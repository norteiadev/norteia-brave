"""Unit tests for the TripAdvisor sweep progress Redis-state module (plan 15-03, TA-12).

The module is a pure writer/reader surface over a Redis HASH
(`brave:ta:sweep:progress`) mirroring `brave/core/engine.py`. Writer is the Celery
sweep worker (15-07), reader is the FastAPI progress endpoint (Task 2) + dashboard
panel (15-08). It holds ONLY offsets/counts/state/timestamps — never cookies/session.

All tests are 100% offline (fakeredis, no real TripAdvisor calls).
"""

from __future__ import annotations

import fakeredis
import pytest

from brave.lanes.tripadvisor import sweep_progress


@pytest.fixture
def redis():
    """Fresh FakeRedis per test."""
    return fakeredis.FakeRedis()


# ---------------------------------------------------------------------------
# Key convention
# ---------------------------------------------------------------------------


def test_progress_key_follows_ta_convention():
    """The hash key must follow the brave:ta:* convention."""
    assert sweep_progress._PROGRESS_KEY == "brave:ta:sweep:progress"


# ---------------------------------------------------------------------------
# get_progress on an absent hash → idle + zeros
# ---------------------------------------------------------------------------


def test_get_progress_absent_returns_idle(redis):
    """No hash present → state=idle with zeroed counters and no started_at."""
    snap = sweep_progress.get_progress(redis)
    assert snap == {
        "state": "idle",
        "pages_done": 0,
        "pages_total": 0,
        "attractions_ingested": 0,
        "current_offset": 0,
        "error_count": 0,
        "started_at": None,
    }


def test_get_resume_offset_absent_returns_zero(redis):
    """No hash present → resume offset is 0."""
    assert sweep_progress.get_resume_offset(redis) == 0


# ---------------------------------------------------------------------------
# start → running snapshot
# ---------------------------------------------------------------------------


def test_start_sets_running_with_totals(redis):
    """start seeds state=running, pages_total, zeroed counters, timestamps."""
    sweep_progress.start(redis, pages_total=334)
    snap = sweep_progress.get_progress(redis)
    assert snap["state"] == "running"
    assert snap["pages_total"] == 334
    assert snap["pages_done"] == 0
    assert snap["attractions_ingested"] == 0
    assert snap["current_offset"] == 0
    assert snap["error_count"] == 0
    assert snap["started_at"] is not None


def test_start_with_resume_offset_seeds_current_offset(redis):
    """start(resume_from_offset=...) seeds current/last_completed offset for resume."""
    sweep_progress.start(redis, pages_total=334, resume_from_offset=60)
    assert sweep_progress.get_progress(redis)["current_offset"] == 60
    assert sweep_progress.get_resume_offset(redis) == 60


# ---------------------------------------------------------------------------
# record_page increments counters + offsets
# ---------------------------------------------------------------------------


def test_record_page_increments(redis):
    """start → record_page(offset=30, ingested_delta=30) → snapshot reflects it."""
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.record_page(redis, offset=30, ingested_delta=30)
    snap = sweep_progress.get_progress(redis)
    assert snap["state"] == "running"
    assert snap["pages_done"] == 1
    assert snap["attractions_ingested"] == 30
    assert snap["current_offset"] == 30


def test_record_page_sets_resume_offset(redis):
    """After record_page(offset=30) the resume offset reads back as 30 (the pinned
    arithmetic contract: consumer resumes at page 3 / offset 60 = the page AFTER)."""
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.record_page(redis, offset=30, ingested_delta=30)
    last = sweep_progress.get_resume_offset(redis)
    assert last == 30
    # Consumer's resume arithmetic: page AFTER last_completed_offset.
    start_page = last // 30 + 1
    assert start_page == 2
    assert start_page * 30 == 60


def test_record_page_accumulates(redis):
    """Two pages accumulate pages_done and attractions_ingested."""
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.record_page(redis, offset=30, ingested_delta=30)
    sweep_progress.record_page(redis, offset=60, ingested_delta=28)
    snap = sweep_progress.get_progress(redis)
    assert snap["pages_done"] == 2
    assert snap["attractions_ingested"] == 58
    assert snap["current_offset"] == 60
    assert sweep_progress.get_resume_offset(redis) == 60


# ---------------------------------------------------------------------------
# record_error — must be a real callable (used by 15-06)
# ---------------------------------------------------------------------------


def test_record_error_increments_error_count(redis):
    """record_error bumps error_count by 1 each call; state unchanged."""
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.record_error(redis)
    sweep_progress.record_error(redis)
    snap = sweep_progress.get_progress(redis)
    assert snap["error_count"] == 2
    assert snap["state"] == "running"


# ---------------------------------------------------------------------------
# terminal states
# ---------------------------------------------------------------------------


def test_stop_needs_bootstrap_sets_terminal_state(redis):
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.stop_needs_bootstrap(redis)
    assert sweep_progress.get_progress(redis)["state"] == "stopped_needs_bootstrap"


def test_mark_done_sets_done_state(redis):
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.mark_done(redis)
    assert sweep_progress.get_progress(redis)["state"] == "done"


# ---------------------------------------------------------------------------
# Secret-free invariant (T-15-03-02)
# ---------------------------------------------------------------------------


def test_progress_hash_holds_no_secrets(redis):
    """The progress hash must carry only offsets/counts/state/timestamps —
    NEVER cookie/session/datadome/proxy/user-agent fields (T-15-03-02)."""
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.record_page(redis, offset=30, ingested_delta=30)
    raw = redis.hgetall(sweep_progress._PROGRESS_KEY)
    keys = {k.decode() if isinstance(k, bytes) else k for k in raw}
    forbidden = {"cookies", "cookie", "session", "session_id", "datadome", "proxy", "user_agent", "query_ids"}
    assert keys.isdisjoint(forbidden), f"secret-bearing fields leaked into progress hash: {keys & forbidden}"
    # Whitelist: only the known non-secret fields.
    # depth/geo_id/target_max_pages are non-secret run params stored for auto-resume (260628-m1n).
    allowed = {
        "state", "pages_total", "pages_done", "attractions_ingested",
        "current_offset", "last_completed_offset", "error_count",
        "started_at", "updated_at",
        "depth", "geo_id", "target_max_pages",
    }
    assert keys <= allowed, f"unexpected fields in progress hash: {keys - allowed}"
