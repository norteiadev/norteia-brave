"""DLQ management endpoints (D-21, CORE-07, CORE-08, D-07, D-08, Phase F).

GET  /api/v1/dlq                          — list DLQ records
PATCH /api/v1/dlq/{rio_id}/reprocess      — trigger reprocess
PATCH /api/v1/dlq/{rio_id}/descarte       — steward reject
PATCH /api/v1/dlq/{rio_id}/validate       — steward validate: set validacao_humana=100 → re-score → Mar + push (D-07)
POST  /api/v1/dlq/validate-batch          — batch validate all DLQ records for a UF (D-08)
POST  /api/v1/dlq/whatsapp-batch          — manual DLQ→WhatsApp move for atrativos (Phase F)
"""

import hmac
import uuid

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from brave.api.deps import (
    get_db,
    get_steward_config,
    require_editing_unlocked,
    require_steward_or_bearer,
)
from brave.config.settings import StewardConfig
from brave.core.dlq.service import validate_and_promote_rio
from brave.core.models import RioRecord
from brave.core.repositories import SqlAlchemyDlqRepository
from brave.lanes.atrativos.state_machine import advance_sub_state
from brave.observability.audit import write_audit

router = APIRouter()
logger = structlog.get_logger(__name__)

# Stateless data-access seam (Phase A). The Session is passed per call and the
# endpoint still owns the transaction — this repo only reads.
_dlq_repo = SqlAlchemyDlqRepository()


def require_steward(
    x_steward_secret: str | None = Header(None, alias="X-Steward-Secret"),
    steward_config: StewardConfig = Depends(get_steward_config),
) -> None:
    """Authenticate a steward on the mutating DLQ endpoints (T-02-06-01 / CR-01).

    These endpoints set validacao_humana=100, re-score into Mar, and push to the
    production norteia-api — a write-to-production trust boundary. Mirrors the
    webhook auth pattern: constant-time hmac compare, fail-closed (an unset
    BRAVE_STEWARD_SECRET rejects every caller), 401 before any DB work, secret
    never logged. Phase 4 (DASH-06) supersedes this with dashboard Bearer auth.
    """
    expected = steward_config.secret
    if not x_steward_secret or not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Steward-Secret header required",
        )
    if not hmac.compare_digest(x_steward_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Steward-Secret",
        )


# ---------------------------------------------------------------------------
# Phase F — manual DLQ→WhatsApp move (atrativos): body + eligibility + dispatch
# ---------------------------------------------------------------------------


class WhatsAppBatchBody(BaseModel):
    """Request body for POST /api/v1/dlq/whatsapp-batch.

    rio_ids are atrativo RioRecord UUIDs currently in DLQ (routing="dlq", sub_state
    None) to move into the WhatsApp column. min_length=1 → an empty list is a 422.
    """

    rio_ids: list[uuid.UUID] = Field(
        ...,
        min_length=1,
        description="Atrativo rio_ids currently in DLQ to move to the WhatsApp column.",
    )


def _is_whatsapp_eligible(normalized: dict | None) -> bool:
    """Eligible for the manual WhatsApp move iff the atrativo has NO horário AND NO preço.

    Server-side eligibility gate (Phase F): a WhatsApp owner-consultation only makes
    sense when we are still MISSING opening hours and price. A record that already
    carries either is rejected (422) — there is nothing to ask the owner.

    Horário sources:
      - normalized["weekday_text"]   (list[str]) — Google Places opening hours (SignalAgent)
      - normalized["owner_horarios"] (str)       — owner-confirmed schedule (post-outreach)
    Preço source:
      - normalized["owner_valor"]    (str)       — owner-confirmed price (the only price a
        pre-outreach atrativo can carry; Places exposes no price today)

    normalized may be None → treated as no horário / no preço → eligible.
    """
    n = normalized or {}
    has_horario = bool(n.get("weekday_text")) or bool(n.get("owner_horarios"))
    has_preco = bool(n.get("owner_valor"))
    return not has_horario and not has_preco


def _dispatch_or_inline(task, *args) -> None:
    """Dispatch a Celery task with an inline-.run fallback (dispatch-then-inline-fallback).

    Same idiom used elsewhere (reprocess endpoint above, the atrativos FSM chain):
    .delay() on a live broker; on any dispatch error (no broker in tests/dev, or a
    real broker-down) run the task body inline in-process. Callers commit the record
    BEFORE calling this (WR-01) so the inline .run's own session sees the committed
    state and can re-acquire the released row lock. An inline failure is logged, never
    raised — the record move is already committed and the send/discovery is retryable.
    """
    try:
        task.delay(*args)
    except Exception:
        try:
            task.run(*args)
        except Exception as exc:  # noqa: BLE001 — move committed; work retryable
            logger.warning(
                "dlq_whatsapp_dispatch_inline_failed",
                task=getattr(task, "name", str(task)),
                error=str(exc),
            )


@router.get("/api/v1/dlq")
def list_dlq(
    uf: str | None = Query(None),
    entity_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List DLQ records (routing='dlq').

    Optional filters: uf, entity_type.
    Default limit: 50.
    """
    rows = _dlq_repo.list_dlq(db, uf=uf, entity_type=entity_type, limit=limit)
    return [
        {
            "id": str(r.id),
            "nascente_id": str(r.nascente_id),
            "entity_type": r.entity_type,
            "uf": r.uf,
            "routing": r.routing,
            "dlq_reason": r.dlq_reason,
            "score": float(r.score) if r.score is not None else None,
            "score_version": r.score_version,
            "canonical_key": r.canonical_key,
        }
        for r in rows
    ]


@router.patch(
    "/api/v1/dlq/{rio_id}/reprocess",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def reprocess_dlq_record(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Trigger reprocessing of a DLQ record.

    Dispatches reprocess_record_task via Celery.
    Returns 202 Accepted.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    # Dispatch Celery task
    try:
        from brave.tasks.pipeline import reprocess_record_task

        reprocess_record_task.delay(str(rio_id))
    except Exception:
        # In tests/dev without Celery broker, fall back to synchronous reprocess
        from brave.config.runtime import load_effective_config
        from brave.core.rio.routing import reprocess_record

        reprocess_record(db, rio_id, load_effective_config(db).score)

    write_audit(
        session=db,
        action="dlq_reprocessed",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state={"routing": "dlq", "dlq_reason": rio.dlq_reason},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id)}


@router.patch(
    "/api/v1/dlq/{rio_id}/validate",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def validate_dlq_record(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward validates a DLQ record: sets validacao_humana=100 → re-score → Mar + push (D-07).

    Steps:
    1. Load RioRecord; 404 if missing.
    2. Delegate to validate_and_promote_rio (sets human validation score, re-scores, promotes if mar).
    3. Write audit row with action='dlq_validated', actor='steward'.
    4. WR-01: commit audit + promotion BEFORE dispatch — mirrors cms.py:342. Worker's
       own session must see the committed record.
    5. If routing becomes 'mar': dispatch push_destination_task. Broker-down → 503 (promotion
       already committed, so the 503 tells the steward to retry the push, not re-do the promotion).

    Returns 202 with {status, rio_id, routing}.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "score": float(rio.score or 0)}

    # Delegate to service: flag_modified+flush → reprocess_record → refresh → promote_to_mar if routing=='mar'
    # Service handles Pitfall 3 (reassign+flag_modified) and Pitfall 4 (reprocess_record not process_nascente_record).
    # Does NOT dispatch Celery tasks — that remains the router's responsibility.
    validate_and_promote_rio(db, rio)
    db.refresh(rio)

    write_audit(
        session=db,
        action="dlq_validated",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"routing": rio.routing, "score": float(rio.score or 0)},
        actor="steward",
    )

    # WR-01: commit audit + promotion BEFORE dispatching the Celery push. The worker
    # opens its own session and early-returns when routing != "mar"; dispatching
    # while the request transaction is still open is a read-before-commit race that
    # silently drops the push to norteia-api in production. Guard on the committed
    # routing == "mar". Mirrors cms.py:342.
    db.commit()
    db.refresh(rio)

    # Only dispatch push when routing == 'mar' (service already promoted; push publishes to norteia-api)
    if rio.routing == "mar":
        try:
            from brave.tasks.pipeline import push_destination_task

            push_destination_task.delay(str(rio_id))
        except Exception as exc:
            # The promotion is already committed (WR-01 above). A broker-down push
            # cannot roll back the Mar record. Under run_real_externals, surface it
            # (log + 503) so the steward knows to retry the dispatch. The retry is safe
            # — validate_and_promote_rio is idempotent (flag_modified re-scores already
            # Mar records). Offline (tests/dev), no broker is expected; push is a no-op.
            from brave.config.settings import AppConfig

            if AppConfig().run_real_externals:
                logger.error(
                    "dlq_push_dispatch_failed",
                    rio_id=str(rio_id),
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Mar push dispatch failed (broker unavailable). "
                        "Promotion is committed — retry once the broker is reachable."
                    ),
                ) from exc

    return {"status": "accepted", "rio_id": str(rio_id), "routing": rio.routing}


@router.post(
    "/api/v1/dlq/validate-batch",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def validate_batch(
    uf: str = Query(..., description="Two-letter UF code (e.g. 'BA')"),
    entity_type: str = Query("destination"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """Batch validate all DLQ records for a UF (D-08, T-02-06-02, T-02-06-03).

    Applies the same validate logic (flag_modified + reprocess_record + push if mar)
    to every DLQ record matching uf + entity_type, up to limit.

    Security: uf is required (no wildcard). limit is capped at 1000 (T-02-06-03).
    Writes individual audit rows per record and one batch summary row after.

    WR-01 per-row: each row's audit + promotion is committed BEFORE its push dispatch.
    A later dispatch failure (503) cannot roll back already-committed rows. Partial
    batch on broker-down is retryable (idempotent validate).

    Returns 202 with {status, uf, validated}.
    """
    rows = _dlq_repo.list_dlq(db, uf=uf, entity_type=entity_type, limit=limit)

    validated = 0
    for rio in rows:
        # Delegate to service: flag_modified+flush → reprocess_record → refresh → promote_to_mar if routing=='mar'
        validate_and_promote_rio(db, rio)
        db.refresh(rio)

        write_audit(
            session=db,
            action="dlq_validated",
            entity_type=rio.entity_type,
            record_id=rio.id,
            before_state={"routing": "dlq", "score": float(rio.score or 0)},
            after_state={"routing": rio.routing, "score": float(rio.score or 0)},
            actor="steward",
        )

        # WR-01 per-row: commit before dispatch; a later dispatch failure cannot roll
        # back this row. Semantics: partial batch on broker-down, retryable (idempotent
        # validate). Mirrors the single-validate WR-01 pattern above.
        db.commit()
        db.refresh(rio)

        if rio.routing == "mar":
            try:
                from brave.tasks.pipeline import push_destination_task

                push_destination_task.delay(str(rio.id))
            except Exception as exc:
                # Per-row commit is already done. Broker-down signals steward to retry
                # this row's push; subsequent rows are left unprocessed (503 exits the
                # loop). Offline it is an expected no-op (never raises under
                # run_real_externals=False).
                from brave.config.settings import AppConfig

                if AppConfig().run_real_externals:
                    logger.error(
                        "dlq_push_dispatch_failed",
                        rio_id=str(rio.id),
                        error=str(exc),
                    )
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "Mar push dispatch failed (broker unavailable). "
                            "Committed rows stay promoted — retry once the broker "
                            "is reachable."
                        ),
                    ) from exc

        validated += 1

    return {"status": "accepted", "uf": uf, "validated": validated}


@router.post(
    "/api/v1/dlq/whatsapp-batch",
    status_code=202,
    dependencies=[
        Depends(require_steward_or_bearer),
        Depends(require_editing_unlocked),
    ],
)
def dlq_to_whatsapp_batch(
    body: WhatsAppBatchBody,
    db: Session = Depends(get_db),
) -> dict:
    """Manually move DLQ atrativos into the WhatsApp column (Phase F — the single entry).

    This REPLACES the old auto-gate approve/reject flow as the operator's way to send an
    atrativo to WhatsApp outreach. It accepts a list of atrativo rio_ids currently in DLQ
    and, for each ELIGIBLE record, moves it to sub_state="aguardando_consulta_whatsapp"
    (off the DLQ column, routing="in_progress") and branches:

      - has a captured celular (normalized["contact"]["whatsapp_candidate"] present) →
        advance aguardando_consulta_whatsapp → whatsapp_in_progress and dispatch
        outreach_task (the existing LangGraph consent/ramp/quality/Twilio path).
      - no celular → dispatch discover_whatsapp_number_task (LLM number-discovery): on a
        found number it proceeds to outreach; offline / not-found it bounces the record
        back to DLQ with dlq_reason="no_contact_found".

    ELIGIBILITY (server-side, 422): only atrativos with NO horário AND NO preço qualify —
    a record that already has weekday_text / owner_horarios / owner_valor has nothing to
    ask the owner. The batch is ATOMIC: if ANY id is ineligible or invalid (not found /
    not an attraction / not in DLQ / already in WhatsApp), the whole request returns 422
    with a per-item breakdown and NOTHING is moved.

    Auth: require_steward_or_bearer (mutation) THEN require_editing_unlocked (Motor
    Pausado edit-lock, Phase C) — auth-before-lock, so an unauthenticated caller gets 401,
    never a 423 that would leak lock state. While the engine is LIGADO this returns 423.

    WR-01: each eligible record's move + audit is committed BEFORE its Celery dispatch, so
    a broker-down / inline fallback sees the committed state and the released row lock. The
    dispatch uses the dispatch-then-inline-fallback idiom.

    Returns 202 with {status, moved, outreach, discovery}.
    """
    # Pass 1 — validate every id up front (no mutation). Atomic: any failure → 422.
    to_move_ids: list[uuid.UUID] = []
    ineligible: list[dict] = []
    for rid in body.rio_ids:
        rio = db.get(RioRecord, rid)
        reason: str | None = None
        if rio is None:
            reason = "not_found"
        elif rio.entity_type != "attraction":
            reason = "not_attraction"
        elif rio.routing != "dlq":
            reason = "not_in_dlq"
        elif rio.sub_state is not None:
            reason = "already_in_whatsapp"
        elif not _is_whatsapp_eligible(rio.normalized):
            reason = "has_horario_or_preco"

        if reason is not None:
            ineligible.append({"rio_id": str(rid), "reason": reason})
        else:
            to_move_ids.append(rid)

    if ineligible:
        # Atomic — one bad id fails the whole batch; nothing has been mutated yet.
        raise HTTPException(
            status_code=422,
            detail={"error": "ineligible_records", "ineligible": ineligible},
        )

    # Pass 2 — move each eligible record under a fresh row lock, commit, then dispatch.
    outreach = 0
    discovery = 0
    for rid in to_move_ids:
        # CR-04: re-lock per record (the first commit below releases prior locks).
        rio = db.get(RioRecord, rid, with_for_update=True)
        if rio is None or rio.routing != "dlq" or rio.sub_state is not None:
            # Raced away between validation and move — skip defensively (no partial move).
            continue

        # dlq → aguardando_consulta_whatsapp (audited FSM edge; routing set separately).
        advance_sub_state(
            db, rio, None, "aguardando_consulta_whatsapp",
            actor="steward", validate=True, lock=False,
        )
        # Move OFF the DLQ column while the WhatsApp consultation is in flight.
        rio.routing = "in_progress"

        candidate = ((rio.normalized or {}).get("contact") or {}).get("whatsapp_candidate")
        branch = "outreach" if candidate else "discovery"

        if candidate:
            # Have a WhatsApp number → approve for outreach immediately.
            advance_sub_state(
                db, rio, "aguardando_consulta_whatsapp", "whatsapp_in_progress",
                actor="steward", validate=True, lock=False,
            )

        write_audit(
            session=db,
            action="dlq_to_whatsapp",
            entity_type=rio.entity_type,
            record_id=rio.id,
            before_state={"routing": "dlq", "sub_state": None},
            after_state={"routing": rio.routing, "sub_state": rio.sub_state, "branch": branch},
            actor="steward",
        )

        # WR-01: commit the move BEFORE dispatch (releases the row lock; the worker /
        # inline .run sees the committed state).
        db.commit()

        if candidate:
            from brave.tasks.pipeline import outreach_task

            _dispatch_or_inline(outreach_task, str(rid))
            outreach += 1
        else:
            from brave.tasks.pipeline import discover_whatsapp_number_task

            _dispatch_or_inline(discover_whatsapp_number_task, str(rid))
            discovery += 1

    return {
        "status": "accepted",
        "moved": outreach + discovery,
        "outreach": outreach,
        "discovery": discovery,
    }


@router.patch(
    "/api/v1/dlq/{rio_id}/descarte",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
def descarte_dlq_record(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward rejects a DLQ record (routing → descarte).

    Sets routing='descarte' with dlq_reason='steward_rejected'.
    Writes audit log.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "dlq_reason": rio.dlq_reason}
    rio.routing = "descarte"
    rio.dlq_reason = "steward_rejected"

    write_audit(
        session=db,
        action="dlq_rejected",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"routing": "descarte", "dlq_reason": "steward_rejected"},
        actor="steward",
    )
    return {"status": "ok", "routing": "descarte", "rio_id": str(rio_id)}
