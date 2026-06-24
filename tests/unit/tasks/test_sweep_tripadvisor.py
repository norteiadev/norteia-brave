"""Unit tests for sweep_tripadvisor fail-fast session error behaviour (plan 12-04).

Tests verify:
  - SessionMissingError → immediate return, no self.retry called, needs_bootstrap key set
  - SessionExpiredError mid-sweep → immediate return, no retry, needs_bootstrap key set
  - Generic RuntimeError → self.retry IS called (regression)
  - SessionMissingError does NOT create a PoisonQuarantine row

All tests run 100% offline (fakeredis, no DB needed, no real TripAdvisor calls).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from brave.lanes.tripadvisor.client import SessionExpiredError, SessionMissingError

# The Redis key that sweep_tripadvisor sets when it fails fast on session errors.
_TA_NEEDS_BOOTSTRAP_KEY = "brave:ta:needs_bootstrap"


def _make_sweep_task():
    """Return the sweep_tripadvisor Celery task function (bound to a mock self)."""
    from brave.tasks.pipeline import sweep_tripadvisor  # noqa: PLC0415

    # Celery bind=True means sweep_tripadvisor is a Task class; get the run function.
    return sweep_tripadvisor


def _make_mock_self(fake_redis_url: str) -> MagicMock:
    """Create a mock Celery task `self` that records retry attempts."""
    mock_self = MagicMock()
    # retry should raise the Retry exception — but we assert it is NOT called on session errors.
    mock_self.retry.side_effect = Exception("self.retry should not have been called")
    mock_self.MaxRetriesExceededError = Exception
    return mock_self


class _StubMissingSessionClient:
    """TripAdvisorClient-shaped stub that raises SessionMissingError on first use."""

    def __init__(self, *args, **kwargs):
        pass

    def _get_session(self):
        raise SessionMissingError("no session in Redis")

    async def fetch_destinations(self, *args, **kwargs):
        raise SessionMissingError("no session in Redis")

    async def fetch_attractions(self, *args, **kwargs):
        raise SessionMissingError("no session in Redis")


class _StubExpiredSessionClient:
    """Stub client that raises SessionExpiredError on fetch_destinations (mid-sweep expiry)."""

    def __init__(self, *args, **kwargs):
        pass

    def _get_session(self):
        return {"cookies": {}, "query_ids": {}, "user_agent": "", "acquired_at": ""}

    async def fetch_destinations(self, *args, **kwargs):
        raise SessionExpiredError("403 DataDome block")

    async def fetch_attractions(self, *args, **kwargs):
        raise SessionExpiredError("403 DataDome block")


class _StubGenericErrorClient:
    """Stub client that raises a generic RuntimeError (non-session error)."""

    def __init__(self, *args, **kwargs):
        pass

    def _get_session(self):
        return {"cookies": {}, "query_ids": {}, "user_agent": "", "acquired_at": ""}

    async def fetch_destinations(self, *args, **kwargs):
        raise RuntimeError("unexpected network error")

    async def fetch_attractions(self, *args, **kwargs):
        raise RuntimeError("unexpected network error")


def _run_sweep_with_stub_client(stub_client_class, fake_redis, monkeypatch):
    """Helper: patch pipeline to use a stub TripAdvisorClient + fakeredis, run sweep."""
    # Patch TripAdvisorClient import inside pipeline.py
    monkeypatch.setattr(
        "brave.tasks.pipeline.TripAdvisorClient",
        stub_client_class,
        raising=False,
    )
    # Force run_real_externals=True so the real-client branch runs (with our stub)
    from brave.config.settings import AppConfig  # noqa: PLC0415

    mock_app_config = MagicMock(spec=AppConfig)
    mock_app_config.run_real_externals = True

    # Patch AppConfig() call inside task to return our mock
    monkeypatch.setattr(
        "brave.tasks.pipeline.AppConfig",
        lambda: mock_app_config,
    )

    # Patch redis.from_url to return fakeredis
    monkeypatch.setattr(
        "redis.from_url",
        lambda url, **kw: fake_redis,
    )

    # Patch os.environ for Redis URL
    monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")

    # Patch _get_session (the SQLAlchemy session factory, not the TA session)
    mock_db_session = MagicMock()
    mock_db_session.__enter__ = lambda s: s
    mock_db_session.__exit__ = MagicMock(return_value=False)
    mock_db_engine = MagicMock()

    monkeypatch.setattr(
        "brave.tasks.pipeline._get_session",
        lambda: (mock_db_session, mock_db_engine),
    )

    # Patch TripAdvisorConfig
    from brave.config.settings import TripAdvisorConfig  # noqa: PLC0415

    monkeypatch.setattr(
        "brave.tasks.pipeline.TripAdvisorConfig",
        MagicMock(return_value=MagicMock(spec=TripAdvisorConfig)),
        raising=False,
    )

    # Patch load_ibge_csv to return empty list (no IBGE records needed)
    monkeypatch.setattr(
        "brave.lanes.tripadvisor.ibge.load_ibge_csv",
        lambda path: [],
        raising=False,
    )

    # Build mock self
    mock_self = MagicMock()
    mock_self.MaxRetriesExceededError = type("MaxRetriesExceededError", (Exception,), {})

    retry_calls = []

    def _recording_retry(exc=None, max_retries=None):
        retry_calls.append(exc)
        raise mock_self.MaxRetriesExceededError("max retries exceeded")

    mock_self.retry.side_effect = _recording_retry

    sweep = _make_sweep_task()

    try:
        sweep.run(mock_self, uf="BA")
    except Exception:
        pass  # Expected for retry paths

    return mock_self, retry_calls


class TestSweepTripAdvisorSessionFailFast:
    """sweep_tripadvisor must exit immediately on session errors without retrying."""

    def test_missing_session_fails_fast_no_retry(self, monkeypatch):
        """SessionMissingError → sweep returns, self.retry is NOT called."""
        fake_redis = fakeredis.FakeRedis()
        mock_self, retry_calls = _run_sweep_with_stub_client(
            _StubMissingSessionClient, fake_redis, monkeypatch
        )
        assert len(retry_calls) == 0, (
            f"self.retry should not be called on SessionMissingError, "
            f"but it was called {len(retry_calls)} time(s)"
        )

    def test_missing_session_marks_needs_bootstrap(self, monkeypatch):
        """After SessionMissingError, needs_bootstrap Redis key is set."""
        fake_redis = fakeredis.FakeRedis()
        _run_sweep_with_stub_client(_StubMissingSessionClient, fake_redis, monkeypatch)
        val = fake_redis.get(_TA_NEEDS_BOOTSTRAP_KEY)
        assert val is not None, (
            f"Expected '{_TA_NEEDS_BOOTSTRAP_KEY}' key in Redis after SessionMissingError, "
            f"but key was not set"
        )

    def test_session_expired_mid_sweep_stops(self, monkeypatch):
        """SessionExpiredError mid-sweep → sweep returns, no retry, needs_bootstrap set."""
        fake_redis = fakeredis.FakeRedis()
        mock_self, retry_calls = _run_sweep_with_stub_client(
            _StubExpiredSessionClient, fake_redis, monkeypatch
        )
        assert len(retry_calls) == 0, (
            f"self.retry should not be called on SessionExpiredError, "
            f"but it was called {len(retry_calls)} time(s)"
        )
        val = fake_redis.get(_TA_NEEDS_BOOTSTRAP_KEY)
        assert val is not None, (
            f"Expected '{_TA_NEEDS_BOOTSTRAP_KEY}' key set after SessionExpiredError"
        )

    def test_normal_exception_still_retries(self, monkeypatch):
        """Generic RuntimeError still triggers self.retry (existing retry unchanged)."""
        fake_redis = fakeredis.FakeRedis()
        mock_self, retry_calls = _run_sweep_with_stub_client(
            _StubGenericErrorClient, fake_redis, monkeypatch
        )
        assert len(retry_calls) == 1, (
            f"self.retry should be called once for a RuntimeError, "
            f"but was called {len(retry_calls)} time(s)"
        )

    def test_session_missing_error_not_quarantined(self, monkeypatch):
        """SessionMissingError does NOT create a PoisonQuarantine row."""
        fake_redis = fakeredis.FakeRedis()
        quarantine_calls = []

        original_quarantine = None
        try:
            from brave.core.quarantine import quarantine_poison  # noqa: PLC0415
            original_quarantine = quarantine_poison
        except ImportError:
            pass

        with patch("brave.core.quarantine.quarantine_poison") as mock_q:
            mock_q.side_effect = lambda **kw: quarantine_calls.append(kw)
            _run_sweep_with_stub_client(_StubMissingSessionClient, fake_redis, monkeypatch)

        assert len(quarantine_calls) == 0, (
            f"quarantine_poison should NOT be called for SessionMissingError, "
            f"but was called {len(quarantine_calls)} time(s)"
        )
