"""DLQ management endpoints (D-21, CORE-07, CORE-08).

GET  /api/v1/dlq           — list DLQ records
PATCH /api/v1/dlq/{rio_id}/reprocess — trigger reprocess
PATCH /api/v1/dlq/{rio_id}/descarte  — steward reject
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.deps import get_db
from brave.core.models import RioRecord
from brave.observability.audit import write_audit

router = APIRouter()


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
    query = select(RioRecord).where(RioRecord.routing == "dlq")
    if uf:
        query = query.where(RioRecord.uf == uf)
    if entity_type:
        query = query.where(RioRecord.entity_type == entity_type)
    query = query.limit(limit)

    rows = list(db.scalars(query).all())
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


@router.patch("/api/v1/dlq/{rio_id}/reprocess", status_code=202)
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
        from brave.config.settings import ScoreConfig
        from brave.core.rio.routing import reprocess_record
        reprocess_record(db, rio_id, ScoreConfig())

    write_audit(
        session=db,
        action="dlq_reprocessed",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state={"routing": "dlq", "dlq_reason": rio.dlq_reason},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id)}


@router.patch("/api/v1/dlq/{rio_id}/descarte", status_code=200)
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
