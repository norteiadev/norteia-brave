"""CMS CRUD endpoints for Destinos and Atrativos (D-03, D-04).

Gives operators a read/write API surface over all pipeline records:
  - Destinos across all routings (Mar + DLQ + descarte)
  - Atrativos across all FSM sub_states

No new pipeline logic — routing to existing building blocks:
  validate_and_promote_rio, advance_sub_state, reprocess_record_task, mask_phone.

Not registered in main.py yet — plan 08-04 handles registration (wave isolation).

Security (T-08-01..05):
  - All read endpoints: require_bearer (401 before any DB work)
  - All mutation endpoints: require_steward_or_bearer
  - phone_e164 never returned: _safe_normalized on every atrativo response path
  - /edit body filters phone_e164 before merging into normalized
"""

import uuid
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.api.deps import (
    get_db,
    require_bearer,
    require_editing_unlocked,
    require_steward_or_bearer,
)
from brave.core.models import AuditLog, MarRecord, NascenteRecord, RioRecord, mask_phone
from brave.observability.audit import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request body models
# ---------------------------------------------------------------------------


class AdvanceBody(BaseModel):
    """Body for PATCH /api/v1/atrativos/{rio_id}/advance."""

    expected_state: str
    next_state: str


class EditBody(BaseModel):
    """Body for PATCH /api/v1/destinos/{rio_id}/edit and /api/v1/atrativos/{rio_id}/edit."""

    fields: dict[str, Any]


class TransitionBody(BaseModel):
    """Body for the generic per-entity stage-transition endpoint (UI-PAINEL-2).

    `to` is the target board column; `expected` is the caller's view of the
    record's CURRENT column — an optimistic-concurrency guard (a stale `expected`
    yields 409 rather than silently mutating). `extra="forbid"` rejects any
    field a client invents.
    """

    model_config = ConfigDict(extra="forbid")

    to: Literal["nascente", "rio", "whatsapp", "mar", "dlq", "descarte"]
    expected: str


# ---------------------------------------------------------------------------
# Stage-transition allow-list (the server twin of the client mapDrop)
# ---------------------------------------------------------------------------

# Server-side DESTINO edge allow-list — keyed by (expected_column, to_column) →
# handler tag. This dict IS the security boundary (T-17.1-03-01): any (expected,
# to) pair absent from it returns 409 "transição não suportada" and NEVER mutates
# the record. Every ("mar", *) edge is deliberately absent so a live Mar destino
# can never be depublished/moved (T-17.1-03-03; the cms.py descarte_destino Mar
# guard stays intact — no new depublish path). Must agree edge-for-edge with the
# client mapDrop (the documented paired contract).
_ALLOWED_EDGES: dict[tuple[str, str], str] = {
    ("rio", "mar"): "promote",
    ("rio", "descarte"): "descarte",
    ("rio", "dlq"): "send_to_review",
    ("dlq", "rio"): "reprocess",
    ("dlq", "mar"): "promote",
    ("dlq", "descarte"): "descarte",
}

# Routing value → board column name. The optimistic-concurrency reference: a
# record's CURRENT column is derived from its routing so a stale `expected`
# (e.g. a record already moved out from under the operator) is rejected with 409.
_ROUTING_TO_COLUMN: dict[str, str] = {
    "in_progress": "rio",
    "mar": "mar",
    "dlq": "dlq",
    "descarte": "descarte",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_contacts(contacts: dict | None) -> dict | None:
    """Return an allow-listed, PII-minimized contacts summary (T-08-04, LGPD R3).

    The Rio normalized payload stores the owner's raw contact data under
    normalized["contacts"] via the ContactResult schema: phone_e164, website,
    ig_handle, email. Of these, phone_e164, ig_handle, and email are personal
    contact data (PII) of the owner/responsável and MUST NOT be returned to the
    dashboard verbatim under the data-minimization contract (R3).

    This builds an explicit allow-list rather than returning the raw dict:
      - website        → passed through (public, non-PII)
      - phone_e164     → replaced with phone_masked (mask_phone)
      - email/ig_handle → DROPPED entirely (no masked surrogate is needed by the UI)

    Any other unexpected contact fields are dropped (deny-by-default). Returns
    None when there are no contacts so callers can omit the field cleanly.
    """
    if not isinstance(contacts, dict):
        return None
    out: dict = {}
    if contacts.get("website") is not None:
        out["website"] = contacts.get("website")
    if "phone_e164" in contacts:
        out["phone_masked"] = mask_phone(contacts.get("phone_e164"))
    return out or None


def _safe_contact(contact: dict | None) -> dict | None:
    """Return an allow-listed, MASKED view of normalized["contact"] (Phase F, LGPD R3).

    The Phase F enrichment stores a WhatsApp candidate at
    normalized["contact"]["whatsapp_candidate"] ALREADY masked (mask_phone). This is a
    deny-by-default allow-list: only whatsapp_candidate is surfaced and it is passed
    through mask_phone again as a defense-in-depth guarantee (mask_phone is idempotent
    on the masked form) so a raw celular can NEVER transit the board even if some
    future writer bypasses the write-time masking. Any other keys are dropped.
    """
    if not isinstance(contact, dict):
        return None
    out: dict = {}
    if contact.get("whatsapp_candidate") is not None:
        out["whatsapp_candidate"] = mask_phone(contact.get("whatsapp_candidate"))
    return out or None


def _safe_normalized(normalized: dict | None) -> dict:
    """Return a copy of the Rio normalized dict with the contacts sub-dict minimized.

    The Rio normalized payload stores the owner's raw contact data at
    normalized["contacts"] and a Phase F MASKED WhatsApp candidate at
    normalized["contact"]["whatsapp_candidate"]. This function MUST be called on every
    atrativo response path — owner PII (phone_e164, email, ig_handle) must never be
    returned to the caller. Delegates to _safe_contacts / _safe_contact for the
    allow-listed summaries.

    Mirrors atrativos_gate.py masking contract (phone masked) and additionally
    drops non-website contact PII (email, ig_handle) per CR-01.
    """
    n = dict(normalized or {})
    contacts = n.get("contacts")
    if isinstance(contacts, dict):
        safe = _safe_contacts(contacts)
        if safe is None:
            n.pop("contacts", None)
        else:
            n["contacts"] = safe
    # Phase F: minimize the singular "contact" sub-dict (WhatsApp candidate) too.
    contact = n.get("contact")
    if "contact" in n:
        safe_contact = _safe_contact(contact) if isinstance(contact, dict) else None
        if safe_contact is None:
            n.pop("contact", None)
        else:
            n["contact"] = safe_contact
    return n


# ===========================================================================
# DESTINOS SECTION — 6 endpoints (D-03)
# ===========================================================================


@router.get("/api/v1/destinos", dependencies=[Depends(require_bearer)])
def list_destinos(
    uf: str | None = Query(None),
    routing: str | None = Query(None),
    score_band: str | None = Query(None, description="mar | dlq | descarte"),
    q: str | None = Query(None, description="Free-text match on canonical_key (ilike)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """List destinos across all routings (D-03, T-08-01).

    LEFT JOIN MarRecord so both promoted and unpromoted destinos are returned.
    Supports filtering by uf, routing, score_band, and free-text q.

    Returns paginated response: {items, total, offset, limit}.
    """
    stmt = (
        select(RioRecord, MarRecord)
        .outerjoin(MarRecord, MarRecord.rio_id == RioRecord.id)
        .where(RioRecord.entity_type == "destination")
    )

    if uf:
        stmt = stmt.where(RioRecord.uf == uf)
    if routing:
        stmt = stmt.where(RioRecord.routing == routing)
    if score_band:
        # score_band maps to routing for destinos
        stmt = stmt.where(RioRecord.routing == score_band)
    if q:
        stmt = stmt.where(RioRecord.canonical_key.ilike(f"%{q}%"))

    # Count total before paging (dashboard.py pattern)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.offset(offset).limit(limit)).all()

    items = [
        {
            "id": str(rio.id),
            "entity_type": rio.entity_type,
            "uf": rio.uf,
            "routing": rio.routing,
            "score": float(rio.score) if rio.score is not None else None,
            "canonical_key": rio.canonical_key,
            "name": (rio.normalized or {}).get("name") or (
                (mar.canonical or {}).get("name") if mar else None
            ),
            "validation_pending": rio.routing == "dlq",
            "mar_id": str(mar.id) if mar else None,
            "published_at": mar.published_at.isoformat() if mar and mar.published_at else None,
        }
        for rio, mar in rows
    ]

    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/api/v1/destinos/{rio_id}", dependencies=[Depends(require_bearer)])
def get_destino_detail(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Return the full destino detail (D-03, T-08-01).

    Surfaces score_breakdown, normalized, audit_log journey, and
    child_atrativos count (by sub_state, grouped).

    Read-only: Bearer-guarded, 404 on unknown rio_id.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="RioRecord not found"
        )

    nascente = db.get(NascenteRecord, rio.nascente_id) if rio.nascente_id else None
    mar = db.scalar(
        select(MarRecord).where(MarRecord.rio_id == rio.id)
    )

    audit_rows = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.record_id == rio.id)
            .order_by(AuditLog.created_at.asc())
        ).all()
    )

    # Child atrativos summary — only available when destino is promoted to Mar
    child_atrativos: dict = {"total": 0, "by_sub_state": {}}
    if mar:
        mar_id_str = str(mar.id)
        # Count total atrativos referencing this Mar parent
        total_child = db.scalar(
            select(func.count(RioRecord.id)).where(
                RioRecord.entity_type == "attraction",
                RioRecord.normalized["parent_mar_id"].as_string() == mar_id_str,
            )
        ) or 0

        # Distribution by sub_state
        sub_state_rows = db.execute(
            select(RioRecord.sub_state, func.count(RioRecord.id))
            .where(
                RioRecord.entity_type == "attraction",
                RioRecord.normalized["parent_mar_id"].as_string() == mar_id_str,
            )
            .group_by(RioRecord.sub_state)
        ).all()

        by_sub_state = {
            (ss or "none"): count for ss, count in sub_state_rows
        }
        child_atrativos = {"total": total_child, "by_sub_state": by_sub_state}

    return {
        "id": str(rio.id),
        "entity_type": rio.entity_type,
        "uf": rio.uf,
        "routing": rio.routing,
        "score": float(rio.score) if rio.score is not None else None,
        "score_breakdown": rio.score_breakdown or {},
        "canonical_key": rio.canonical_key,
        "normalized": rio.normalized or {},
        "source": nascente.source if nascente else None,
        "audit_log": [
            {
                "action": row.action,
                "actor": row.actor,
                "after_state": row.after_state,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in audit_rows
        ],
        "child_atrativos": child_atrativos,
    }


@router.patch(
    "/api/v1/destinos/{rio_id}/promote",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def promote_destino(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward promotes a destino: validate_and_promote_rio → Mar + push (D-03, T-08-02).

    Delegates to validate_and_promote_rio (sets human validation score, re-scores,
    promotes if Mar-eligible). Dispatches push_destination_task on Celery if routing
    reaches 'mar'. Returns 202 Accepted.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "score": float(rio.score or 0)}

    # Lazy import: avoids circular at module load, matches dlq.py pattern
    from brave.core.dlq.service import validate_and_promote_rio

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

    # WR-01: commit + refresh BEFORE dispatching the Celery push. The worker
    # opens its own session and early-returns when routing != "mar"; dispatching
    # while the request transaction is still open is a read-before-commit race
    # that silently drops the push to norteia-api in production. Guard on the
    # committed routing == "mar".
    db.commit()
    db.refresh(rio)

    if rio.routing == "mar":
        try:
            from brave.tasks.pipeline import push_destination_task

            push_destination_task.delay(str(rio_id))
        except Exception as exc:
            # The promotion is already committed (WR-01 above), so a broker-down
            # push cannot roll back — the record IS in Mar but unpublished. Under
            # run_real_externals, surface it (log + 503) so the steward knows the
            # downstream publish failed and can retry the promotion to re-dispatch.
            # Offline (tests/dev), no broker is expected and the push is a no-op.
            from brave.config.settings import AppConfig

            if AppConfig().run_real_externals:
                logger.error(
                    "cms_push_dispatch_failed",
                    rio_id=str(rio_id),
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Destino promoted to Mar but downstream publish failed "
                        "(broker unavailable). Retry the promotion once the broker "
                        "is reachable to re-dispatch the push."
                    ),
                ) from exc

    return {"status": "accepted", "rio_id": str(rio_id), "routing": rio.routing}


@router.patch(
    "/api/v1/destinos/{rio_id}/descarte",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
def descarte_destino(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward rejects a destino (routing → descarte) (D-03, T-08-02).

    Sets routing='descarte', dlq_reason='steward_rejected'. Writes audit log.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    # WR-05: refuse to descartar a destino that already reached Mar. Plain
    # descarte only flips routing on the Rio — it does NOT remove/depublish the
    # canonical MarRecord nor notify norteia-api, so the record would stay live
    # downstream while the CMS shows routing="descarte" (violates the Mar
    # trust invariant). Block with 409; a dedicated retract/depublish flow is
    # required for already-promoted records (out of scope here).
    existing_mar = db.scalar(select(MarRecord).where(MarRecord.rio_id == rio.id))
    if existing_mar is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Destino já promovido ao Mar (MarRecord existe) não pode ser "
                "descartado diretamente — use um fluxo de retract/depublicação "
                "que remova o registro canônico e notifique a norteia-api."
            ),
        )

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
    db.commit()
    return {"status": "ok", "routing": "descarte", "rio_id": str(rio_id)}


@router.patch(
    "/api/v1/destinos/{rio_id}/transition",
    status_code=200,
    dependencies=[
        Depends(require_steward_or_bearer),
        Depends(require_editing_unlocked),
    ],
)
def transition_destino(
    rio_id: uuid.UUID,
    body: TransitionBody,
    db: Session = Depends(get_db),
) -> dict:
    """Generic, audited stage transition for a destino (UI-PAINEL-2).

    Server-side `_ALLOWED_EDGES` is the security boundary (the twin of the client
    mapDrop): only (expected, to) edges present in the table mutate; everything
    else — notably every mar → * edge — returns 409 and never touches the record.
    Optimistic concurrency: `body.expected` must still match the record's current
    column. Each performed edge REUSES an existing helper (no new pipeline logic),
    writes one audit row (action=`transition_<to>`), then commits.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    edge = _ALLOWED_EDGES.get((body.expected, body.to))
    if edge is None:
        # Unmapped (incl. every mar → *) — reject BEFORE any mutation (T-17.1-03-01/03).
        raise HTTPException(status_code=409, detail="transição não suportada")

    # Optimistic concurrency: the caller's `expected` column must still be current.
    current_column = _ROUTING_TO_COLUMN.get(rio.routing, rio.routing)
    if current_column != body.expected:
        raise HTTPException(
            status_code=409,
            detail=f"coluna atual é '{current_column}', esperado '{body.expected}'",
        )

    before_state = {"column": body.expected, "routing": rio.routing}

    if edge == "promote":
        # Reuse the DLQ validate-and-promote helper (validacao_humana=100 →
        # re-score → promote_to_mar). No new depublish/retract path is added.
        from brave.core.dlq.service import validate_and_promote_rio

        validate_and_promote_rio(db, rio)
        db.refresh(rio)
    elif edge == "descarte":
        rio.routing = "descarte"
        rio.dlq_reason = "steward_rejected"
    elif edge == "send_to_review":
        # Force a record back into the DLQ review column.
        rio.routing = "dlq"
        rio.dlq_reason = "steward_sent_to_review"
    elif edge == "reprocess":
        # Reopen: reset → re-score (§7.6). Reuses the routing helper, no new machinery.
        from brave.config.runtime import load_effective_config
        from brave.core.rio.routing import reprocess_record

        reprocess_record(db, rio.id, load_effective_config(db).score)
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


@router.patch(
    "/api/v1/destinos/{rio_id}/reprocess",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def reprocess_destino(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Trigger reprocessing of a destino (D-03, T-08-02).

    Dispatches reprocess_record_task via Celery; falls back to synchronous
    reprocess_record if broker is unavailable (offline tests/dev).

    Returns 202 Accepted.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "dlq_reason": rio.dlq_reason}

    # WR-07: narrow the fallback to broker-connection errors only. A bare
    # `except Exception` swallowed import/runtime errors as if the broker were
    # absent, and the audit row was written unconditionally — asserting a
    # reprocess that may never have happened. Only fall back to the synchronous
    # path on broker-connection failures, and only write the audit row AFTER a
    # path is confirmed dispatched/executed (annotated with the dispatch mode).
    from kombu.exceptions import OperationalError as KombuOperationalError

    try:
        from brave.tasks.pipeline import reprocess_record_task

        reprocess_record_task.delay(str(rio_id))
        dispatch_mode = "celery"
    except (KombuOperationalError, ConnectionError, OSError):
        # Broker unreachable (offline tests/dev) → run synchronously.
        from brave.config.runtime import load_effective_config
        from brave.core.rio.routing import reprocess_record

        reprocess_record(db, rio_id, load_effective_config(db).score)
        dispatch_mode = "synchronous"

    write_audit(
        session=db,
        action="dlq_reprocessed",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"dispatch_mode": dispatch_mode},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id), "dispatch_mode": dispatch_mode}


@router.patch(
    "/api/v1/destinos/{rio_id}/edit",
    status_code=200,
    dependencies=[
        Depends(require_steward_or_bearer),
        Depends(require_editing_unlocked),
    ],
)
def edit_destino(
    rio_id: uuid.UUID,
    body: EditBody,
    db: Session = Depends(get_db),
) -> dict:
    """Steward edits canonical fields on a destino normalized payload (D-03, T-08-05).

    Merges body.fields into rio.normalized. Uses flag_modified to ensure SQLAlchemy
    detects the JSON mutation (Pitfall 3 — never mutate in-place without flag_modified).

    Returns 200 OK.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"normalized_keys": list((rio.normalized or {}).keys())}

    # Pitfall 3: reassign + flag_modified; never mutate in-place
    normalized = dict(rio.normalized or {})
    normalized.update(body.fields)
    rio.normalized = normalized
    flag_modified(rio, "normalized")

    write_audit(
        session=db,
        action="cms_edited",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"edited_keys": list(body.fields.keys())},
        actor="steward",
    )
    db.commit()
    return {"status": "ok"}


# ===========================================================================
# ATRATIVOS SECTION — 5 endpoints (D-04)
# ===========================================================================


@router.get("/api/v1/atrativos", dependencies=[Depends(require_bearer)])
def list_atrativos(
    uf: str | None = Query(None),
    sub_state: str | None = Query(None),
    parent_mar_id: str | None = Query(None),
    routing: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """List atrativos across all FSM sub_states (D-04, T-08-01, T-08-04).

    Filters: uf, sub_state, parent_mar_id (JSON subscript on normalized), routing.
    parent_mar_id uses as_string() JSON subscript (NOT JSONB @> operator — Pitfall 2).

    LGPD: _safe_normalized applied on every atrativo in the response. phone_e164
    never returned — only phone_masked (T-08-04).

    Returns paginated response: {items, total, offset, limit}.
    """
    stmt = select(RioRecord).where(RioRecord.entity_type == "attraction")

    if uf:
        stmt = stmt.where(RioRecord.uf == uf)
    if sub_state:
        stmt = stmt.where(RioRecord.sub_state == sub_state)
    if parent_mar_id:
        # JSON subscript — emits normalized->>'parent_mar_id' in PG
        # NOT @> JSONB operator (Pitfall 2 — works for both JSON and JSONB columns)
        stmt = stmt.where(
            RioRecord.normalized["parent_mar_id"].as_string() == str(parent_mar_id)
        )
    if routing:
        stmt = stmt.where(RioRecord.routing == routing)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.offset(offset).limit(limit)).all())

    # Phase F/H: WhatsApp eligibility (no horário AND no preço) — lets the Kanban
    # disable the manual DLQ→WhatsApp move for ineligible cards. Server-side batch
    # move (dlq.py) enforces it regardless; this projection just powers the UI.
    from brave.api.routers.dlq import _is_whatsapp_eligible  # noqa: PLC0415

    items = [
        {
            "id": str(rio.id),
            "entity_type": rio.entity_type,
            "uf": rio.uf,
            "routing": rio.routing,
            "sub_state": rio.sub_state,
            "score": float(rio.score) if rio.score is not None else None,
            "name": (rio.normalized or {}).get("name"),
            "validation_pending": rio.sub_state == "aguardando_consulta_whatsapp",
            "whatsapp_eligible": _is_whatsapp_eligible(rio.normalized),
            "mar_id": None,  # atrativos don't have direct mar_id in normalized
            # Público-geo município (nome) resolved at ingest — NOT PII (same class as uf).
            "municipio": (rio.normalized or {}).get("municipio"),
            "municipio_id": (rio.normalized or {}).get("municipio_id"),
            "parent_mar_id": (rio.normalized or {}).get("parent_mar_id"),
            # T-08-04 / CR-01: never expose raw contacts — allow-listed summary
            # (website + phone_masked only; email/ig_handle dropped as owner PII)
            "contacts_summary": _safe_contacts((rio.normalized or {}).get("contacts")),
        }
        for rio in rows
    ]

    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/api/v1/atrativos/{rio_id}", dependencies=[Depends(require_bearer)])
def get_atrativo_detail(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Return the full atrativo detail (D-04, T-08-01, T-08-04).

    Surfaces FSM audit trail, score_breakdown, contacts with phone_masked,
    and parent destino link.

    LGPD: _safe_normalized applied to normalized field. phone_e164 never returned.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="RioRecord not found"
        )

    audit_rows = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.record_id == rio.id)
            .order_by(AuditLog.created_at.asc())
        ).all()
    )

    # Load parent destino MarRecord if normalized.parent_mar_id is set
    parent_destino = None
    parent_mar_id_str = (rio.normalized or {}).get("parent_mar_id")
    if parent_mar_id_str:
        try:
            parent_mar = db.get(MarRecord, uuid.UUID(str(parent_mar_id_str)))
            if parent_mar:
                parent_destino = {
                    "mar_id": str(parent_mar.id),
                    "name": (parent_mar.canonical or {}).get("name"),
                }
        except (ValueError, TypeError):
            pass  # Invalid UUID in parent_mar_id — ignore silently

    return {
        "id": str(rio.id),
        "entity_type": rio.entity_type,
        "uf": rio.uf,
        "routing": rio.routing,
        "sub_state": rio.sub_state,
        "score": float(rio.score) if rio.score is not None else None,
        "score_breakdown": rio.score_breakdown or {},
        "canonical_key": rio.canonical_key,
        # T-08-04: _safe_normalized masks phone_e164 → phone_masked
        "normalized": _safe_normalized(rio.normalized),
        "audit_log": [
            {
                "action": row.action,
                "actor": row.actor,
                "after_state": row.after_state,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in audit_rows
        ],
        "parent_destino": parent_destino,
    }


@router.patch(
    "/api/v1/atrativos/{rio_id}/advance",
    status_code=200,
    dependencies=[
        Depends(require_steward_or_bearer),
        Depends(require_editing_unlocked),
    ],
)
def advance_atrativo_state(
    rio_id: uuid.UUID,
    body: AdvanceBody,
    db: Session = Depends(get_db),
) -> dict:
    """Steward advances the FSM sub_state of an atrativo (D-04, T-08-03).

    Calls advance_sub_state(lock=True) — SELECT FOR UPDATE serializes concurrent
    requests against the same rio_id. Returns 409 on expected_state mismatch
    (idempotency guard: already advanced or wrong state).

    Returns 200 with {status, rio_id, sub_state}.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    from brave.core.atrativos.state_machine import advance_sub_state

    advanced = advance_sub_state(
        session=db,
        rio=rio,
        expected_state=body.expected_state,
        next_state=body.next_state,
        actor="steward",
        lock=True,  # SELECT FOR UPDATE; use lock=False only in offline tests (Pitfall 4)
    )
    if not advanced:
        raise HTTPException(
            status_code=409,
            detail=f"sub_state is '{rio.sub_state}', expected '{body.expected_state}'",
        )

    db.commit()
    return {"status": "ok", "rio_id": str(rio_id), "sub_state": rio.sub_state}


@router.patch(
    "/api/v1/atrativos/{rio_id}/descarte",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
def descarte_atrativo(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward rejects an atrativo from the gate (routing → dlq, reason = steward_rejected_gate) (D-04, T-08-02).

    Sets routing='dlq', dlq_reason='steward_rejected_gate', sub_state=None.
    Writes audit log.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"sub_state": rio.sub_state, "routing": rio.routing}
    rio.routing = "dlq"
    rio.dlq_reason = "steward_rejected_gate"
    rio.sub_state = None

    write_audit(
        session=db,
        action="whatsapp_gate_rejected",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"routing": "dlq", "dlq_reason": "steward_rejected_gate", "sub_state": None},
        actor="steward",
    )
    db.commit()
    return {"status": "ok", "routing": "dlq", "rio_id": str(rio_id)}


@router.patch(
    "/api/v1/atrativos/{rio_id}/edit",
    status_code=200,
    dependencies=[
        Depends(require_steward_or_bearer),
        Depends(require_editing_unlocked),
    ],
)
def edit_atrativo(
    rio_id: uuid.UUID,
    body: EditBody,
    db: Session = Depends(get_db),
) -> dict:
    """Steward edits canonical fields on an atrativo normalized payload (D-04, T-08-05).

    Merges body.fields into rio.normalized, EXCLUDING phone_e164 (T-08-05:
    never allow overwriting PII via the edit endpoint).

    Uses flag_modified to ensure SQLAlchemy detects the JSON mutation (Pitfall 3).

    Returns 200 OK.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"normalized_keys": list((rio.normalized or {}).keys())}

    # T-08-05: exclude phone_e164 from body.fields — never allow PII overwrite via edit
    sanitized_fields = {k: v for k, v in body.fields.items() if k != "phone_e164"}

    # Pitfall 3: reassign + flag_modified; never mutate in-place
    normalized = dict(rio.normalized or {})
    normalized.update(sanitized_fields)
    rio.normalized = normalized
    flag_modified(rio, "normalized")

    write_audit(
        session=db,
        action="cms_edited",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"edited_keys": list(sanitized_fields.keys())},
        actor="steward",
    )
    db.commit()
    return {"status": "ok"}
