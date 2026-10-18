"""TripAdvisor sweep progress — a Redis-backed live-progress state for the bulk run.

The bulk paginating sweep (15-07) fans out over the 334 pages of the all-Brazil
AttractionsFusion listing. This module is the single shared progress surface
between the Celery worker (the *writer*) and the FastAPI status endpoint + Next.js
dashboard panel (the *readers*). It mirrors `brave/core/engine.py`: pure functions
over a sync Redis client — no DB, no dispatch, fakeredis-testable.

State lives in one Redis HASH (following the repo `brave:ta:*` convention, cf.
`client.py:47` `brave:ta:session`):

  brave:ta:sweep:progress
    state                  idle | running | done | stopped | stopped_needs_bootstrap
    pages_total            how many pages this run will fetch
    pages_done             how many pages have been ingested so far
    attractions_ingested   running count of cards landed in Nascente
    current_offset         the oa{N} offset of the most recent page
    last_completed_offset  the oa{N} offset to resume AFTER on a re-run
    next_page              the 1-based page a resumed run starts at (the resume cursor)
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

from datetime import UTC, datetime
from typing import Any

IDLE = "idle"
RUNNING = "running"
DONE = "done"
STOPPED = "stopped"
STOPPED_NEEDS_BOOTSTRAP = "stopped_needs_bootstrap"
_VALID_STATES = frozenset({IDLE, RUNNING, DONE, STOPPED, STOPPED_NEEDS_BOOTSTRAP})

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
_F_NEXT_PAGE = "next_page"

# A "running" hash untouched this long is a dead worker, not a slow page (a page takes
# seconds; the Nominatim-paced worst case is ~1 min). Surfaced as ``stale``.
_STALE_AFTER_SECONDS = 600


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _now() -> str:
    """ISO8601 UTC timestamp (no secrets, safe to expose)."""
    return datetime.now(UTC).isoformat()


def start(
    redis: Any,
    pages_total: int,
    resume_from_offset: int = 0,
    *,
    resume: bool = False,
    start_page: int | None = None,
) -> None:
    """Seed a run: state=running.

    Fresh (default): counters zeroed, offsets seeded at ``resume_from_offset`` and the
    resume cursor at ``start_page`` (default: the page after that offset).
    ``resume=True`` continues the stored run: only state/pages_total/updated_at change,
    so pages_done, attractions, offsets and next_page stay cumulative.
    """
    now = _now()
    if resume:
        redis.hset(
            _PROGRESS_KEY,
            mapping={_F_STATE: RUNNING, _F_PAGES_TOTAL: int(pages_total), _F_UPDATED_AT: now},
        )
        return
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
            _F_NEXT_PAGE: int(start_page or resume_from_offset // 30 + 1),
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
            _F_NEXT_PAGE: int(offset) // 30 + 2,
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


def stop(redis: Any) -> None:
    """Terminal state: the run ended before its last page — motor paused/off/stopped, or a
    provider billing wall. The resume offset is intact; a re-run continues from it."""
    redis.hset(_PROGRESS_KEY, mapping={_F_STATE: STOPPED, _F_UPDATED_AT: _now()})


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
            "updated_at": None,
            "stale": False,
        }

    hash_map = {_decode(k): v for k, v in raw.items()}

    state = _decode(hash_map.get(_F_STATE))
    if state not in _VALID_STATES:
        state = IDLE

    started_at = _decode(hash_map.get(_F_STARTED_AT)) or None
    updated_at = _decode(hash_map.get(_F_UPDATED_AT)) or None
    stale = False
    if state == RUNNING and updated_at:
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(updated_at)).total_seconds()
            stale = age > _STALE_AFTER_SECONDS
        except ValueError:
            stale = False

    return {
        "state": state,
        "pages_done": int(_decode(hash_map.get(_F_PAGES_DONE)) or 0),
        "pages_total": int(_decode(hash_map.get(_F_PAGES_TOTAL)) or 0),
        "attractions_ingested": int(_decode(hash_map.get(_F_ATTRACTIONS)) or 0),
        "current_offset": int(_decode(hash_map.get(_F_CURRENT_OFFSET)) or 0),
        "error_count": int(_decode(hash_map.get(_F_ERROR_COUNT)) or 0),
        "started_at": started_at,
        "updated_at": updated_at,
        "stale": stale,
    }


def get_resume_offset(redis: Any) -> int:
    """The last fully-completed offset (0 when absent).

    The consumer resumes at the page AFTER this: start_page = offset // 30 + 1.
    """
    raw = redis.hget(_PROGRESS_KEY, _F_LAST_COMPLETED_OFFSET)
    return int(_decode(raw) or 0)


def get_resume_page(redis: Any) -> int | None:
    """The page a re-run continues at, or None when it must start fresh.

    Only an unfinished run (running / stopped / stopped_needs_bootstrap) resumes; idle
    and done start fresh, so a finished national run never turns every later bulk into
    a no-op. Read from the explicit next_page cursor, so a resumed run that halts
    before completing a page keeps its place (pages_done is not the signal). A hash
    written before next_page existed falls back to last_completed_offset when it
    recorded a page.
    """
    raw = redis.hgetall(_PROGRESS_KEY)
    hash_map = {_decode(k): _decode(v) for k, v in (raw or {}).items()}
    if hash_map.get(_F_STATE) not in (RUNNING, STOPPED, STOPPED_NEEDS_BOOTSTRAP):
        return None
    if hash_map.get(_F_NEXT_PAGE):
        return int(hash_map[_F_NEXT_PAGE])
    if int(hash_map.get(_F_PAGES_DONE) or 0) > 0:
        return int(hash_map.get(_F_LAST_COMPLETED_OFFSET) or 0) // 30 + 2
    return None
