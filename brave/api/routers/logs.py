"""GET /api/v1/logs — per-source Brave log tail endpoint (T-ks0-02).

Bearer-gated. Returns the newest entries from the Redis log ring buffer for
the given source. When source is omitted, defaults to the active engine source
(brave:engine:source key in Redis).

LGPD note: the ring buffer enforced by log_buffer.py never contains cookie/
token/proxy/session/api_key fields — the _BLOCKED_FIELDS guard in append_log
is the enforcement point. This endpoint serialises the stored entries verbatim
and is therefore safe to expose to the operator dashboard.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from redis import Redis

from brave.api.deps import get_redis, require_bearer
from brave.core import engine as collection_engine
from brave.observability.log_buffer import tail_logs

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.get("/api/v1/logs", dependencies=[Depends(require_bearer)])
def get_logs(
    source: str | None = Query(None),
    since: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=200),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Tail the per-source log ring buffer. Bearer-gated (T-ks0-02).

    When source is omitted, defaults to the active engine source
    (brave:engine:source). Returns {source, lines: [{id, ts, level, event,
    ...safe_fields}], cursor}.

    Query params:
      source  — which ring buffer to read (default: active engine source)
      since   — cursor from the last response; only lines with id > since returned
      limit   — max lines to return (1–200, default 50)
    """
    if source is None:
        source = collection_engine.get_source(redis) or "default"
    lines, cursor = tail_logs(redis, source, since_id=since, limit=limit)
    return {"source": source, "lines": lines, "cursor": cursor}
