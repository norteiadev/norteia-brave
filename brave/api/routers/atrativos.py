"""Atrativos operator API — TripAdvisor mar-ready promote-override endpoints (plan 11-03, TA-05/TA-06).

Endpoints:
  GET  /api/v1/atrativos/mar-ready          — list TripAdvisor attractions ready for steward override
  PATCH /api/v1/atrativos/{rio_id}/promote  — steward promote-override single record
  POST  /api/v1/atrativos/promote-batch     — batch promote-override for a UF

Security (T-11-03-01, T-11-03-02):
  - GET /mar-ready: require_bearer (read-only)
  - PATCH + POST: require_steward_or_bearer (mutation)
  - promote_override raises PromoteNotAllowed → 409 for non-mar_ready records;
    this guard is evaluated BEFORE any DB mutation, regardless of caller identity.

Mirrors brave/api/routers/dlq.py exactly for the promote-single and promote-batch
endpoints (same broker-down 503 contract, same audit pattern, same 404/202 shape).
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, require_bearer, require_steward_or_bearer
from brave.api.routers.cms import _ROUTING_TO_COLUMN, TransitionBody
from brave.core.models import RioRecord
from brave.core.promote.service import PromoteNotAllowed, promote_override
from brave.observability.audit import write_audit

router = APIRouter()
logger = structlog.get_logger(__name__)


# Server-side ATRATIVO edge allow-list — the atrativo twin of the client mapDrop
# (keyed by (expected_column, to_column) → handler tag). Mirrors the destino
# _ALLOWED_EDGES posture: any (expected, to) pair absent from it returns 409 and
# NEVER mutates; every ("mar", *) edge is absent (T-17.1-03-03). The into-whatsapp
# edge is sub_state-guarded and delegates to the audited gate approve — it never
# duplicates the outreach dispatch.
_ATRATIVO_ALLOWED_EDGES: dict[tuple[str, str], str] = {
    ("rio", "dlq"): "send_to_review",      # force send-to-review
    ("dlq", "rio"): "reprocess",           # reopen / reprocess (NEW backward edge)
    ("rio", "mar"): "promote_override",    # mar-ready promote-override
    ("rio", "descarte"): "descarte",       # descarte
    ("whatsapp", "whatsapp"): "gate_approve",  # delegate to atrativos_gate approve
}


def push_attraction_task_delay(rio_id: str) -> None:
    """Dispatch push_attraction_task.delay. Isolated function for easy test patching."""
    from brave.tasks.pipeline import push_attraction_task  # lazy — no Celery import at startup

    push_attraction_task.delay(rio_id)


@router.get("/api/v1/atrativos/mar-ready", dependencies=[Depends(require_bearer)])
def list_mar_ready(
    uf: str | None = Query(None, description="Optional two-letter UF filter"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List TripAdvisor attractions with mar_ready=True and routing='dlq'.

    These are the records eligible for steward promote-override. Filtered to
    TripAdvisor attractions only (canonical_key starts with 'tripadvisor:').

    Optional filter: uf (two-letter UF code).
    Default limit: 100.
    """
    query = (
        select(RioRecord)
        .where(
            RioRecord.mar_ready == True,  # noqa: E712 — SQLAlchemy filter
            RioRecord.routing == "dlq",
            RioRecord.canonical_key.like("tripadvisor:%"),
        )
    )
    if uf:
        query = query.where(RioRecord.uf == uf)
    query = query.limit(limit)

    rows = list(db.scalars(query).all())
    return [
        {
            "id": str(r.id),
            "canonical_key": r.canonical_key,
            "uf": r.uf,
            "score": float(r.score) if r.score is not None else None,
            "entity_type": r.entity_type,
            "mar_ready": r.mar_ready,
        }
        for r in rows
    ]


@router.patch(
    "/api/v1/atrativos/{rio_id}/promote",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def promote_atrativo(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward promote-override for a single TripAdvisor attraction (TA-05).

    Bypasses the ≥85 score gate for records that are mar_ready=True (set by
    route_by_score for TA attractions with sufficient atualidade + corroboracao).

    Steps:
    1. Load RioRecord; 404 if missing.
    2. Call promote_override → PromoteNotAllowed (409) if not mar_ready.
    3. Dispatch push_attraction_task (broker-down → 503 if run_real_externals).
    4. Write audit row with action='atrativo_promoted_override', actor='steward'.

    Returns 202 with {status, rio_id, routing}.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "score": float(rio.score or 0), "mar_ready": rio.mar_ready}

    try:
        promote_override(db, rio, reason="steward_override_review_validated")
    except PromoteNotAllowed as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Record is not mar_ready — promote-override not allowed: {exc}",
        ) from exc

    db.refresh(rio)

    try:
        push_attraction_task_delay(str(rio_id))
    except Exception as exc:
        # Broker-down must not silently leave a Mar record unpublished.
        # Under run_real_externals, surface it (log + 503) so the whole
        # promote rolls back — the steward retries once the broker is reachable.
        # Offline (tests/dev), no broker is expected; promote_override already
        # committed the Mar record, so the missing push is an expected no-op.
        from brave.config.settings import AppConfig

        if AppConfig().run_real_externals:
            logger.error(
                "atrativo_promote_dispatch_failed",
                rio_id=str(rio_id),
                error=str(exc),
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Promote not committed — Mar push dispatch failed "
                    "(broker unavailable). Retry once the broker is reachable."
                ),
            ) from exc

    write_audit(
        session=db,
        action="atrativo_promoted_override",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"routing": rio.routing, "score": float(rio.score or 0)},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id), "routing": rio.routing}


@router.post(
    "/api/v1/atrativos/promote-batch",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def promote_batch(
    uf: str = Query(..., description="Two-letter UF code (e.g. 'BA')"),
    source: str = Query("tripadvisor"),
    limit: int = Query(100, ge=1, le=1000, description="Max records per batch (T-11-03-04)"),
    db: Session = Depends(get_db),
) -> dict:
    """Batch promote-override all mar_ready attractions for a UF (TA-06).

    Applies the same promote-override logic to every mar_ready=True + routing='dlq'
    TripAdvisor attraction in the requested UF, up to limit.

    Security (T-11-03-04): uf is required (no wildcard). limit is capped at 1000.
    Writes individual audit rows per record.

    Returns 202 with {status, uf, promoted}.
    """
    rows = list(
        db.scalars(
            select(RioRecord)
            .where(
                RioRecord.mar_ready == True,  # noqa: E712
                RioRecord.routing == "dlq",
                RioRecord.canonical_key.like("tripadvisor:%"),
                RioRecord.uf == uf,
            )
            .limit(limit)
        ).all()
    )

    promoted = 0
    for rio in rows:
        before_state = {"routing": rio.routing, "score": float(rio.score or 0)}

        try:
            promote_override(db, rio, reason="steward_override_review_validated")
        except PromoteNotAllowed:
            # Should not happen (we filtered by mar_ready=True), but guard defensively.
            continue

        db.refresh(rio)

        try:
            push_attraction_task_delay(str(rio.id))
        except Exception as exc:
            from brave.config.settings import AppConfig

            if AppConfig().run_real_externals:
                logger.error(
                    "atrativo_promote_dispatch_failed",
                    rio_id=str(rio.id),
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Batch promote not committed — Mar push dispatch "
                        "failed (broker unavailable). Retry once the broker "
                        "is reachable."
                    ),
                ) from exc

        write_audit(
            session=db,
            action="atrativo_promoted_override",
            entity_type=rio.entity_type,
            record_id=rio.id,
            before_state=before_state,
            after_state={"routing": rio.routing, "score": float(rio.score or 0)},
            actor="steward",
        )
        promoted += 1

    return {"status": "accepted", "uf": uf, "promoted": promoted}


@router.patch(
    "/api/v1/atrativos/{rio_id}/transition",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
def transition_atrativo(
    rio_id: uuid.UUID,
    body: TransitionBody,
    db: Session = Depends(get_db),
) -> dict:
    """Generic, audited stage transition for an atrativo (UI-PAINEL-2).

    Server-side `_ATRATIVO_ALLOWED_EDGES` is the security boundary (the atrativo
    twin of the client mapDrop): only allow-listed (expected, to) edges mutate;
    everything else — notably every mar → * edge — returns 409 and never touches
    the record. The into-whatsapp edge is sub_state-guarded (only from
    `aguardando_consulta_whatsapp`) and DELEGATES to the audited gate approve —
    it never duplicates the outreach dispatch. All other edges reuse an existing
    helper (no new pipeline logic), write one audit row, then commit.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    edge = _ATRATIVO_ALLOWED_EDGES.get((body.expected, body.to))
    if edge is None:
        # Unmapped (incl. every mar → *) — reject BEFORE any mutation.
        raise HTTPException(status_code=409, detail="transição não suportada")

    if edge == "gate_approve":
        # Into-whatsapp is sub_state-guarded (NOT routing-guarded). Only valid from
        # aguardando_consulta_whatsapp; delegate to the audited gate approve, which
        # owns the outreach dispatch + audit + commit. Never duplicate it here.
        if rio.sub_state != "aguardando_consulta_whatsapp":
            raise HTTPException(
                status_code=409,
                detail=(
                    "into-whatsapp válido apenas a partir de "
                    "sub_state 'aguardando_consulta_whatsapp'"
                ),
            )
        from brave.api.routers.atrativos_gate import approve_whatsapp_gate

        return approve_whatsapp_gate(rio_id, db)

    # Optimistic concurrency: the caller's `expected` column must still be current.
    current_column = _ROUTING_TO_COLUMN.get(rio.routing, rio.routing)
    if current_column != body.expected:
        raise HTTPException(
            status_code=409,
            detail=f"coluna atual é '{current_column}', esperado '{body.expected}'",
        )

    before_state = {"column": body.expected, "routing": rio.routing, "sub_state": rio.sub_state}

    if edge == "promote_override":
        try:
            promote_override(db, rio, reason="steward_override_review_validated")
        except PromoteNotAllowed as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Record is not mar_ready — promote-override not allowed: {exc}",
            ) from exc
        db.refresh(rio)
    elif edge == "descarte":
        rio.routing = "descarte"
        rio.dlq_reason = "steward_rejected"
        rio.sub_state = None
    elif edge == "send_to_review":
        rio.routing = "dlq"
        rio.dlq_reason = "steward_sent_to_review"
        rio.sub_state = None
    elif edge == "reprocess":
        # Reopen: reset → re-score (§7.6). Reuses the routing helper, no new machinery.
        from brave.config.settings import ScoreConfig
        from brave.core.rio.routing import reprocess_record

        reprocess_record(db, rio.id, ScoreConfig())
        db.refresh(rio)

    write_audit(
        session=db,
        action=f"transition_{body.to}",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"column": body.to, "routing": rio.routing},
        actor="steward",
    )
    db.commit()
    return {"status": "ok", "to": body.to}
