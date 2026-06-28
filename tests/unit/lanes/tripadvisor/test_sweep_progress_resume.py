"""TDD RED tests for sweep_progress auto-resume extensions (plan 260628-m1n).

Tests the new RESUMING state, claim_resume, get_resume_params, is_paused_needs_bootstrap,
and the start() depth/geo_id/target_max_pages kwargs.

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
# RESUMING state
# ---------------------------------------------------------------------------


def test_resuming_is_valid_state(redis):
    """RESUMING must be in _VALID_STATES so get_progress() returns 'resuming', not 'idle'."""
    assert sweep_progress.RESUMING in sweep_progress._VALID_STATES
    # Set state directly to 'resuming' and verify get_progress does NOT fall back to idle
    redis.hset(sweep_progress._PROGRESS_KEY, mapping={"state": sweep_progress.RESUMING, "pages_total": "0",
                                                        "pages_done": "0", "attractions_ingested": "0",
                                                        "current_offset": "0", "error_count": "0"})
    snap = sweep_progress.get_progress(redis)
    assert snap["state"] == "resuming", f"Expected 'resuming', got '{snap['state']}'"


# ---------------------------------------------------------------------------
# is_paused_needs_bootstrap
# ---------------------------------------------------------------------------


def test_is_paused_needs_bootstrap_true(redis):
    """start() + stop_needs_bootstrap() → is_paused_needs_bootstrap returns True."""
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.stop_needs_bootstrap(redis)
    assert sweep_progress.is_paused_needs_bootstrap(redis) is True


def test_is_paused_needs_bootstrap_false_when_running(redis):
    """start() (no stop) → is_paused_needs_bootstrap returns False (state is RUNNING)."""
    sweep_progress.start(redis, pages_total=334)
    assert sweep_progress.is_paused_needs_bootstrap(redis) is False


def test_is_paused_needs_bootstrap_false_when_absent(redis):
    """Fresh FakeRedis (no hash) → is_paused_needs_bootstrap returns False."""
    assert sweep_progress.is_paused_needs_bootstrap(redis) is False


# ---------------------------------------------------------------------------
# claim_resume
# ---------------------------------------------------------------------------


def test_claim_resume_winner_returns_true(redis):
    """start() + stop_needs_bootstrap() → claim_resume returns True AND state=='resuming'."""
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.stop_needs_bootstrap(redis)
    result = sweep_progress.claim_resume(redis)
    assert result is True
    assert sweep_progress.get_progress(redis)["state"] == "resuming"


def test_claim_resume_second_caller_returns_false(redis):
    """First claim_resume returns True; second call on same Redis returns False (SETNX gate)."""
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.stop_needs_bootstrap(redis)
    first = sweep_progress.claim_resume(redis)
    second = sweep_progress.claim_resume(redis)
    assert first is True
    assert second is False


def test_claim_resume_false_when_not_paused(redis):
    """State is RUNNING (not stopped_needs_bootstrap) → claim_resume returns False.

    The state check fires BEFORE the SETNX so a RUNNING-state call with a fresh
    Redis instance (empty claim key) should still return False.
    """
    sweep_progress.start(redis, pages_total=334)
    # State is RUNNING — claim_resume must bail immediately on state check
    result = sweep_progress.claim_resume(redis)
    assert result is False


# ---------------------------------------------------------------------------
# get_resume_params
# ---------------------------------------------------------------------------


def test_get_resume_params_stored_by_start(redis):
    """start(redis, 334, depth='nascente', geo_id=294280, target_max_pages=5) →
    get_resume_params returns {'depth': 'nascente', 'geo_id': 294280, 'max_pages': 5}."""
    sweep_progress.start(redis, 334, depth="nascente", geo_id=294280, target_max_pages=5)
    params = sweep_progress.get_resume_params(redis)
    assert params["depth"] == "nascente"
    assert params["geo_id"] == 294280
    assert params["max_pages"] == 5


def test_get_resume_params_defaults_when_absent(redis):
    """Fresh FakeRedis → get_resume_params returns {'depth': None, 'geo_id': 294280, 'max_pages': 334}."""
    params = sweep_progress.get_resume_params(redis)
    assert params["depth"] is None
    assert params["geo_id"] == 294280
    assert params["max_pages"] == 334


# ---------------------------------------------------------------------------
# start() new kwargs stored in hash
# ---------------------------------------------------------------------------


def test_start_stores_depth_geo_id_max_pages(redis):
    """start() with new kwargs → HGETALL includes 'depth', 'geo_id', 'target_max_pages' fields."""
    sweep_progress.start(redis, 334, depth="nascente", geo_id=294280, target_max_pages=5)
    raw = redis.hgetall(sweep_progress._PROGRESS_KEY)
    keys = {k.decode() if isinstance(k, bytes) else k for k in raw}
    assert "depth" in keys
    assert "geo_id" in keys
    assert "target_max_pages" in keys


def test_start_without_new_kwargs_omits_fields(redis):
    """start(redis, 334) without new kwargs → hash does NOT contain 'depth'/'geo_id'/'target_max_pages'.

    The {k:v for ... if v is not None} filter and None default for geo_id must hold.
    """
    sweep_progress.start(redis, 334)
    raw = redis.hgetall(sweep_progress._PROGRESS_KEY)
    keys = {k.decode() if isinstance(k, bytes) else k for k in raw}
    assert "depth" not in keys
    assert "geo_id" not in keys
    assert "target_max_pages" not in keys
