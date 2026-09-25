"""Last error of each maintenance beat task, surfaced on the Painel.

The maintenance beats (collect_description_batches, prune_record_events, ta_keepalive,
repush_pending_mar, redispatch_stalled_chain) log and swallow — or just let Celery record
— their failures, so nobody sees a beat that fails every tick. Each one records its
failure here and clears it on the next success; GET /api/v1/engine/status lists them.

  brave:beat:last_error:{task}   JSON {at, error_type}, TTL 7 days

Only the exception TYPE is stored, never its message (it may carry PII or cookies).
Kernel module: plain functions over a sync Redis client, no task imports (D-18).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

_KEY_PREFIX = "brave:beat:last_error:"
_TTL_SECONDS = 7 * 24 * 3600


def record_error(redis: Any, task: str, exc: BaseException) -> None:
    """Store ``task``'s latest failure (type + timestamp only)."""
    redis.set(
        _KEY_PREFIX + task,
        json.dumps({"at": datetime.now(UTC).isoformat(), "error_type": type(exc).__name__}),
        ex=_TTL_SECONDS,
    )


def clear_error(redis: Any, task: str) -> None:
    """``task`` succeeded: drop its recorded failure."""
    redis.delete(_KEY_PREFIX + task)


def beat_errors(redis: Any) -> list[dict[str, Any]]:
    """Every recorded failure as ``[{task, at, error_type}]``, sorted by task name."""
    errors = []
    for key in redis.scan_iter(match=_KEY_PREFIX + "*"):
        name = key.decode() if isinstance(key, bytes) else key
        raw = redis.get(key)
        if raw is None:  # expired between SCAN and GET
            continue
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        errors.append(
            {"task": name[len(_KEY_PREFIX):], "at": data.get("at"), "error_type": data.get("error_type")}
        )
    return sorted(errors, key=lambda e: e["task"])
