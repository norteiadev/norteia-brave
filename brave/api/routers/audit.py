"""GET /api/v1/audit — audit log (D-21, OBS-04)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.deps import get_db
from brave.core.models import AuditLog

router = APIRouter()


@router.get("/api/v1/audit")
def list_audit_log(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Return paginated audit log entries (most recent first).

    Args:
        limit:  Maximum number of entries (default 100, max 1000).
        offset: Number of entries to skip for pagination.
    """
    rows = list(
        db.scalars(
            select(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return [
        {
            "id": str(r.id),
            "action": r.action,
            "entity_type": r.entity_type,
            "record_id": str(r.record_id) if r.record_id else None,
            "actor": r.actor,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
