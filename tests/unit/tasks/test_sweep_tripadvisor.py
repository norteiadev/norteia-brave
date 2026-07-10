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
    - patch load_ibge_municipios to return empty list.
    - patch TripAdvisorAtrativosIngest so that its produce() raises the exception from
      the stub client directly (destinos step removed — oa3).
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

    # Patch AppConfig constructor + the effective-config loader (score seam)
    monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: mock_app_config)
    monkeypatch.setattr(
        "brave.tasks.pipeline.load_effective_config",
        lambda session, redis=None: MagicMock(score=mock_score_config),
    )

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
        "brave.lanes.tripadvisor.ibge.load_ibge_municipios",
        lambda session: [],
    )

    # Patch the atrativos produce() to actually call the stub client
    # and propagate its errors (destinos step removed — oa3).
    async def _stub_produce(
        uf, run_rio=True, enrich_reviews=False, redis=None, max_per_uf=None
    ):
        # Errors originate from the atrativos path (no destinos step — oa3).
        # enrich_reviews accepted because the per-UF path now passes enrich_reviews=True.
        # redis accepted because the per-UF path now passes redis= for the mid-run
        # Motor Pausado/Desligado halt gate (engine.should_halt_producer).
        # max_per_uf accepted because the per-UF path now threads the operator cap.
        await stub_client.fetch_attractions(geo_id=0)

    mock_atrativos_ingest = MagicMock()
    mock_atrativos_ingest.produce = AsyncMock(side_effect=_stub_produce)

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


class TestSweepTripAdvisorPerUfDestinoBuild:
    """Per-UF destino_rio_map is built from ALL destination RioRecords, not source-filtered."""

    def test_per_uf_destino_rio_map_sourced_from_authoritative_rio(self, monkeypatch):
        """destino_rio_map is keyed by municipio_id from ANY destination RioRecord (Mtur etc).

        Simulates a Mtur destino already in Rio for BA/Salvador (ibge 2927408).
        Asserts atrativos ingest receives that map entry — proves the source=='tripadvisor'
        filter is gone and the oa3 fix is wired end-to-end.
        """
        import uuid as _uuid
        import asyncio as _asyncio

        fake_redis = fakeredis.FakeRedis()
        mtur_rio_id = _uuid.uuid4()
        mtur_source_ref = "mtur:BA:2927408"

        # Fake DB row simulating a Mtur destino RioRecord for Salvador/BA
        fake_row = MagicMock()
        fake_row.id = mtur_rio_id
        fake_row.source_ref = mtur_source_ref
        fake_row.municipio_id = "2927408"

        mock_db_session = MagicMock()
        mock_db_session.execute.return_value = MagicMock(all=lambda: [fake_row])
        mock_db_engine = MagicMock()

        captured: dict = {}

        class _CapturingAtrativosIngest:
            def __init__(self, **kw):
                captured["map"] = dict(kw.get("destino_rio_map") or {})

            async def produce(self, uf, *, run_rio=True, enrich_reviews=False, redis=None):
                pass  # no-op; constructor arg is what we assert

        mock_app_config = MagicMock()
        mock_app_config.run_real_externals = False  # uses NullTripAdvisorClient

        monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: mock_app_config)
        monkeypatch.setattr(
            "brave.tasks.pipeline.load_effective_config",
            lambda session, redis=None: MagicMock(),
        )
        monkeypatch.setattr("redis.from_url", lambda url, **kw: fake_redis)
        monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setattr(
            "brave.tasks.pipeline._get_session",
            lambda: (mock_db_session, mock_db_engine),
        )
        monkeypatch.setattr(
            "brave.lanes.tripadvisor.ibge.load_ibge_municipios",
            lambda session: [],
        )
        monkeypatch.setattr(
            "brave.lanes.tripadvisor.atrativos.TripAdvisorAtrativosIngest",
            lambda **kw: _CapturingAtrativosIngest(**kw),
        )

        mock_self = MagicMock()
        mock_self.MaxRetriesExceededError = type("MaxRetriesExceededError", (Exception,), {})
        mock_self.retry.side_effect = lambda **kw: mock_self.MaxRetriesExceededError()

        from brave.tasks.pipeline import sweep_tripadvisor  # noqa: PLC0415

        raw_fn = sweep_tripadvisor.__wrapped__.__func__
        try:
            raw_fn(mock_self, uf="BA")
        except Exception:
            pass  # MaxRetriesExceededError etc. are fine; we only check captured

        assert "2927408" in captured.get("map", {}), (
            f"destino_rio_map must contain Mtur ibge key '2927408'; "
            f"got keys: {list(captured.get('map', {}).keys())}"
        )
        assert captured["map"]["2927408"] == (mtur_rio_id, mtur_source_ref), (
            "Map entry must be (rio_id, source_ref) from the authoritative destino row"
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
        score_version="v1.1",
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

    async def fetch_attractions_paginated_gql(
        self, geo_id: int, start_page: int = 1, max_pages: int = 334
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        for i in range(self._pages_before_raise):
            yield i * 30, [_make_card(location_id=1000 + i)]
        raise SessionExpiredError("TripAdvisor GraphQL returned 403 — session expired.")


class _RecordingGqlClient:
    """Records the start_page passed to fetch_attractions_paginated_gql; yields nothing.

    Self-contained (independent of FakeTripAdvisorClient's internal call-recording
    attributes) so the resume assertion targets a stable, locally-owned surface.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.gql_calls: list[dict[str, Any]] = []

    async def fetch_attractions_paginated_gql(
        self, geo_id: int, start_page: int = 1, max_pages: int = 334
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        self.gql_calls.append(
            {"geo_id": geo_id, "start_page": start_page, "max_pages": max_pages}
        )
        return
        yield  # unreachable — marks this coroutine as an async generator


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
    monkeypatch.setattr(
        "brave.tasks.pipeline.load_effective_config",
        lambda session, redis=None: MagicMock(score=_make_config()),
    )
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
        "brave.lanes.tripadvisor.ibge.load_ibge_municipios",
        lambda session: _IBGE_RECORDS,
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
            gql_pages=[(0, page1), (30, page2)]
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
        fake_client = _RecordingGqlClient()  # records start_page; yields nothing
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

        assert fake_client.gql_calls, "produce_paginated must drive the gql client"
        # resume_offset=30 → start_page = 30 // 30 + 2 = 3 (the page AFTER offset 30).
        assert fake_client.gql_calls[0]["start_page"] == 3, (
            "re-run must resume at the page after the last completed offset, not page 1"
        )


# ---------------------------------------------------------------------------
# R1: session expiry turns the engine OFF (260629-e69)
# ---------------------------------------------------------------------------


class TestR1EngineOffOnSessionExpiry:
    """R1: when the sweep fails fast on SessionMissing/Expired, the engine latch
    must be turned OFF so the operator must inject a fresh session before re-starting.
    No auto-restart ever fires.
    """

    def test_r1_session_missing_disables_engine(self, monkeypatch):
        """SessionMissingError during per-UF sweep → engine latch set OFF + state=idle."""
        import fakeredis as _fr

        from brave.core import engine as collection_engine

        fake_redis = _fr.FakeRedis()
        # Seed engine as running (operator started a sweep)
        collection_engine.start_run(fake_redis, ufs_total=1)
        assert collection_engine.is_enabled(fake_redis), "precondition: engine enabled"

        _run_sweep_with_stub_client(_StubMissingSessionClient, fake_redis, monkeypatch)

        assert collection_engine.is_enabled(fake_redis) is False, (
            "R1: engine latch must be OFF after SessionMissingError"
        )
        assert collection_engine.get_state(fake_redis) == collection_engine.IDLE, (
            "R1: engine state must be idle after SessionMissingError"
        )
        assert collection_engine.get_mode(fake_redis) == collection_engine.DESLIGADO, (
            "R1: operator mode must be DESLIGADO — leaving it LIGADO while enabled=0 "
            "makes the topbar 'Ligar' a no-op (stuck UI)"
        )

    def test_r1_session_expired_disables_engine(self, monkeypatch):
        """SessionExpiredError during per-UF sweep → engine latch set OFF + state=idle."""
        import fakeredis as _fr

        from brave.core import engine as collection_engine

        fake_redis = _fr.FakeRedis()
        collection_engine.start_run(fake_redis, ufs_total=1)
        assert collection_engine.is_enabled(fake_redis), "precondition: engine enabled"

        _run_sweep_with_stub_client(_StubExpiredSessionClient, fake_redis, monkeypatch)

        assert collection_engine.is_enabled(fake_redis) is False, (
            "R1: engine latch must be OFF after SessionExpiredError"
        )
        assert collection_engine.get_state(fake_redis) == collection_engine.IDLE, (
            "R1: engine state must be idle after SessionExpiredError"
        )
        assert collection_engine.get_mode(fake_redis) == collection_engine.DESLIGADO, (
            "R1: operator mode must be DESLIGADO — leaving it LIGADO while enabled=0 "
            "makes the topbar 'Ligar' a no-op (stuck UI)"
        )


# ---------------------------------------------------------------------------
# T1: ta_config wired to TripAdvisorAtrativosIngest constructor in per-UF path
# ---------------------------------------------------------------------------


class TestSweepTripAdvisorTaConfig:
    """sweep_tripadvisor per-UF path must wire ta_config to TripAdvisorAtrativosIngest.

    Bug (260630-pfr #1): sweep_tripadvisor defined ta_config ONLY inside the
    `if run_real_externals:` block. The offline else-branch and the constructor
    call at the bottom of the per-UF path never saw ta_config — so fetch_attraction_geo
    (the ftx geo-linkage, plan 260630-ftx) was permanently dormant even in production.
    Fix: initialise ta_config=None before the if-block; pass ta_config=ta_config
    to the TripAdvisorAtrativosIngest constructor unconditionally.
    """

    def _run_per_uf_capture_ta_config(
        self,
        monkeypatch: Any,
        *,
        real_externals: bool,
    ) -> "tuple[Any, Any]":
        """Run sweep_tripadvisor per-UF path and return (captured_ta_config, sentinel).

        sentinel is the object() returned by our patched TripAdvisorConfig() when
        real_externals=True, or None when real_externals=False.
        """
        fake_redis = fakeredis.FakeRedis()
        mock_db_session = MagicMock()
        mock_db_session.execute.return_value = MagicMock(all=lambda: [])
        mock_db_engine = MagicMock()

        captured: dict[str, Any] = {}

        class _CapturingAtrativosIngest:
            def __init__(self, **kw: Any) -> None:
                captured.update(kw)

            async def produce(
                self,
                uf: str,
                *,
                run_rio: bool = True,
                enrich_reviews: bool = False,
                redis: Any = None,
                max_per_uf: int | None = None,
            ) -> None:
                # Record produce() kwargs so the per-UF enrich_reviews wiring is assertable.
                captured["produce_enrich_reviews"] = enrich_reviews
                captured["produce_run_rio"] = run_rio
                captured["produce_redis_passed"] = redis is not None
                captured["produce_max_per_uf"] = max_per_uf

        mock_app_config = MagicMock()
        mock_app_config.run_real_externals = real_externals

        monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: mock_app_config)
        monkeypatch.setattr(
            "brave.tasks.pipeline.load_effective_config",
            lambda session, redis=None: MagicMock(),
        )
        monkeypatch.setattr("redis.from_url", lambda url, **kw: fake_redis)
        monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setattr(
            "brave.tasks.pipeline._get_session",
            lambda: (mock_db_session, mock_db_engine),
        )
        monkeypatch.setattr(
            "brave.lanes.tripadvisor.ibge.load_ibge_municipios",
            lambda session: [],
        )
        # Patch TripAdvisorAtrativosIngest at module level so the lazy import in
        # pipeline.py picks up the capturing class.
        monkeypatch.setattr(
            "brave.lanes.tripadvisor.atrativos.TripAdvisorAtrativosIngest",
            lambda **kw: _CapturingAtrativosIngest(**kw),
        )

        sentinel: Any = None
        if real_externals:
            # Build a unique sentinel so we can assert identity, not equality.
            sentinel = object()
            monkeypatch.setattr(
                "brave.config.settings.TripAdvisorConfig",
                lambda: sentinel,
            )
            monkeypatch.setattr(
                "brave.lanes.tripadvisor.client.TripAdvisorClient",
                lambda **kw: MagicMock(),
            )
            from brave.clients.null_nominatim import NullGeocoderClient  # noqa: PLC0415
            monkeypatch.setattr(
                "brave.clients.nominatim.NominatimGeocoderClient",
                lambda config, redis: NullGeocoderClient(),
            )

        mock_self = MagicMock()
        mock_self.MaxRetriesExceededError = type("MaxRetriesExceededError", (Exception,), {})
        mock_self.retry.side_effect = lambda **kw: mock_self.MaxRetriesExceededError()

        from brave.tasks.pipeline import sweep_tripadvisor  # noqa: PLC0415

        raw_fn = sweep_tripadvisor.__wrapped__.__func__
        try:
            raw_fn(mock_self, uf="BA")
        except Exception:
            pass  # MaxRetriesExceededError or similar — only the captured kwargs matter

        self._last_captured = captured  # expose full capture for other assertions
        return captured.get("ta_config"), sentinel

    def test_per_uf_passes_enrich_reviews_true(self, monkeypatch: Any) -> None:
        """The per-UF sweep MUST call produce(enrich_reviews=True) so atualidade is
        populated. A regression dropping this kwarg would silently zero atualidade
        across every per-UF sweep (review-recency enrichment)."""
        self._run_per_uf_capture_ta_config(monkeypatch, real_externals=False)
        assert self._last_captured.get("produce_enrich_reviews") is True, (
            "per-UF sweep_tripadvisor must pass enrich_reviews=True to produce() "
            f"— got {self._last_captured.get('produce_enrich_reviews')!r}"
        )

    def test_passes_ta_config_when_real_externals(self, monkeypatch: Any) -> None:
        """With run_real_externals=True, TripAdvisorAtrativosIngest receives the
        TripAdvisorConfig instance (not None) — gate for fetch_attraction_geo ftx path."""
        ta_config_received, sentinel = self._run_per_uf_capture_ta_config(
            monkeypatch, real_externals=True
        )
        assert ta_config_received is sentinel, (
            f"expected ta_config to be the TripAdvisorConfig sentinel, "
            f"got {ta_config_received!r} — sweep_tripadvisor likely did not pass "
            "ta_config=ta_config to TripAdvisorAtrativosIngest"
        )

    def test_passes_ta_config_none_when_offline(self, monkeypatch: Any) -> None:
        """With run_real_externals=False (offline), TripAdvisorAtrativosIngest receives
        ta_config=None — the ftx guard (ta_config is not None) keeps geo-linkage dormant."""
        ta_config_received, _ = self._run_per_uf_capture_ta_config(
            monkeypatch, real_externals=False
        )
        assert ta_config_received is None, (
            f"expected ta_config=None in offline mode, got {ta_config_received!r}"
        )


# ---------------------------------------------------------------------------
# TA-lane description enrichment: sweep_tripadvisor dispatches enrich_description_task
# for every atrativo produce() ingested, at rio depths (run_rio), NOT at nascente-only.
# ---------------------------------------------------------------------------


class TestSweepTripAdvisorDescriptionDispatch:
    """The TA lane never enters the Places FSM chain, so sweep_tripadvisor is the ONLY
    place that kicks description enrichment for TA atrativos. It dispatches
    enrich_description_task for each Rio id produce() returns, at every rio depth."""

    def _run(self, monkeypatch, *, depth, produce_ids):
        fake_redis = fakeredis.FakeRedis()
        mock_db_session = MagicMock()
        mock_db_session.execute.return_value = MagicMock(all=lambda: [])

        class _StubIngest:
            def __init__(self, **kw: Any) -> None:
                pass

            async def produce(self, uf: str, **kw: Any) -> list[str]:
                # Return ids ONLY when run_rio (mirror the real producer: nascente-only
                # creates no Rio records, so no enrichment is dispatched).
                return list(produce_ids) if kw.get("run_rio") else []

        mock_app_config = MagicMock()
        mock_app_config.run_real_externals = False

        monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: mock_app_config)
        monkeypatch.setattr(
            "brave.tasks.pipeline.load_effective_config",
            lambda session, redis=None: MagicMock(),
        )
        monkeypatch.setattr("redis.from_url", lambda url, **kw: fake_redis)
        monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setattr(
            "brave.tasks.pipeline._get_session",
            lambda: (mock_db_session, MagicMock()),
        )
        monkeypatch.setattr(
            "brave.lanes.tripadvisor.ibge.load_ibge_municipios", lambda session: []
        )
        monkeypatch.setattr(
            "brave.lanes.tripadvisor.atrativos.TripAdvisorAtrativosIngest",
            lambda **kw: _StubIngest(**kw),
        )
        # Capture enrich_description_task dispatches.
        enrich_mock = MagicMock()
        monkeypatch.setattr(
            "brave.tasks.pipeline.enrich_description_task", enrich_mock
        )

        mock_self = MagicMock()
        mock_self.MaxRetriesExceededError = type("MRE", (Exception,), {})
        mock_self.retry.side_effect = lambda **kw: mock_self.MaxRetriesExceededError()

        from brave.tasks.pipeline import sweep_tripadvisor  # noqa: PLC0415

        raw_fn = sweep_tripadvisor.__wrapped__.__func__
        try:
            raw_fn(mock_self, uf="ES", depth=depth)
        except Exception:
            pass
        return enrich_mock

    def test_dispatches_enrich_for_each_id_at_nascente_rio(self, monkeypatch):
        ids = [str(uuid.uuid4()) for _ in range(3)]
        enrich_mock = self._run(monkeypatch, depth="nascente_rio", produce_ids=ids)
        dispatched = [c.args[0] for c in enrich_mock.delay.call_args_list]
        assert dispatched == ids, (
            "sweep_tripadvisor must dispatch enrich_description_task for every ingested "
            f"Rio id at nascente_rio; got {dispatched}"
        )

    def test_dispatches_enrich_at_nascente_rio_mar_too(self, monkeypatch):
        ids = [str(uuid.uuid4()) for _ in range(2)]
        enrich_mock = self._run(monkeypatch, depth="nascente_rio_mar", produce_ids=ids)
        assert enrich_mock.delay.call_count == 2

    def test_no_enrich_at_nascente_only(self, monkeypatch):
        """depth=nascente → run_rio False → produce returns [] → no enrichment."""
        ids = [str(uuid.uuid4()) for _ in range(3)]
        enrich_mock = self._run(monkeypatch, depth="nascente", produce_ids=ids)
        enrich_mock.delay.assert_not_called()
