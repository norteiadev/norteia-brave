"""TDD RED tests for maybe_resume_bulk_sweep (plan 260628-m1n).

Pure unit tests — fakeredis only, no TestClient, no app fixture.
Tests all branches of the idempotent resume helper including the self-heal
path on dispatch failure.

All tests are 100% offline (fakeredis, no real TripAdvisor calls).
"""

from __future__ import annotations

import fakeredis
import pytest

import brave.tasks.pipeline as pipeline_module
from brave.lanes.tripadvisor import sweep_progress
from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY
from brave.lanes.tripadvisor.resume import maybe_resume_bulk_sweep


def _setup_paused_with_session(redis, *, depth="nascente", geo_id=294280, max_pages=334):
    """Helper: seed a stopped_needs_bootstrap state + session key."""
    sweep_progress.start(redis, pages_total=334, depth=depth, geo_id=geo_id, target_max_pages=max_pages)
    sweep_progress.stop_needs_bootstrap(redis)
    redis.set(BRAVE_TA_SESSION_KEY, '{"cookies": {"d": "x"}}', ex=3600)


# ---------------------------------------------------------------------------
# False-return paths (preconditions not met)
# ---------------------------------------------------------------------------


def test_maybe_resume_returns_false_when_not_paused():
    """Fresh FakeRedis (no hash) → maybe_resume_bulk_sweep returns False; delay NOT called."""
    redis = fakeredis.FakeRedis()
    result = maybe_resume_bulk_sweep(redis)
    assert result is False


def test_maybe_resume_returns_false_when_paused_no_session():
    """stopped_needs_bootstrap state but no BRAVE_TA_SESSION_KEY → returns False."""
    redis = fakeredis.FakeRedis()
    sweep_progress.start(redis, pages_total=334)
    sweep_progress.stop_needs_bootstrap(redis)
    # No session key set
    result = maybe_resume_bulk_sweep(redis)
    assert result is False


def test_maybe_resume_returns_false_when_running():
    """State=RUNNING (not stopped) + session present → returns False."""
    redis = fakeredis.FakeRedis()
    sweep_progress.start(redis, pages_total=334)
    redis.set(BRAVE_TA_SESSION_KEY, '{"cookies": {"d": "x"}}', ex=3600)
    result = maybe_resume_bulk_sweep(redis)
    assert result is False


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_maybe_resume_dispatches_when_paused_with_session(monkeypatch):
    """Paused state + session → returns True; delay called once with bulk_national=True."""
    redis = fakeredis.FakeRedis()
    _setup_paused_with_session(redis)

    delayed = []
    monkeypatch.setattr(pipeline_module.sweep_tripadvisor, "delay", lambda *a, **kw: delayed.append((a, kw)))

    result = maybe_resume_bulk_sweep(redis)

    assert result is True
    assert len(delayed) == 1
    _args, _kwargs = delayed[0]
    assert _args[0] == "BR"
    assert _kwargs.get("bulk_national") is True


def test_maybe_resume_passes_stored_params_to_delay(monkeypatch):
    """Params stored by start() are passed to delay: depth, geo_id, max_pages."""
    redis = fakeredis.FakeRedis()
    _setup_paused_with_session(redis, depth="nascente", geo_id=294280, max_pages=5)

    delayed = []
    monkeypatch.setattr(pipeline_module.sweep_tripadvisor, "delay", lambda *a, **kw: delayed.append((a, kw)))

    maybe_resume_bulk_sweep(redis)

    assert len(delayed) == 1
    _args, _kwargs = delayed[0]
    assert _args[0] == "BR"
    assert _args[1] == "nascente"
    assert _kwargs["geo_id"] == 294280
    assert _kwargs["max_pages"] == 5


def test_maybe_resume_clears_needs_bootstrap_key(monkeypatch):
    """After successful dispatch, TA_NEEDS_BOOTSTRAP_KEY is deleted."""
    from brave.lanes.tripadvisor.resume import TA_NEEDS_BOOTSTRAP_KEY

    redis = fakeredis.FakeRedis()
    _setup_paused_with_session(redis)
    redis.set(TA_NEEDS_BOOTSTRAP_KEY, "1")

    monkeypatch.setattr(pipeline_module.sweep_tripadvisor, "delay", lambda *a, **kw: None)

    maybe_resume_bulk_sweep(redis)

    assert redis.exists(TA_NEEDS_BOOTSTRAP_KEY) == 0


# ---------------------------------------------------------------------------
# Race safety
# ---------------------------------------------------------------------------


def test_maybe_resume_race_second_caller_returns_false(monkeypatch):
    """Two calls with same fakeredis: first=True, second=False (claim_resume consumed by first)."""
    redis = fakeredis.FakeRedis()
    _setup_paused_with_session(redis)

    monkeypatch.setattr(pipeline_module.sweep_tripadvisor, "delay", lambda *a, **kw: None)

    first = maybe_resume_bulk_sweep(redis)
    second = maybe_resume_bulk_sweep(redis)

    assert first is True
    assert second is False


# ---------------------------------------------------------------------------
# Self-heal on dispatch failure
# ---------------------------------------------------------------------------


def test_maybe_resume_resets_state_on_dispatch_failure(monkeypatch):
    """Dispatch raises RuntimeError → state resets to stopped_needs_bootstrap;
    claim key deleted so next caller can re-acquire; exception is re-raised."""
    redis = fakeredis.FakeRedis()
    _setup_paused_with_session(redis)

    def _raise(*a, **kw):
        raise RuntimeError("broker down")

    monkeypatch.setattr(pipeline_module.sweep_tripadvisor, "delay", _raise)

    with pytest.raises(RuntimeError, match="broker down"):
        maybe_resume_bulk_sweep(redis)

    # State must reset so the next trigger can retry
    assert sweep_progress.is_paused_needs_bootstrap(redis) is True
    # Claim key must be deleted so SETNX can be re-acquired
    assert redis.exists(sweep_progress._RESUME_CLAIM_KEY) == 0
