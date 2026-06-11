"""GET /api/v1/health — readiness check (D-21)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from redis import Redis

from brave.api.deps import get_db, get_redis

router = APIRouter()


@router.get("/api/v1/health")
def health_check(
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Return service health status.

    Checks:
      - DB: can execute a trivial query
      - Redis: ping responds

    Returns:
        {"status": "ok", "db": "ok", "redis": "ok"}
    """
    db_status = "ok"
    redis_status = "ok"

    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception:
        db_status = "error"

    try:
        redis.ping()
    except Exception:
        redis_status = "error"

    return {"status": "ok", "db": db_status, "redis": redis_status}
