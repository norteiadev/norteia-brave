"""Mar → norteia-api sync health: is the API reachable, and what is still unpushed.

A Mar row with ``pushed_at IS NULL`` never reached norteia-api (API down, broker
down, retries exhausted). The push tasks consult ``norteia_api_up`` before the POST
so a down API (or an expired token) costs one cached probe instead of 3 Celery retries per record, and
``pending_push_rows`` feeds the re-dispatch (beat + Painel "Reenviar"), which lives
in brave.tasks.pipeline — the kernel never imports tasks.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.config.settings import AppConfig
from brave.core.models import MarRecord

logger = structlog.get_logger(__name__)

_HEALTH_KEY = "brave:norteia_api:health"
_HEALTH_TTL_S = 30
_HEALTH_PATH = "/api/v1/health"  # norteia-api: 200 when its DB + Redis answer, else 503
# An empty ingest POST proves what /health cannot: the route is deployed and the
# Sanctum token is still valid (it expires). 422 = reached validation, nothing written.
_INGEST_PROBE_PATH = "/api/internal/territorial/attractions"
# ponytail: one tick re-dispatches at most this many; a bigger backlog drains over
# the next ticks (or further "Reenviar" clicks). Raise if backlogs outgrow it.
PENDING_DISPATCH_LIMIT = 500


def _probe(base_url: str) -> str:
    """Return "ok", or why a push would fail: unreachable | unhealthy:<st> | ingest:<st>."""
    token = os.environ.get("BRAVE_NORTEIA_API_SERVICE_TOKEN", "")
    try:
        health = httpx.get(f"{base_url}{_HEALTH_PATH}", timeout=3.0).status_code
        if health != 200:
            return f"unhealthy:{health}"
        ingest = httpx.post(
            f"{base_url}{_INGEST_PROBE_PATH}",
            json={},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=3.0,
        ).status_code
    except httpx.HTTPError:
        return "unreachable"
    return "ok" if ingest == 422 else f"ingest:{ingest}"


def norteia_api_health(redis: Any | None = None) -> str | None:
    """Return "ok" or a failure reason (see ``_probe``); None = externals off / no URL.

    Cached in Redis for 30s so a burst of push tasks (or the dashboard status poll)
    shares one probe. A Redis failure degrades to an uncached probe.
    """
    base_url = os.environ.get("BRAVE_NORTEIA_API_URL", "").rstrip("/")
    if not AppConfig().run_real_externals or not base_url:
        return None

    if redis is not None:
        try:
            cached = redis.get(_HEALTH_KEY)
            if cached is not None:
                return cached.decode() if isinstance(cached, bytes) else str(cached)
        except Exception:  # noqa: BLE001 — cache is best-effort
            redis = None

    result = _probe(base_url)
    if result != "ok":
        logger.warning("norteia_api_down", url=base_url, reason=result)

    if redis is not None:
        with contextlib.suppress(Exception):  # cache is best-effort
            redis.set(_HEALTH_KEY, result, ex=_HEALTH_TTL_S)
    return result


def norteia_api_up(redis: Any | None = None) -> bool | None:
    """True/False = a push would go through; None = not applicable."""
    health = norteia_api_health(redis)
    return None if health is None else health == "ok"


def _pending_filter() -> Any:
    return (MarRecord.pushed_at.is_(None), MarRecord.superseded_by_id.is_(None))


def count_pending_pushes(session: Session) -> int:
    """Active Mar rows norteia-api has never accepted."""
    return session.scalar(select(func.count(MarRecord.id)).where(*_pending_filter())) or 0


def pending_push_rows(
    session: Session, limit: int = PENDING_DISPATCH_LIMIT
) -> list[tuple[Any, str]]:
    """(rio_id, entity_type) of up to ``limit`` unpushed active Mar rows, oldest first."""
    return [
        (rio_id, entity_type)
        for rio_id, entity_type in session.execute(
            select(MarRecord.rio_id, MarRecord.entity_type)
            .where(*_pending_filter())
            .order_by(MarRecord.published_at)
            .limit(limit)
        ).all()
    ]
