"""TripAdvisor sweep progress — a Redis-backed live-progress state for the bulk run.

The bulk paginating sweep (15-07) fans out over the 334 pages of the all-Brazil
AttractionsFusion listing. This module is the single shared progress surface
between the Celery worker (the *writer*) and the FastAPI status endpoint + Next.js
dashboard panel (the *readers*). It mirrors `brave/core/engine.py`: pure functions
over a sync Redis client — no DB, no dispatch, fakeredis-testable.

State lives in one Redis HASH (following the repo `brave:ta:*` convention, cf.
`client.py:47` `brave:ta:session`):

  brave:ta:sweep:progress
    state                  idle | running | done | stopped_needs_bootstrap
    pages_total            how many pages this run will fetch
    pages_done             how many pages have been ingested so far
    attractions_ingested   running count of cards landed in Nascente
    current_offset         the oa{N} offset of the most recent page
    last_completed_offset  the oa{N} offset to resume AFTER on a re-run
    error_count            per-page ingest errors (best-effort; non-fatal)
    started_at             ISO8601 timestamp of the run start
    updated_at             ISO8601 timestamp of the last write

SECURITY (T-15-03-02 / T-12-02-01): this hash holds ONLY offsets, counts, state,
and timestamps. It NEVER stores cookie/session/datadome/proxy/user-agent values —
those live exclusively in the `brave:ta:session` key the worker reads. The progress
hash is the operator-visible surface; keeping it secret-free is what lets the
read-only endpoint serialize it verbatim.

Resume contract (pinned by tests): after start(pages_total=334) then
record_page(offset=30, ingested_delta=30), get_resume_offset() == 30, and the
consumer computes start_page = last_completed_offset // 30 + 1 → page 3 / offset 60
(the page AFTER the last fully-completed offset).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

IDLE = "idle"
RUNNING = "running"
DONE = "done"
STOPPED_NEEDS_BOOTSTRAP = "stopped_needs_bootstrap"
_VALID_STATES = frozenset({IDLE, RUNNING, DONE, STOPPED_NEEDS_BOOTSTRAP})

# One Redis HASH, brave:ta:* convention (client.py:47 brave:ta:session).
_PROGRESS_KEY = "brave:ta:sweep:progress"

# Hash fields (no secrets — offsets/counts/state/timestamps only).
_F_STATE = "state"
_F_PAGES_TOTAL = "pages_total"
_F_PAGES_DONE = "pages_done"
_F_ATTRACTIONS = "attractions_ingested"
_F_CURRENT_OFFSET = "current_offset"
_F_LAST_COMPLETED_OFFSET = "last_completed_offset"
_F_ERROR_COUNT = "error_count"
_F_STARTED_AT = "started_at"
_F_UPDATED_AT = "updated_at"


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _now() -> str:
    """ISO8601 UTC timestamp (no secrets, safe to expose)."""
    return datetime.now(timezone.utc).isoformat()


def start(redis: Any, pages_total: int, resume_from_offset: int = 0) -> None:
    """Seed a fresh run: state=running, counters zeroed, offsets seeded for resume.

    `resume_from_offset` lets a re-run continue mid-sweep — current_offset and
    last_completed_offset start there so get_resume_offset() reflects prior progress.
    """
    now = _now()
    redis.hset(
        _PROGRESS_KEY,
        mapping={
            _F_STATE: RUNNING,
            _F_PAGES_TOTAL: int(pages_total),
            _F_PAGES_DONE: 0,
            _F_ATTRACTIONS: 0,
            _F_CURRENT_OFFSET: int(resume_from_offset),
            _F_LAST_COMPLETED_OFFSET: int(resume_from_offset),
            _F_ERROR_COUNT: 0,
            _F_STARTED_AT: now,
            _F_UPDATED_AT: now,
        },
    )


def record_page(redis: Any, offset: int, ingested_delta: int) -> None:
    """Record one fully-ingested page: bump pages_done + attractions, advance offsets."""
    redis.hincrby(_PROGRESS_KEY, _F_PAGES_DONE, 1)
    redis.hincrby(_PROGRESS_KEY, _F_ATTRACTIONS, int(ingested_delta))
    redis.hset(
        _PROGRESS_KEY,
        mapping={
            _F_CURRENT_OFFSET: int(offset),
            _F_LAST_COMPLETED_OFFSET: int(offset),
            _F_UPDATED_AT: _now(),
        },
    )


def record_error(redis: Any) -> None:
    """Increment the per-page error counter (best-effort; non-fatal).

    Used by the bulk producer (15-06) when a single page's ingest fails but the
    sweep continues. Does NOT change `state` — only a fail-fast 403/429 transitions
    to stopped_needs_bootstrap via stop_needs_bootstrap().
    """
    redis.hincrby(_PROGRESS_KEY, _F_ERROR_COUNT, 1)
    redis.hset(_PROGRESS_KEY, _F_UPDATED_AT, _now())


def stop_needs_bootstrap(redis: Any) -> None:
    """Terminal state: the session expired mid-run (403/429) — operator must re-inject."""
    redis.hset(
        _PROGRESS_KEY,
        mapping={_F_STATE: STOPPED_NEEDS_BOOTSTRAP, _F_UPDATED_AT: _now()},
    )


def mark_done(redis: Any) -> None:
    """Terminal state: the run completed all its pages."""
    redis.hset(_PROGRESS_KEY, mapping={_F_STATE: DONE, _F_UPDATED_AT: _now()})


def get_progress(redis: Any) -> dict[str, Any]:
    """Snapshot for the endpoint — EXACTLY the serialized field set.

    Returns state=idle + zeros when the hash is absent (no run has started).
    Ints are decoded via the bytes/None-safe _decode helper.
    """
    raw = redis.hgetall(_PROGRESS_KEY)
    if not raw:
        return {
            "state": IDLE,
            "pages_done": 0,
            "pages_total": 0,
            "attractions_ingested": 0,
            "current_offset": 0,
            "error_count": 0,
            "started_at": None,
        }

    hash_map = {_decode(k): v for k, v in raw.items()}

    state = _decode(hash_map.get(_F_STATE))
    if state not in _VALID_STATES:
        state = IDLE

    started_at = _decode(hash_map.get(_F_STARTED_AT)) or None

    return {
        "state": state,
        "pages_done": int(_decode(hash_map.get(_F_PAGES_DONE)) or 0),
        "pages_total": int(_decode(hash_map.get(_F_PAGES_TOTAL)) or 0),
        "attractions_ingested": int(_decode(hash_map.get(_F_ATTRACTIONS)) or 0),
        "current_offset": int(_decode(hash_map.get(_F_CURRENT_OFFSET)) or 0),
        "error_count": int(_decode(hash_map.get(_F_ERROR_COUNT)) or 0),
        "started_at": started_at,
    }


def get_resume_offset(redis: Any) -> int:
    """The last fully-completed offset (0 when absent).

    The consumer resumes at the page AFTER this: start_page = offset // 30 + 1.
    """
    raw = redis.hget(_PROGRESS_KEY, _F_LAST_COMPLETED_OFFSET)
    return int(_decode(raw) or 0)
