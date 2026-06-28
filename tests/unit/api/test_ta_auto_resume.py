"""TDD RED tests for TripAdvisor inject auto-resume hook + ta_resume_watch task (260628-m1n).

Tests:
  - inject_session calls maybe_resume_bulk_sweep after canary passes
  - inject_session swallows exceptions from maybe_resume_bulk_sweep (best-effort)
  - ta_resume_watch task calls maybe_resume_bulk_sweep

All tests are 100% offline (fakeredis, no real TripAdvisor calls).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest

# Ensure fakeredis mode
os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

BEARER = "test-bearer-token-auto-resume"

_VALID_BODY = {
    "cookies": {"datadome": "x"},
    "query_ids": {"destinations": "abc123def456abcd"},
    "user_agent": "Mozilla/5.0",
    "acquired_at": "2026-06-28T12:00:00Z",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", "test-steward-auto-resume")
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")


@pytest.fixture
def fake_redis():
    """Fresh FakeRedis per test."""
    return fakeredis.FakeRedis()


@pytest.fixture
def authed_client(monkeypatch, fake_redis):
    """TestClient with fakeredis + auth + DB overridden for happy-path tests."""
    from brave.api.deps import get_db, get_redis, require_steward_or_bearer
    from brave.api.main import app
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[require_steward_or_bearer] = lambda: None
    app.dependency_overrides[get_db] = lambda: None
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Inject hook calls maybe_resume_bulk_sweep post-canary
# ---------------------------------------------------------------------------


def test_inject_calls_auto_resume_when_canary_passes(authed_client, fake_redis, monkeypatch):
    """POST valid session body → 200; maybe_resume_bulk_sweep called once.

    The patch target is "brave.api.routers.tripadvisor_session.maybe_resume_bulk_sweep"
    because the module-level import (per plan_check_correction) makes that binding
    interceptable.
    """
    import brave.api.routers.tripadvisor_session as ts_module

    # Bypass canary — it's async
    async def _noop_canary(session, ta_config, redis):
        pass

    monkeypatch.setattr(ts_module, "_run_canary", _noop_canary)

    mock_resume = MagicMock(return_value=True)
    monkeypatch.setattr(ts_module, "maybe_resume_bulk_sweep", mock_resume)

    resp = authed_client.post("/api/v1/tripadvisor/session", json=_VALID_BODY)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    mock_resume.assert_called_once()


def test_inject_auto_resume_exception_does_not_break_inject(authed_client, fake_redis, monkeypatch):
    """Exception in maybe_resume_bulk_sweep is swallowed; inject still returns 200."""
    import brave.api.routers.tripadvisor_session as ts_module

    async def _noop_canary(session, ta_config, redis):
        pass

    monkeypatch.setattr(ts_module, "_run_canary", _noop_canary)

    def _raise_error(redis):
        raise RuntimeError("broker down")

    monkeypatch.setattr(ts_module, "maybe_resume_bulk_sweep", _raise_error)

    resp = authed_client.post("/api/v1/tripadvisor/session", json=_VALID_BODY)
    assert resp.status_code == 200, f"Exception should be swallowed; got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# ta_resume_watch task
# ---------------------------------------------------------------------------


def test_ta_resume_watch_calls_maybe_resume(monkeypatch):
    """ta_resume_watch.run() calls maybe_resume_bulk_sweep.

    Patch target is "brave.lanes.tripadvisor.resume.maybe_resume_bulk_sweep"
    because ta_resume_watch does a lazy import:
      from brave.lanes.tripadvisor.resume import maybe_resume_bulk_sweep
    which binds the name in the resume module namespace. Patching
    brave.tasks.pipeline.maybe_resume_bulk_sweep has no effect on that binding.
    """
    import brave.tasks.pipeline as pipeline_module

    # Ensure ta_resume_watch is importable (GREEN will add it)
    ta_resume_watch = pipeline_module.ta_resume_watch

    redis_instance = fakeredis.FakeRedis()

    def _fake_from_url(url):
        return redis_instance

    import redis as _redis_lib
    monkeypatch.setattr(_redis_lib, "from_url", _fake_from_url)

    called = []

    def _fake_maybe_resume(redis):
        called.append(redis)
        return False

    monkeypatch.setattr(
        "brave.lanes.tripadvisor.resume.maybe_resume_bulk_sweep",
        _fake_maybe_resume,
    )

    # Run the task synchronously (no Celery worker needed)
    ta_resume_watch.run()

    assert len(called) == 1
