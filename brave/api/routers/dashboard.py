"""Dashboard read-aggregation surface (D-01, DASH-01..05).

A thin, read-only FastAPI router the operations dashboard (Territorial CMS) reads
through its BFF — so the UI never touches the database directly (D-01). Every
endpoint is Bearer-guarded (require_bearer, D-02) and performs no pipeline logic:
it only reads existing medallion + observability tables.

Endpoints:
  GET /api/v1/dlq/{rio_id} — full DLQ detail (DASH-01): the per-criterion §7.6
      score_breakdown + Rio normalized + Nascente raw payload + signals + the
      per-record WhatsApp/steward event log. The existing GET /api/v1/dlq list
      (dlq.py) deliberately omits these heavier fields; this surfaces them.

Later plans accrete the monitor/cost/funnels/conversations read endpoints onto
this same router.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, require_bearer
from brave.core.models import (
    AuditLog,
    NascenteRecord,
    RioRecord,
)

router = APIRouter()


def _extract_signals(
    normalized: dict | None, payload: dict | None
) -> dict:
    """Pull the SignalAgent signals block from the Rio normalized or Nascente payload.

    Read-only best-effort: the SignalAgent stores its signals under a "signals"
    key. Prefer the normalized (post-processing) view, fall back to the raw
    Nascente payload, default to an empty dict. No PII is surfaced here — this
    lane (destinos/atrativos pre-contact) carries no phone PII in the DLQ detail
    (T-04-10); phone masking is enforced in the plan-07 conversation/gate reads.
    """
    for source in (normalized, payload):
        if isinstance(source, dict):
            signals = source.get("signals")
            if isinstance(signals, dict):
                return signals
    return {}


@router.get("/api/v1/dlq/{rio_id}", dependencies=[Depends(require_bearer)])
def get_dlq_detail(rio_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Return the full DLQ detail for a single Rio record (DASH-01, D-01).

    Surfaces what the list endpoint omits: the §7.6 per-criterion score_breakdown
    (the explainability panel source), the Rio normalized view, the joined raw
    Nascente payload, the extracted signals, and the per-record WhatsApp/steward
    event log (AuditLog rows for this rio_id, oldest-first).

    Read-only (db.get + select; no writes, no pipeline mutation). Bearer-guarded:
    the 401 fires before any DB work. Unknown rio_id → 404 (dlq.py idiom).
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="RioRecord not found"
        )

    nascente = db.get(NascenteRecord, rio.nascente_id)
    nascente_payload = nascente.payload if nascente else {}

    whatsapp_rows = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.record_id == rio.id)
            .order_by(AuditLog.created_at.asc())
        ).all()
    )

    return {
        "id": str(rio.id),
        "routing": rio.routing,
        "sub_state": rio.sub_state,
        "dlq_reason": rio.dlq_reason,
        "score": float(rio.score) if rio.score is not None else None,
        "score_version": rio.score_version,
        # §7.6 per-criterion breakdown — the DASH-01 explainability panel source.
        "score_breakdown": rio.score_breakdown or {},
        "normalized": rio.normalized or {},
        "nascente_payload": nascente_payload,
        "signals": _extract_signals(rio.normalized, nascente_payload),
        "whatsapp_log": [
            {
                "id": str(row.id),
                "action": row.action,
                "actor": row.actor,
                "before_state": row.before_state,
                "after_state": row.after_state,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in whatsapp_rows
        ],
    }
