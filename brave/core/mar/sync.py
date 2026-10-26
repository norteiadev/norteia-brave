"""Mar → norteia-api sync health: is the API reachable, and what is still unpushed.

A Mar row norteia-api is behind on (``_needs_sync``: never accepted, updated after the
last attempt, or whose Rio left 'mar' and still shows on the API) is pending.
brave.publish_mar (via NorteiaApiClient.push) consults ``norteia_api_up`` before the POST so a down API (or an expired token) costs one cached
probe instead of 3 Celery retries per record, and ``pending_push_rows`` feeds the
re-dispatch (beat + Painel "Reenviar") in brave.core.mar.publication.republish_pending.
"""

from __future__ import annotations

import contextlib
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from brave.config.settings import AppConfig
from brave.core.models import MarRecord, RioRecord

logger = structlog.get_logger(__name__)

_HEALTH_KEY = "brave:norteia_api:health"
_HEALTH_TTL_S = 30
_HEALTH_PATH = "/api/v1/health"  # norteia-api: 200 when its DB + Redis answer, else 503
# An empty ingest POST proves what /health cannot: the route is deployed and the
# Sanctum token is still valid (it expires). 422 = reached validation, nothing written.
_INGEST_PROBE_PATH = "/api/internal/territorial/attractions"
_PROBE_ATTEMPTS = 2
_PROBE_RETRY_S = 0.5
# ponytail: one tick re-dispatches at most this many; a bigger backlog drains over
# the next ticks (or further "Reenviar" clicks). Raise if backlogs outgrow it.
PENDING_DISPATCH_LIMIT = 500


def _probe(base_url: str) -> str:
    """Return "ok", or why a push would fail: unreachable | unhealthy:<st> | ingest:<st>.

    A transport error retries the whole sequence once (a client-side blip must not
    cache "down" for 30s); an HTTP status is the API's answer and is not retried.
    """
    token = os.environ.get("BRAVE_NORTEIA_API_SERVICE_TOKEN", "")
    for attempt in range(1, _PROBE_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=3.0) as client:
                health = client.get(f"{base_url}{_HEALTH_PATH}").status_code
                # 429 = /health is under the per-IP 'api' throttle: the API answered,
                # so the (unthrottled) internal ingest route decides.
                if health not in (200, 429):
                    return f"unhealthy:{health}"
                ingest = client.post(
                    f"{base_url}{_INGEST_PROBE_PATH}",
                    json={},
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                ).status_code
            return "ok" if ingest == 422 else f"ingest:{ingest}"
        except httpx.HTTPError as exc:
            logger.warning(
                "norteia_api_probe_error",
                url=base_url,
                attempt=attempt,
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )
            if attempt < _PROBE_ATTEMPTS:
                time.sleep(_PROBE_RETRY_S)
    return "unreachable"


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


# A row attempted this recently is not re-dispatched: publish_mar's own retry chain (5xx:
# 180+360+720s) finishes inside it, so a tick never stacks a second chain on the same row.
_RETRY_AFTER = timedelta(minutes=30)
# A row norteia-api rejected (permanent 4xx) is retried once a day — a fix on either side
# (contract, data) then lands on its own — and never hogs the dispatch cap meanwhile.
_REJECTED_RETRY_AFTER = timedelta(hours=24)


def _needs_sync() -> Any:
    """Active Mar row whose norteia-api copy is behind its Rio (joined on RioRecord).

    In Mar: never accepted, retracted earlier (re-show it), or re-scored after the last
    attempt — route_by_score stamps processed_at on every description / Places / TA-backfill
    write, and publish re-derives the snapshot (F48). Out of Mar: not yet retracted (F47).
    """
    in_mar = RioRecord.routing == "mar"
    return and_(
        MarRecord.superseded_by_id.is_(None),
        or_(
            and_(
                in_mar,
                or_(
                    MarRecord.pushed_at.is_(None),
                    MarRecord.retracted_at.isnot(None),
                    RioRecord.processed_at
                    > func.coalesce(MarRecord.push_attempted_at, MarRecord.published_at),
                ),
            ),
            and_(~in_mar, MarRecord.retracted_at.is_(None)),
        ),
    )


def _base() -> Any:
    return select(func.count(MarRecord.id)).join(RioRecord, RioRecord.id == MarRecord.rio_id)


def count_pending_pushes(session: Session) -> int:
    """Active Mar rows norteia-api is behind on (rejected ones excluded — see below)."""
    stmt = _base().where(_needs_sync(), MarRecord.push_error.is_(None))
    return session.scalar(stmt) or 0


def count_rejected_pushes(session: Session) -> int:
    """Active Mar rows norteia-api refused with a permanent 4xx (``push_error`` set)."""
    stmt = _base().where(_needs_sync(), MarRecord.push_error.isnot(None))
    return session.scalar(stmt) or 0


def pending_push_rows(
    session: Session, limit: int = PENDING_DISPATCH_LIMIT, *, now: datetime | None = None
) -> list[tuple[Any, str]]:
    """(rio_id, entity_type) of up to ``limit`` rows to dispatch, never-attempted first."""
    now = now or datetime.now(UTC)
    attempted = MarRecord.push_attempted_at
    due = or_(
        attempted.is_(None),
        and_(MarRecord.push_error.is_(None), attempted < now - _RETRY_AFTER),
        attempted < now - _REJECTED_RETRY_AFTER,
    )
    return [
        (rio_id, entity_type)
        for rio_id, entity_type in session.execute(
            select(MarRecord.rio_id, MarRecord.entity_type)
            .join(RioRecord, RioRecord.id == MarRecord.rio_id)
            .where(_needs_sync(), due)
            .order_by(attempted.asc().nulls_first(), MarRecord.published_at)
            .limit(limit)
        ).all()
    ]
