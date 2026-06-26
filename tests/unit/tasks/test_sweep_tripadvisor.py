"""Unit tests for sweep_tripadvisor fail-fast session error behaviour (plan 12-04).

Tests verify:
  - SessionMissingError → immediate return, no self.retry called, needs_bootstrap key set
  - SessionExpiredError mid-sweep → immediate return, no retry, needs_bootstrap key set
  - Generic RuntimeError → self.retry IS called (regression)
  - SessionMissingError does NOT create a PoisonQuarantine row

All tests run 100% offline (fakeredis, no DB needed, no real TripAdvisor calls).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis

from brave.config.settings import ScoreConfig
from brave.lanes.tripadvisor import sweep_progress
from brave.lanes.tripadvisor.client import SessionExpiredError, SessionMissingError
from brave.lanes.tripadvisor.ibge import IbgeMunicipio
from tests.fakes.fake_nominatim import FakeGeocoderClient
from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

# The Redis key that sweep_tripadvisor sets when it fails fast on session errors.
_TA_NEEDS_BOOTSTRAP_KEY = "brave:ta:needs_bootstrap"

# All-Brazil geoId driven by the bulk national branch (Phase 15, TA-12).
_GEO_ID_BR = 294280


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

    def test_per_uf_session_expiry_no_unbound_local_error(self, monkeypatch):
        """REGRESSION (T-15-07-04): the per-UF (bulk_national=False) path reaches the
        SHARED fail-fast except with rc still None. The guarded
        `if rc is not None: sweep_progress.stop_needs_bootstrap(rc)` must keep it from
        raising UnboundLocalError — the task returns cleanly and NEVER writes the bulk
        progress hash (its state stays idle, proving the bulk write was guarded out).
        """
        fake_redis = fakeredis.FakeRedis()
        # If the rc guard regressed, the shared except would raise UnboundLocalError
        # here. The existing helper swallows exceptions, so assert the behavioural
        # fingerprint instead: needs_bootstrap set + bulk progress NEVER touched.
        mock_self, retry_calls = _run_sweep_with_stub_client(
            _StubExpiredSessionClient, fake_redis, monkeypatch
        )
        assert len(retry_calls) == 0, "per-UF SessionExpiredError must not retry"
        assert fake_redis.get(_TA_NEEDS_BOOTSTRAP_KEY) is not None
        # rc was None on the per-UF path → stop_needs_bootstrap was guarded out →
        # the progress hash was never written → state is the absent-hash default.
        assert sweep_progress.get_progress(fake_redis)["state"] == "idle", (
            "the per-UF path must NOT write the bulk sweep progress hash "
            "(guarded `if rc is not None` skipped stop_needs_bootstrap)"
        )


# ---------------------------------------------------------------------------
# Bulk national branch (Phase 15, TA-12) — resume + fail-fast + done-state.
# ---------------------------------------------------------------------------


# Uberlândia (MG) IBGE seat — the bulk-path target município for geocode resolution.
_IBGE_RECORDS = [
    IbgeMunicipio("3170107", "Uberlândia", "MG", -18.9186, -48.2772),
]


def _make_config() -> ScoreConfig:
    return ScoreConfig(
        weight_origem=30.0,
        weight_completude=20.0,
        weight_corroboracao=20.0,
        weight_atualidade=15.0,
        weight_validacao_humana=15.0,
        threshold_mar=85.0,
        threshold_dlq=40.0,
        score_version="v1.1",
        mar_ready_atualidade_bar=70.0,
        mar_ready_corrob_bar=60.0,
    )


def _make_card(location_id: int, name: str = "Parque do Sabiá") -> dict[str, Any]:
    """A normalized AttractionsFusion listing card (no lat/lng — geocoded nationally)."""
    return {
        "locationId": location_id,
        "name": name,
        "review_count": 100,
        "rating": 4.0,
        "category": "Parks",
    }


def _geo_near_uberlandia() -> dict[str, Any]:
    return {"lat": -18.92, "lon": -48.28, "osm_id": 1, "municipio_name": "Uberlândia"}


def _resolvable_geo_fixture(location_ids: list[str]) -> dict[str, dict[str, Any]]:
    """National-geocode fixture resolving every id near the Uberlândia IBGE seat."""
    return {lid: _geo_near_uberlandia() for lid in location_ids}


class _RaisingPaginatedClient:
    """Fake TA client whose paginated iterator raises SessionExpiredError mid-run.

    Yields `pages_before_raise` single-card pages, then raises — modelling a mid-run
    DataDome/session expiry (403/429) after some pages have already been ingested.
    """

    def __init__(self, *args, pages_before_raise: int = 1, **kwargs) -> None:
        self._pages_before_raise = pages_before_raise

    async def fetch_attractions_paginated(
        self, geo_id: int, start_page: int = 1, max_pages: int = 334
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        for i in range(self._pages_before_raise):
            yield i * 30, [_make_card(location_id=1000 + i)]
        raise SessionExpiredError("TripAdvisor HTML returned 403 — session expired.")


def _run_bulk_sweep(
    *,
    fake_client,
    fake_geo,
    fake_redis,
    monkeypatch,
    start_page: int = 1,
    max_pages: int | None = None,
    pre_seed=None,
):
    """Run sweep_tripadvisor's BULK branch end-to-end offline.

    Uses the REAL TripAdvisorAtrativosIngest.produce_paginated (not mocked) so the
    branch's resume/progress/fail-fast wiring is exercised. store_raw +
    process_nascente_record are patched (no DB). redis.from_url → the shared fakeredis,
    so `rc` and the progress hash are the same instance the asserts read.
    """
    mock_app_config = MagicMock()
    mock_app_config.run_real_externals = True

    mock_db_session = MagicMock()
    mock_db_engine = MagicMock()

    # Real-client branch picks up our fake (ignores config/redis kwargs).
    monkeypatch.setattr(
        "brave.lanes.tripadvisor.client.TripAdvisorClient",
        lambda **kw: fake_client,
    )
    monkeypatch.setattr(
        "brave.clients.nominatim.NominatimGeocoderClient",
        lambda config, redis: fake_geo,
    )
    monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: mock_app_config)
    monkeypatch.setattr("brave.tasks.pipeline.ScoreConfig", _make_config)
    monkeypatch.setattr("redis.from_url", lambda url, **kw: fake_redis)
    monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(
        "brave.tasks.pipeline._get_session",
        lambda: (mock_db_session, mock_db_engine),
    )

    from brave.config.settings import TripAdvisorConfig  # noqa: PLC0415

    monkeypatch.setattr(
        "brave.config.settings.TripAdvisorConfig",
        lambda: MagicMock(spec=TripAdvisorConfig),
    )
    monkeypatch.setattr(
        "brave.lanes.tripadvisor.ibge.load_ibge_csv",
        lambda path: _IBGE_RECORDS,
    )

    if pre_seed is not None:
        pre_seed(fake_redis)

    mock_self = MagicMock()
    mock_self.MaxRetriesExceededError = type("MaxRetriesExceededError", (Exception,), {})
    retry_calls = []

    def _recording_retry(exc=None, max_retries=None):
        retry_calls.append(exc)
        raise mock_self.MaxRetriesExceededError("max retries exceeded")

    mock_self.retry.side_effect = _recording_retry

    from brave.tasks.pipeline import sweep_tripadvisor  # noqa: PLC0415

    raw_fn = sweep_tripadvisor.__wrapped__.__func__

    with (
        patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
        patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
    ):
        mock_store_raw.return_value = MagicMock(id=uuid.uuid4())
        # NOTE: do NOT swallow — an UnboundLocalError regression must fail the test.
        raw_fn(
            mock_self,
            "BR",
            None,
            bulk_national=True,
            start_page=start_page,
            max_pages=max_pages,
            geo_id=_GEO_ID_BR,
        )
        store_call_count = mock_store_raw.call_count

    return mock_self, retry_calls, store_call_count


class TestSweepTripAdvisorBulkNational:
    """The bulk national branch: happy-path done-state, fail-fast, and resume."""

    def test_bulk_happy_path_marks_done(self, monkeypatch):
        """2 pages × 30 cards → both ingested; progress state=done, pages_done=2."""
        page1 = [_make_card(location_id=10_000 + i) for i in range(30)]
        page2 = [_make_card(location_id=20_000 + i) for i in range(30)]
        all_ids = [str(c["locationId"]) for c in page1 + page2]
        fake_client = FakeTripAdvisorClient(
            fixture_pages={_GEO_ID_BR: [(0, page1), (30, page2)]}
        )
        fake_geo = FakeGeocoderClient(
            fixture_national_results=_resolvable_geo_fixture(all_ids)
        )
        fake_redis = fakeredis.FakeRedis()

        _, retry_calls, store_call_count = _run_bulk_sweep(
            fake_client=fake_client,
            fake_geo=fake_geo,
            fake_redis=fake_redis,
            monkeypatch=monkeypatch,
            start_page=1,
            max_pages=2,
        )

        assert retry_calls == [], "happy path must not retry"
        snap = sweep_progress.get_progress(fake_redis)
        assert snap["state"] == "done"
        assert snap["pages_done"] == 2
        assert snap["attractions_ingested"] == 60
        assert store_call_count == 60, "all 60 cards must reach Nascente (rows > 0)"

    def test_bulk_mid_run_expiry_stops_needs_bootstrap(self, monkeypatch):
        """Page-2 SessionExpiredError → stopped_needs_bootstrap, page-1 durable, no retry."""
        fake_geo = FakeGeocoderClient(
            fixture_national_results=_resolvable_geo_fixture(["1000"])
        )
        fake_redis = fakeredis.FakeRedis()

        _, retry_calls, store_call_count = _run_bulk_sweep(
            fake_client=_RaisingPaginatedClient(pages_before_raise=1),
            fake_geo=fake_geo,
            fake_redis=fake_redis,
            monkeypatch=monkeypatch,
            start_page=1,
            max_pages=334,
        )

        assert retry_calls == [], "session expiry must NOT retry (fail-fast)"
        assert fake_redis.get(_TA_NEEDS_BOOTSTRAP_KEY) is not None, "needs_bootstrap marker set"
        snap = sweep_progress.get_progress(fake_redis)
        assert snap["state"] == "stopped_needs_bootstrap"
        # Page 1 (offset 0) committed its single card BEFORE the page-2 raise → durable.
        assert store_call_count == 1, "page-1 record must remain durable (per-page commit)"
        assert snap["pages_done"] == 1
        assert sweep_progress.get_resume_offset(fake_redis) == 0

    def test_bulk_resume_starts_after_last_completed_offset(self, monkeypatch):
        """A re-run with last_completed_offset=30 calls produce_paginated at start_page 3."""
        fake_client = FakeTripAdvisorClient(fixture_pages={_GEO_ID_BR: []})  # yields nothing
        fake_geo = FakeGeocoderClient(fixture_national_results={})
        fake_redis = fakeredis.FakeRedis()

        def _seed(rc):
            # Prior run completed page 2 (offset 30) before stopping.
            sweep_progress.start(rc, pages_total=334)
            sweep_progress.record_page(rc, offset=30, ingested_delta=30)

        _run_bulk_sweep(
            fake_client=fake_client,
            fake_geo=fake_geo,
            fake_redis=fake_redis,
            monkeypatch=monkeypatch,
            start_page=1,  # ignored — resume offset takes precedence
            max_pages=334,
            pre_seed=_seed,
        )

        assert fake_client.paginated_calls, "produce_paginated must drive the client"
        # resume_offset=30 → start_page = 30 // 30 + 2 = 3 (the page AFTER offset 30).
        assert fake_client.paginated_calls[0]["start_page"] == 3, (
            "re-run must resume at the page after the last completed offset, not page 1"
        )
