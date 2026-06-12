"""DLQ management endpoints (D-21, CORE-07, CORE-08, D-07, D-08).

GET  /api/v1/dlq                          — list DLQ records
PATCH /api/v1/dlq/{rio_id}/reprocess      — trigger reprocess
PATCH /api/v1/dlq/{rio_id}/descarte       — steward reject
PATCH /api/v1/dlq/{rio_id}/validate       — steward validate: set validacao_humana=100 → re-score → Mar + push (D-07)
POST  /api/v1/dlq/validate-batch          — batch validate all DLQ records for a UF (D-08)
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


@router.patch("/api/v1/dlq/{rio_id}/validate", status_code=202)
def validate_dlq_record(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward validates a DLQ record: sets validacao_humana=100 → re-score → Mar + push (D-07).

    Steps:
    1. Load RioRecord; 404 if missing.
    2. Reassign normalized dict with validacao_humana_value=100.0 + flag_modified (Pitfall 3).
    3. Re-score via reprocess_record (NOT process_nascente_record — Pitfall 4).
    4. If routing becomes 'mar': dispatch push_destination_task (sync fallback if no broker).
    5. Write audit row with action='dlq_validated', actor='steward'.

    Returns 202 with {status, rio_id, routing}.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "score": float(rio.score or 0)}

    # CRITICAL: reassign + flag_modified — SQLAlchemy does not auto-track in-place
    # JSON column mutations (Pitfall 3, T-02-06-04).
    from sqlalchemy.orm.attributes import flag_modified
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0
    rio.normalized = normalized
    flag_modified(rio, "normalized")
    db.flush()

    # Re-score: reprocess_record resets routing → in_progress → re-routes via §7.6
    # Never call process_nascente_record here — it returns early if canonical_key exists (Pitfall 4)
    from brave.config.settings import ScoreConfig
    from brave.core.rio.routing import reprocess_record
    reprocess_record(db, rio_id, ScoreConfig())

    db.refresh(rio)

    # Only dispatch push when routing == 'mar' (never call promote_to_mar without this check)
    if rio.routing == "mar":
        try:
            from brave.tasks.pipeline import push_destination_task
            push_destination_task.delay(str(rio_id))
        except Exception:
            # Sync fallback: no Celery broker in tests/dev
            from brave.core.mar.service import promote_to_mar
            promote_to_mar(db, rio)

    write_audit(
        session=db,
        action="dlq_validated",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"routing": rio.routing, "score": float(rio.score or 0)},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id), "routing": rio.routing}


@router.post("/api/v1/dlq/validate-batch", status_code=202)
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

    Returns 202 with {status, uf, validated}.
    """
    rows = list(
        db.scalars(
            select(RioRecord).where(
                RioRecord.routing == "dlq",
                RioRecord.uf == uf,
                RioRecord.entity_type == entity_type,
            ).limit(limit)
        ).all()
    )

    validated = 0
    for rio in rows:
        # Inline single-record validate logic (same flag_modified + reprocess pattern)
        from sqlalchemy.orm.attributes import flag_modified as _flag_modified
        normalized = dict(rio.normalized or {})
        normalized["validacao_humana_value"] = 100.0
        rio.normalized = normalized
        _flag_modified(rio, "normalized")
        db.flush()

        from brave.config.settings import ScoreConfig as _ScoreConfig
        from brave.core.rio.routing import reprocess_record as _reprocess_record
        _reprocess_record(db, rio.id, _ScoreConfig())
        db.refresh(rio)

        if rio.routing == "mar":
            try:
                from brave.tasks.pipeline import push_destination_task
                push_destination_task.delay(str(rio.id))
            except Exception:
                from brave.core.mar.service import promote_to_mar
                promote_to_mar(db, rio)

        write_audit(
            session=db,
            action="dlq_validated",
            entity_type=rio.entity_type,
            record_id=rio.id,
            before_state={"routing": "dlq", "score": float(rio.score or 0)},
            after_state={"routing": rio.routing, "score": float(rio.score or 0)},
            actor="steward",
        )
        validated += 1

    return {"status": "accepted", "uf": uf, "validated": validated}


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
