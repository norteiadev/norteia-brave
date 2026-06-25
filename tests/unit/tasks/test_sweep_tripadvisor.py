"""Unit tests for sweep_tripadvisor fail-fast session error behaviour (plan 12-04).

Tests verify:
  - SessionMissingError → immediate return, no self.retry called, needs_bootstrap key set
  - SessionExpiredError mid-sweep → immediate return, no retry, needs_bootstrap key set
  - Generic RuntimeError → self.retry IS called (regression)
  - SessionMissingError does NOT create a PoisonQuarantine row

All tests run 100% offline (fakeredis, no DB needed, no real TripAdvisor calls).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest

from brave.lanes.tripadvisor.client import SessionExpiredError, SessionMissingError

# The Redis key that sweep_tripadvisor sets when it fails fast on session errors.
_TA_NEEDS_BOOTSTRAP_KEY = "brave:ta:needs_bootstrap"


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
    """Helper: patch pipeline to use a stub TripAdvisorClient + fakeredis, run sweep.

    Patching strategy:
    - patch 'brave.lanes.tripadvisor.client.TripAdvisorClient' so that the lazy
      `from brave.lanes.tripadvisor.client import TripAdvisorClient` in pipeline.py
      gets our stub class.
    - patch AppConfig to return run_real_externals=True so the real-client branch runs.
    - patch redis.from_url to return fakeredis (for both the client and _mark_needs_bootstrap).
    - patch _get_session (SQLAlchemy factory) to return mock DB session/engine.
    - patch load_ibge_csv to return empty list.
    - patch TripAdvisorDestinosIngest and TripAdvisorAtrativosIngest so that their
      produce() raises the exception from the stub client directly.
    """
    # Build a mock AppConfig with run_real_externals=True
    mock_app_config = MagicMock()
    mock_app_config.run_real_externals = True

    # Build a mock ScoreConfig
    mock_score_config = MagicMock()

    # Build mock DB session / engine (SQLAlchemy _get_session factory)
    mock_db_session = MagicMock()
    mock_db_session.execute.return_value = MagicMock(all=lambda: [])
    mock_db_engine = MagicMock()

    # Build stub TA client instance
    stub_client = stub_client_class()

    # Patch TripAdvisorClient at the source module so the local import in pipeline.py gets it
    monkeypatch.setattr(
        "brave.lanes.tripadvisor.client.TripAdvisorClient",
        stub_client_class,
    )

    # Patch AppConfig and ScoreConfig constructors
    monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: mock_app_config)
    monkeypatch.setattr("brave.tasks.pipeline.ScoreConfig", lambda: mock_score_config)

    # Patch redis.from_url to return fakeredis
    monkeypatch.setattr("redis.from_url", lambda url, **kw: fake_redis)

    # Patch os.environ for Redis URL
    monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")

    # Patch _get_session (SQLAlchemy session factory)
    monkeypatch.setattr(
        "brave.tasks.pipeline._get_session",
        lambda: (mock_db_session, mock_db_engine),
    )

    # Patch TripAdvisorConfig
    from brave.config.settings import TripAdvisorConfig  # noqa: PLC0415

    mock_ta_config = MagicMock(spec=TripAdvisorConfig)
    monkeypatch.setattr(
        "brave.config.settings.TripAdvisorConfig",
        lambda: mock_ta_config,
    )

    # Patch load_ibge_csv to return empty list
    monkeypatch.setattr(
        "brave.lanes.tripadvisor.ibge.load_ibge_csv",
        lambda path: [],
    )

    # Patch the destinos and atrativos produce() to actually call the stub client
    # and propagate its errors — this is what the real produce() would do.
    async def _stub_produce(uf, run_rio=True):
        # Call fetch_destinations to propagate stub errors
        await stub_client.fetch_destinations()

    mock_destinos_ingest = MagicMock()
    mock_destinos_ingest.produce = AsyncMock(side_effect=_stub_produce)

    mock_atrativos_ingest = MagicMock()
    mock_atrativos_ingest.produce = AsyncMock(side_effect=_stub_produce)

    monkeypatch.setattr(
        "brave.lanes.tripadvisor.destinos.TripAdvisorDestinosIngest",
        lambda **kw: mock_destinos_ingest,
    )
    monkeypatch.setattr(
        "brave.lanes.tripadvisor.atrativos.TripAdvisorAtrativosIngest",
        lambda **kw: mock_atrativos_ingest,
    )

    # Patch NominatimGeocoderClient (TA-15 wiring) so the guard doesn't fire
    # in unit tests where RUN_REAL_EXTERNALS is not set in the environment.
    from brave.clients.null_nominatim import NullGeocoderClient  # noqa: PLC0415
    monkeypatch.setattr(
        "brave.clients.nominatim.NominatimGeocoderClient",
        lambda config, redis: NullGeocoderClient(),
    )

    # Build mock Celery task self
    mock_self = MagicMock()
    mock_self.MaxRetriesExceededError = type("MaxRetriesExceededError", (Exception,), {})

    retry_calls = []

    def _recording_retry(exc=None, max_retries=None):
        retry_calls.append(exc)
        raise mock_self.MaxRetriesExceededError("max retries exceeded")

    mock_self.retry.side_effect = _recording_retry

    from brave.tasks.pipeline import sweep_tripadvisor  # noqa: PLC0415

    # For bind=True Celery tasks, the raw function is at __wrapped__.__func__.
    # Calling sweep_tripadvisor.run() uses the task's own self (no mock injection).
    # We need __func__ to inject our mock_self that records retry calls.
    raw_fn = sweep_tripadvisor.__wrapped__.__func__

    try:
        raw_fn(mock_self, uf="BA")
    except Exception:
        pass  # Expected for retry paths / MaxRetriesExceededError

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

        with patch("brave.core.quarantine.quarantine_poison") as mock_q:
            mock_q.side_effect = lambda **kw: quarantine_calls.append(kw)
            _run_sweep_with_stub_client(_StubMissingSessionClient, fake_redis, monkeypatch)

        assert len(quarantine_calls) == 0, (
            f"quarantine_poison should NOT be called for SessionMissingError, "
            f"but was called {len(quarantine_calls)} time(s)"
        )
