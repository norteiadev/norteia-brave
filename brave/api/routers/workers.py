"""Process observability endpoints (D-05).

Provides:
  GET /api/v1/workers   — Celery inspect + Redis queue depths, graceful broker-absent
  GET /api/v1/failures  — PoisonQuarantine list with by_task counts

Both endpoints are Bearer-guarded. Neither performs any writes.

Design decisions:
  - celery_app imported lazily inside handler body to avoid import-time broker
    connection (Pitfall 1: hanging on Celery import when broker is down).
  - inspect(timeout=1.0) + try/except wraps the entire inspect block; None returns
    coerced to {} so broker absence always returns 200 with broker_reachable=False,
    never a 500.
  - Redis LLEN wrapped in separate try/except; returns null on Redis error.
  - PoisonQuarantine.payload NOT serialized in /failures list response — it can be
    large and contain pipeline internals (T-08-08).
"""

import uuid

from fastapi import APIRouter, Depends, Query
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, require_bearer
from brave.core.models import PoisonQuarantine

router = APIRouter()


@router.get("/api/v1/workers", dependencies=[Depends(require_bearer)])
def get_workers(redis: Redis = Depends(get_redis)) -> dict:
    """Return Celery worker health + Redis queue depths.

    Gracefully handles broker absence: inspect timeout=1.0s, entire block in
    try/except, None returns coerced to empty dict. Returns broker_reachable=false
    and workers=[] (not a 500) when no broker or workers are available.

    T-08-07: timeout=1.0 + try/except prevents self-inflicted DoS from broker hang.
    """
    # Lazy import to avoid import-time broker connection (Pitfall 1).
    from brave.tasks.celery_app import app as celery_app  # noqa: PLC0415

    try:
        i = celery_app.control.inspect(timeout=1.0)
        ping = i.ping() or {}  # None → {} when broker unreachable
        active = i.active() or {}
        reserved = i.reserved() or {}
    except Exception:
        ping = active = reserved = {}

    broker_reachable = bool(ping)
    workers = [
        {
            "hostname": h,
            "status": "up" if resp.get("ok") == "pong" else "down",
            "active_count": len(active.get(h, [])),
            "reserved_count": len(reserved.get(h, [])),
        }
        for h, resp in ping.items()
    ]

    try:
        queue_depths = {
            "brave.sweep": redis.llen("brave.sweep"),
            "celery": redis.llen("celery"),
        }
    except Exception:
        queue_depths = {"brave.sweep": None, "celery": None}

    return {
        "broker_reachable": broker_reachable,
        "workers": workers,
        "queues": queue_depths,
        "beat_schedule": {"entries": 54, "queues": ["brave.sweep"]},
    }


@router.get("/api/v1/failures", dependencies=[Depends(require_bearer)])
def get_failures(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """Return PoisonQuarantine list with by_task counts.

    Returns up to `limit` quarantine entries ordered by quarantined_at DESC.
    Provides a by_task count dict for quick anomaly detection.

    T-08-08: PoisonQuarantine.payload NOT included in list response — it can be
    large and contain pipeline internals. Only task_name + error_message (truncated
    to 500 chars) + quarantined_at are surfaced.
    """
    rows = list(
        db.scalars(
            select(PoisonQuarantine)
            .order_by(PoisonQuarantine.quarantined_at.desc())
            .limit(limit)
        ).all()
    )

    by_task: dict[str, int] = {}
    for r in rows:
        by_task[r.task_name] = by_task.get(r.task_name, 0) + 1

    return {
        "total": len(rows),
        "by_task": by_task,
        "items": [
            {
                "id": str(r.id),
                "task_name": r.task_name,
                "error_message": (r.error_message or "")[:500],
                "quarantined_at": r.quarantined_at.isoformat() if r.quarantined_at else None,
            }
            for r in rows
        ],
    }
