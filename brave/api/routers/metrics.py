"""GET /api/v1/metrics — per-layer volume metrics (D-21, OBS-03)."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.api.deps import get_db
from brave.core.models import MarRecord, NascenteRecord, RioRecord

router = APIRouter()

ROUTING_VALUES = ["in_progress", "mar", "dlq", "descarte"]


@router.get("/api/v1/metrics")
def get_metrics(db: Session = Depends(get_db)) -> dict:
    """Return per-layer record counts.

    Returns:
        {
            "nascente_count": int,
            "rio_count": {"in_progress": int, "mar": int, "dlq": int, "descarte": int},
            "mar_count": int
        }
    """
    # Nascente count
    nascente_count = db.scalar(select(func.count(NascenteRecord.id))) or 0

    # Rio counts grouped by routing
    rio_counts: dict[str, int] = {v: 0 for v in ROUTING_VALUES}
    rows = db.execute(
        select(RioRecord.routing, func.count(RioRecord.id)).group_by(RioRecord.routing)
    ).fetchall()
    for routing, count in rows:
        if routing in rio_counts:
            rio_counts[routing] = count

    # Mar count
    mar_count = db.scalar(select(func.count(MarRecord.id))) or 0

    return {
        "nascente_count": nascente_count,
        "rio_count": rio_counts,
        "mar_count": mar_count,
    }
