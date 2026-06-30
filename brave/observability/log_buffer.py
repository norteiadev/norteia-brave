"""Redis-backed per-source log ring buffer.

Pure functions over a sync Redis client — no FastAPI/Celery coupling,
fakeredis-testable. Mirrors brave/lanes/tripadvisor/sweep_progress.py in
security posture: no secrets, no PII (§T-ks0-01).

Key layout:
  brave:logs:{source}         — LPUSH list of JSON-encoded log entries (newest at index 0)
  brave:logs:{source}:seq     — INCR monotonic id counter

LGPD guard (_BLOCKED_FIELDS): every append_log call strips sensitive fields
before writing to Redis. String values are capped at 2000 chars to prevent
oversized blobs from slipping through under unexpected key names.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOG_KEY_TPL = "brave:logs:{}"       # LPUSH target per source
_SEQ_KEY_TPL = "brave:logs:{}:seq"   # INCR monotonic id counter
_CAP = 500                            # LTRIM keeps newest 500 entries
_VALUE_CAP = 2000                     # max chars per string value (prevents blob leaks)

# LGPD / T-ks0-01: keys that must never appear in the stored log ring buffer.
# Raw exceptions (exc_info) are also excluded — they may contain stack frames
# with secrets injected via environment / request context.
_BLOCKED_FIELDS: frozenset[str] = frozenset(
    {
        "cookie",
        "cookies",
        "session",
        "token",
        "proxy",
        "user_agent",
        "password",
        "secret",
        "authorization",
        "api_key",
        "exc_info",
    }
)


# ---------------------------------------------------------------------------
# Helpers (mirrored from sweep_progress.py)
# ---------------------------------------------------------------------------


def _decode(value: Any) -> str:
    """Bytes/None-safe decode — mirrors sweep_progress._decode."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _sanitize(d: dict) -> dict:
    """Return a copy of *d* with blocked fields removed and values capped.

    Blocked keys are matched case-insensitively against _BLOCKED_FIELDS so that
    'Cookies', 'COOKIE', etc. are all rejected. String values longer than
    _VALUE_CAP chars are truncated — this is the second-line defence against
    oversized blobs arriving under an unexpected key name.
    """
    out: dict = {}
    for k, v in d.items():
        if k.lower() in _BLOCKED_FIELDS:
            continue
        if isinstance(v, str) and len(v) > _VALUE_CAP:
            v = v[:_VALUE_CAP]
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def append_log(redis: Any, source: str, event_dict: dict) -> None:
    """Append *event_dict* to the per-source log ring buffer in Redis.

    Steps:
      1. Assign a monotonic id via INCR on the per-source sequence key.
      2. Sanitize the event dict (strip _BLOCKED_FIELDS, cap strings).
      3. LPUSH the JSON-encoded entry (newest at index 0 in the list).
      4. LTRIM to keep only the newest _CAP entries.

    Never raises — callers (structlog processors) must not crash on buffer errors.
    """
    seq = redis.incr(_SEQ_KEY_TPL.format(source))
    safe = _sanitize(event_dict)
    safe["id"] = int(seq)
    redis.lpush(_LOG_KEY_TPL.format(source), json.dumps(safe))
    redis.ltrim(_LOG_KEY_TPL.format(source), 0, _CAP - 1)


def tail_logs(
    redis: Any,
    source: str,
    since_id: int | None = None,
    limit: int = 50,
) -> tuple[list[dict], int]:
    """Tail the per-source log ring buffer, returning (lines, cursor).

    Lines are sorted ascending by id (oldest first) so the frontend can
    append them in order. Only entries with id > since_id are returned when
    since_id is given (incremental tail).

    Returns ([], 0) when the source key does not exist.
    """
    raw = redis.lrange(_LOG_KEY_TPL.format(source), 0, _CAP - 1)
    if not raw:
        return [], since_id or 0

    lines: list[dict] = []
    for entry in raw:
        try:
            parsed = json.loads(_decode(entry))
        except (json.JSONDecodeError, ValueError):
            continue
        if since_id is not None and int(parsed.get("id", 0)) <= since_id:
            continue
        lines.append(parsed)

    # Sort ascending (oldest first) — the list stores newest at index 0 (LPUSH)
    lines.sort(key=lambda e: int(e.get("id", 0)))

    # Take last `limit` entries when more than limit after filtering
    if len(lines) > limit:
        lines = lines[-limit:]

    cursor = lines[-1]["id"] if lines else (since_id or 0)
    return lines, cursor
