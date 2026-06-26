"""Unit tests for the TripAdvisor BULK national ingest path (plan 15-06, TA-12).

The bulk lane resolves the operator-locked A1 blocker: it writes all-Brazil
attractions to Nascente WITHOUT a parent destino, deriving uf + município from the
national geocode (geocode_national) + nearest-IBGE-seat resolution
(resolve_municipio_national). This is a DISTINCT path:

  - _ingest_one_bulk    — parent-less single-card ingest (drops the parent gate)
  - produce_paginated   — drives fetch_attractions_paginated, commits PER PAGE,
                          records progress + a live error counter

The existing per-UF _ingest_one parent-linkage path stays byte-for-byte intact
(asserted by git diff in the plan acceptance, not here).

All tests are 100% offline: RUN_REAL_EXTERNALS unset, fake client + fake geocoder
+ fakeredis; store_raw / process_nascente_record are monkeypatched (no DB).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from pydantic import ValidationError

from brave.config.settings import ScoreConfig
from brave.lanes.tripadvisor import sweep_progress
from brave.lanes.tripadvisor.client import SessionExpiredError
from brave.lanes.tripadvisor.ibge import IbgeMunicipio
from brave.lanes.tripadvisor.schemas import TripAdvisorReviewSignals
from tests.fakes.fake_nominatim import FakeGeocoderClient
from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Uberlândia (MG) IBGE seat — bulk-path target município.
_IBGE_RECORDS = [
    IbgeMunicipio("3170107", "Uberlândia", "MG", -18.9186, -48.2772),
    IbgeMunicipio("3550308", "São Paulo", "SP", -23.5505, -46.6333),
]

_GEO_ID_BR = 294280  # all-Brazil geoId


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


def _make_card(
    location_id: int = 312332,
    name: str = "Parque do Sabiá",
    review_count: int = 100,
    rating: float = 4.0,
    category: str = "Parks",
) -> dict[str, Any]:
    """A normalized AttractionsFusion listing card (no lat/lng — geocoded nationally)."""
    return {
        "locationId": location_id,
        "name": name,
        "review_count": review_count,
        "rating": rating,
        "category": category,
    }


def _geo_near_uberlandia(location_id: str) -> tuple[str, dict[str, Any]]:
    """A national geocode fixture entry resolving close to the Uberlândia seat."""
    return location_id, {
        "lat": -18.92,
        "lon": -48.28,
        "osm_id": 1,
        "municipio_name": "Uberlândia",
    }


# ---------------------------------------------------------------------------
# Task 1 — _ingest_one_bulk
# ---------------------------------------------------------------------------


class TestIngestBulk:
    """_ingest_one_bulk: parent-less Nascente ingest with derived uf/município."""

    @pytest.mark.asyncio
    async def test_ingest_bulk_writes_parentless_nascente_row(self) -> None:
        """A geocodable card → exactly one Nascente row, parent_rio_id None, derived uf."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(location_id=312332)
        fake_geo = FakeGeocoderClient(
            fixture_national_results=dict([_geo_near_uberlandia("312332")])
        )

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=FakeTripAdvisorClient(),
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},  # bulk path ignores the parent map entirely
                geocoder=fake_geo,
            )
            await ingest._ingest_one_bulk(card, run_rio=False)

        assert mock_store_raw.call_count == 1, "exactly one Nascente row must be written"
        kwargs = mock_store_raw.call_args.kwargs
        assert kwargs["entity_type"] == "attraction"
        # uf is DERIVED from the matched IBGE record, not any input arg.
        assert kwargs["uf"] == "MG"
        payload = kwargs["payload"]
        assert payload["parent_rio_id"] is None, "bulk path is parent-less"
        assert payload["parent_source_ref"] is None
        assert payload["municipio_id"] == "3170107"
        assert payload["canonical"]["municipio"] == "Uberlândia"
        assert payload["canonical"]["uf"] == "MG"
        # National geocode used (not the per-UF geocode).
        assert fake_geo.geocode_national_calls == [
            {"location_id": "312332", "name": "Parque do Sabiá"}
        ]
        assert fake_geo.geocode_calls == []

    @pytest.mark.asyncio
    async def test_ingest_bulk_unresolvable_quarantines_ibge_unmatched(self) -> None:
        """Geocoder cannot resolve → ibge_unmatched quarantine, zero Nascente rows."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(location_id=999)
        fake_geo = FakeGeocoderClient(fixture_national_results={})  # all misses

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.quarantine_poison") as mock_q,
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=FakeTripAdvisorClient(),
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest._ingest_one_bulk(card, run_rio=False)

        assert not mock_store_raw.called, "no Nascente row for an unresolvable card"
        unmatched = [
            c for c in mock_q.call_args_list
            if c.kwargs.get("task_name") == "brave.ta.atrativos.ibge_unmatched"
        ]
        assert len(unmatched) == 1
        # The dropped parent gate must NEVER fire in the bulk path.
        parent_absent = [
            c for c in mock_q.call_args_list
            if c.kwargs.get("task_name") == "brave.ta.atrativos.parent_destino_absent"
        ]
        assert parent_absent == []

    @pytest.mark.asyncio
    async def test_ingest_bulk_no_ibge_seat_within_radius_quarantines(self) -> None:
        """Geocode succeeds but no IBGE seat within 50 km → ibge_unmatched, no row."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(location_id=555)
        # Geocode lands in the mid-Atlantic — far from every BR seat.
        fake_geo = FakeGeocoderClient(
            fixture_national_results={
                "555": {"lat": 0.0, "lon": -30.0, "osm_id": 9, "municipio_name": None}
            }
        )

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.quarantine_poison") as mock_q,
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=FakeTripAdvisorClient(),
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest._ingest_one_bulk(card, run_rio=False)

        assert not mock_store_raw.called
        unmatched = [
            c for c in mock_q.call_args_list
            if c.kwargs.get("task_name") == "brave.ta.atrativos.ibge_unmatched"
        ]
        assert len(unmatched) == 1

    def test_review_signals_reject_extra_fields_lgpd(self) -> None:
        """LGPD guard intact: review-author/text fields are rejected (extra='forbid')."""
        # Aggregate-only construction succeeds.
        TripAdvisorReviewSignals(review_count=10, rating=4.0)
        # Any author/text drift is rejected at parse time.
        with pytest.raises(ValidationError):
            TripAdvisorReviewSignals(
                review_count=10,
                rating=4.0,
                author="Jane Doe",  # type: ignore[call-arg]
                text="great place",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# Task 2 — produce_paginated
# ---------------------------------------------------------------------------


def _resolvable_geo_fixture(location_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Build a national-geocode fixture resolving every id near Uberlândia."""
    return {lid: _geo_near_uberlandia(lid)[1] for lid in location_ids}


class _RaisingPaginatedClient:
    """Fake client whose paginated iterator raises SessionExpiredError mid-run."""

    def __init__(self, pages_before_raise: int = 1) -> None:
        self._pages_before_raise = pages_before_raise

    async def fetch_attractions_paginated(
        self, geo_id: int, start_page: int = 1, max_pages: int = 334
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        for i in range(self._pages_before_raise):
            yield i * 30, [_make_card(location_id=1000 + i)]
        raise SessionExpiredError("TripAdvisor HTML returned 403 — session expired.")


class TestProducePaginated:
    """produce_paginated: per-page commit + progress + live error counter."""

    @pytest.mark.asyncio
    async def test_produce_paginated_ingests_all_pages(self) -> None:
        """2 pages × 30 cards → all ingested; progress shows pages_done/attractions/offset."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # Two pages of 30 cards; page 1 offset 0, page 2 offset 30.
        page1 = [_make_card(location_id=10_000 + i) for i in range(30)]
        page2 = [_make_card(location_id=20_000 + i) for i in range(30)]
        all_ids = [str(c["locationId"]) for c in page1 + page2]
        fake_client = FakeTripAdvisorClient(
            fixture_pages={_GEO_ID_BR: [(0, page1), (30, page2)]}
        )
        fake_geo = FakeGeocoderClient(
            fixture_national_results=_resolvable_geo_fixture(all_ids)
        )
        rc = fakeredis.FakeRedis()
        sweep_progress.start(rc, pages_total=2)

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest.produce_paginated(
                _GEO_ID_BR, start_page=1, max_pages=2, redis=rc, run_rio=False
            )

        assert mock_store_raw.call_count == 60, "all 60 cards must reach Nascente"
        snap = sweep_progress.get_progress(rc)
        assert snap["pages_done"] == 2
        assert snap["attractions_ingested"] == 60
        assert snap["current_offset"] == 30
        assert snap["error_count"] == 0
        assert sweep_progress.get_resume_offset(rc) == 30

    @pytest.mark.asyncio
    async def test_produce_paginated_commits_per_page_before_record_page(self) -> None:
        """Session.commit fires once per page, BEFORE record_page (resume integrity)."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        page1 = [_make_card(location_id=30_000)]
        page2 = [_make_card(location_id=30_001)]
        all_ids = ["30000", "30001"]
        fake_client = FakeTripAdvisorClient(
            fixture_pages={_GEO_ID_BR: [(0, page1), (30, page2)]}
        )
        fake_geo = FakeGeocoderClient(
            fixture_national_results=_resolvable_geo_fixture(all_ids)
        )
        rc = fakeredis.FakeRedis()
        mock_session = MagicMock()

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
            patch(
                "brave.lanes.tripadvisor.atrativos.sweep_progress.record_page"
            ) as mock_record_page,
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            # Order spy: commit must precede record_page within each page.
            manager = MagicMock()
            manager.attach_mock(mock_session.commit, "commit")
            manager.attach_mock(mock_record_page, "record_page")

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=mock_session,
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest.produce_paginated(
                _GEO_ID_BR, start_page=1, max_pages=2, redis=rc, run_rio=False
            )

        assert mock_session.commit.call_count == 2, "one commit per page"
        assert mock_record_page.call_count == 2
        # Verify ordering: every record_page is immediately preceded by a commit.
        names = [c[0] for c in manager.mock_calls]
        assert names == ["commit", "record_page", "commit", "record_page"]

    @pytest.mark.asyncio
    async def test_produce_paginated_session_expiry_propagates(self) -> None:
        """SessionExpiredError from the client iterator is NOT swallowed."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        fake_geo = FakeGeocoderClient(
            fixture_national_results=_resolvable_geo_fixture(["1000"])
        )
        rc = fakeredis.FakeRedis()

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())
            ingest = TripAdvisorAtrativosIngest(
                ta_client=_RaisingPaginatedClient(pages_before_raise=1),
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            with pytest.raises(SessionExpiredError):
                await ingest.produce_paginated(
                    _GEO_ID_BR, start_page=1, max_pages=334, redis=rc, run_rio=False
                )

    @pytest.mark.asyncio
    async def test_produce_paginated_unmatched_card_increments_error_count(self) -> None:
        """A card that cannot ingest (ibge_unmatched) increments the live error_count."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # One resolvable card + one unresolvable card on the same page.
        good = _make_card(location_id=40_000)
        bad = _make_card(location_id=40_001)
        fake_client = FakeTripAdvisorClient(
            fixture_pages={_GEO_ID_BR: [(0, [good, bad])]}
        )
        # Only the good id resolves; the bad id is a national-geocode miss.
        fake_geo = FakeGeocoderClient(
            fixture_national_results=_resolvable_geo_fixture(["40000"])
        )
        rc = fakeredis.FakeRedis()
        sweep_progress.start(rc, pages_total=1)

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())
            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest.produce_paginated(
                _GEO_ID_BR, start_page=1, max_pages=1, redis=rc, run_rio=False
            )

        snap = sweep_progress.get_progress(rc)
        assert snap["error_count"] > 0, (
            "ibge_unmatched failures must increment the live panel error counter "
            "(record_error wired, not orphaned)"
        )
        assert snap["attractions_ingested"] == 1, "only the resolvable card landed"
        assert mock_store_raw.call_count == 1
