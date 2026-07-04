"""Unit tests for TripAdvisorAtrativosIngest card-field mapping (plan 13-02).

Verifies that _ingest_one reads normalized AttractionsFusion card dict fields:
  - review_count (underscore, not camelCase reviewCount)
  - most_recent_review_at always None at Nascente (not parsed from listing card)
  - category carried into raw Nascente payload

All tests are 100% offline: store_raw and process_nascente_record are monkeypatched.
No DB connection required.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from brave.config.settings import ScoreConfig
from brave.lanes.tripadvisor.ibge import IbgeMunicipio
from tests.fakes.fake_nominatim import FakeGeocoderClient
from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IBGE_RECORDS = [
    IbgeMunicipio("3170107", "Uberlândia", "MG", -18.9186, -48.2772),
    IbgeMunicipio("3550308", "São Paulo", "SP", -23.5505, -46.6333),
]

# geo_id=303380 = Minas Gerais; use Uberlândia as the IBGE match for the fixture
_GEO_ID_MG = 303380
_IBGE_CODE_UB = "3170107"
_PARENT_RIO_ID = uuid.uuid4()
_DESTINO_RIO_MAP: dict[str, tuple[uuid.UUID, str]] = {
    _IBGE_CODE_UB: (_PARENT_RIO_ID, "tripadvisor:destination:303380"),
}


def _make_card(
    review_count: int = 100,
    rating: float = 4.0,
    category: str = "Waterfalls",
    **extra: Any,
) -> dict[str, Any]:
    """Build a minimal normalized AttractionsFusion card dict."""
    card: dict[str, Any] = {
        "locationId": 312332,
        # Name must fuzzy-resolve to an IBGE municipality (resolve_municipio,
        # threshold 88) because AttractionsFusion listing cards carry no lat/lng
        # for the haversine fallback. "Uberlândia" matches the MG fixture record
        # exactly; the field-mapping assertions below don't depend on the name.
        "name": "Uberlândia",
        "review_count": review_count,
        "rating": rating,
        "category": category,
        # lat/lng absent in listing card — None values fine
    }
    card.update(extra)
    return card


def _make_fake_client(card: dict[str, Any]) -> FakeTripAdvisorClient:
    return FakeTripAdvisorClient(
        fixture_attractions={_GEO_ID_MG: [card]},
        gql_pages=[(0, [card])],  # produce() paginates via fetch_attractions_paginated_gql
        geo_ids={"MG": _GEO_ID_MG},
    )


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


def _make_coordless_card(name: str = "Cachoeira do Tabuleiro") -> dict[str, Any]:
    """Build a coordless card whose name does NOT match any IBGE município name.

    Used for TA-15 regression tests: this card quarantines without geo-enrichment
    and resolves via Nominatim when a FakeGeocoderClient is injected.
    """
    return {
        "locationId": 312332,
        "name": name,
        "review_count": 100,
        "rating": 4.0,
        "category": "Waterfalls",
        # lat/lng deliberately absent — mirrors real AttractionsFusion listing card
    }


# ---------------------------------------------------------------------------
# TestAtrativosIngestCardFields
# ---------------------------------------------------------------------------


class TestAtrativosIngestCardFields:
    """Field-mapping tests for _ingest_one with normalized AttractionsFusion card dicts."""

    @pytest.mark.asyncio
    async def test_ingest_one_maps_review_count_underscore(self) -> None:
        """review_count (underscore) is read; reviewCount (camelCase) is ignored.

        The normalized card dict from _parse_attractions_page uses underscore.
        A stale camelCase key should be silently ignored (entity.get("review_count")).
        """
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(review_count=500, reviewCount=999)  # camelCase MUST be ignored
        fake_client = _make_fake_client(card)
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=mock_session,
                config=config,
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
            )
            await ingest.produce("MG", run_rio=False)

        assert mock_store_raw.called, "store_raw must be called"
        payload = mock_store_raw.call_args.kwargs["payload"]
        assert payload["review_count"] == 500, (
            f"Expected review_count=500 (from underscore key), got {payload.get('review_count')}. "
            "If 999, _ingest_one is reading camelCase reviewCount instead of underscore review_count."
        )

    @pytest.mark.asyncio
    async def test_ingest_one_sets_most_recent_review_at_none(self) -> None:
        """most_recent_review_at is None unconditionally at Nascente (Phase 13 decision).

        The AttractionsFusion listing card does not carry mostRecentReviewDate.
        _ingest_one must NOT try to parse it — review_signals.most_recent_review_at is None.
        atualidade_from_recency(None) must return 0.0 (not raise) — verified by checking
        atualidade_value=0.0 in the payload and that the ingest completed successfully.
        """
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # Card with no mostRecentReviewDate key — simulates real AttractionsFusion card
        card = _make_card(review_count=200, rating=4.5)
        assert "mostRecentReviewDate" not in card, "Fixture must not include mostRecentReviewDate"

        fake_client = _make_fake_client(card)
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=mock_session,
                config=config,
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
            )
            # Must not raise — atualidade_from_recency(None) must return 0.0
            await ingest.produce("MG", run_rio=False)

        assert mock_store_raw.called, "store_raw must be called — ingest must not crash with None recency"
        payload = mock_store_raw.call_args.kwargs["payload"]

        # atualidade_from_recency(None) → 0.0
        assert payload["atualidade_value"] == 0.0, (
            f"atualidade_value must be 0.0 when most_recent_review_at is None; "
            f"got {payload.get('atualidade_value')}"
        )

        # Verify review_signals carries most_recent_review_at=None via store_raw payload
        # (store_raw stores the Nascente payload dict — review_signals is not a top-level key
        # but the model was constructed without error, which means most_recent_review_at=None was accepted)
        assert mock_store_raw.called, "Ingest with None recency completed without error"

    @pytest.mark.asyncio
    async def test_ingest_one_stores_category(self) -> None:
        """category from AttractionsFusion card (primaryInfo.text) is stored in raw payload."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(category="Waterfalls")
        fake_client = _make_fake_client(card)
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=mock_session,
                config=config,
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
            )
            await ingest.produce("MG", run_rio=False)

        assert mock_store_raw.called, "store_raw must be called"
        payload = mock_store_raw.call_args.kwargs["payload"]
        assert "category" in payload, f"category must be in Nascente payload; keys={list(payload.keys())}"
        assert payload["category"] == "Waterfalls", (
            f"category must be 'Waterfalls'; got {payload.get('category')!r}"
        )

    @pytest.mark.asyncio
    async def test_ingest_one_completude_not_capped_for_listing_card(self) -> None:
        """WR-01: a typical listing card must NOT be silently capped at completude 40.

        Regression guard for the field-name mismatch: _ingest_one must map the
        camelCase card onto the snake_case keys _TA_COMPLETUDE_FIELDS expects
        (uf, location_id) before scoring. A minimal listing card carries
        name + locationId + rating + review_count + category, and _ingest_one
        adds uf + location_id — so 6/10 completude fields match → 60.0, never 40.0.
        """
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(review_count=200, rating=4.5)
        # lat/lng/address/description are absent from a listing card
        fake_client = _make_fake_client(card)
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=mock_session,
                config=config,
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
            )
            await ingest.produce("MG", run_rio=False)

        assert mock_store_raw.called, "store_raw must be called"
        payload = mock_store_raw.call_args.kwargs["payload"]
        # name, uf, location_id, rating, review_count, category present → 6/10 → 60.0
        assert payload["completude_value"] == 60.0, (
            f"completude_value must be 60.0 for a typical listing card (6/10 fields), "
            f"got {payload.get('completude_value')}. A value of 40.0 means the "
            "camelCase->snake_case mapping regressed and uf/location_id no longer match."
        )

    @pytest.mark.asyncio
    async def test_ingest_one_completude_reaches_100_for_full_card(self) -> None:
        """WR-01: a fully-populated card (all 10 completude fields) scores 100.0.

        Adds lat, lng, address, description on top of the listing-card fields so
        all 10 _TA_COMPLETUDE_FIELDS are present. Confirms the cap=100 ceiling is
        actually reachable once the card is complete — proving the prior 40-cap
        was a field-name mismatch, not an intentional ceiling.
        """
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(
            review_count=200,
            rating=4.5,
            lat=-18.9186,
            lng=-48.2772,
            address="Uberlândia, MG",
            description="A complete attraction record",
        )
        fake_client = _make_fake_client(card)
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=mock_session,
                config=config,
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
            )
            await ingest.produce("MG", run_rio=False)

        assert mock_store_raw.called, "store_raw must be called"
        payload = mock_store_raw.call_args.kwargs["payload"]
        assert payload["completude_value"] == 100.0, (
            f"completude_value must reach 100.0 when all 10 fields are present; "
            f"got {payload.get('completude_value')}"
        )


# ---------------------------------------------------------------------------
# TestAtrativosGeoEnrichment (TA-15)
# ---------------------------------------------------------------------------


class TestAtrativosGeoEnrichment:
    """Regression tests for TA-15: coordless card geo-enrichment via Nominatim."""

    @pytest.mark.asyncio
    async def test_coordless_resolves_via_geo(self) -> None:
        """Coordless card that previously quarantined now resolves to correct município.

        Fixtures: "Cachoeira do Tabuleiro" (locationId=312332, MG) → Nominatim returns
        Conceição do Mato Dentro coords → resolve_municipio haversine 50km matches.
        """
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # IBGE record for Conceição do Mato Dentro (MG, spike-verified coords)
        ibge_records = [
            IbgeMunicipio("3117900", "Conceição do Mato Dentro", "MG", -19.047, -43.426),
        ]
        destino_rio_map: dict[str, tuple[uuid.UUID, str]] = {
            "3117900": (_PARENT_RIO_ID, "tripadvisor:destination:303380"),
        }
        card = _make_coordless_card()
        fake_ta = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            gql_pages=[(0, [card])],  # produce() paginates via fetch_attractions_paginated_gql
            geo_ids={"MG": _GEO_ID_MG},
        )
        # FakeGeocoderClient returns the Nominatim geocode result for this locationId
        fake_geo = FakeGeocoderClient(
            fixture_results={
                "312332": {
                    "lat": -19.047,
                    "lon": -43.426,
                    "osm_id": 123,
                    "municipio_name": "Conceição do Mato Dentro",
                }
            }
        )

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_ta,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=ibge_records,
                destino_rio_map=destino_rio_map,
                geocoder=fake_geo,
            )
            await ingest.produce("MG", run_rio=False)

        assert mock_store_raw.called, (
            "store_raw must be called — coordless card must resolve via geo-enrichment "
            "instead of quarantining as ibge_unmatched"
        )
        payload = mock_store_raw.call_args.kwargs["payload"]
        assert payload["municipio_id"] == "3117900"
        assert len(fake_geo.geocode_calls) == 1
        assert fake_geo.geocode_calls[0]["location_id"] == "312332"
        # WR-01 regression guard: geocoded coordinates must be promoted into the
        # persisted payload — they must NOT remain None after a successful geocode.
        assert payload["lat"] == -19.047, (
            f"Geocoded lat must be persisted; expected -19.047, got {payload.get('lat')!r}. "
            "If None, geo-enrichment resolved IBGE but discarded the coordinates (WR-01)."
        )
        assert payload["lng"] == -43.426, (
            f"Geocoded lng must be persisted; expected -43.426, got {payload.get('lng')!r}. "
            "If None, geo-enrichment resolved IBGE but discarded the coordinates (WR-01)."
        )

    @pytest.mark.asyncio
    async def test_quarantine_after_both_fail(self) -> None:
        """ibge_unmatched quarantine fires only after both name-match AND geo-enrichment fail."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        ibge_records = [IbgeMunicipio("3550308", "São Paulo", "SP", -23.55, -46.63)]
        card = _make_coordless_card()
        fake_ta = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            gql_pages=[(0, [card])],  # produce() paginates via fetch_attractions_paginated_gql
            geo_ids={"MG": _GEO_ID_MG},
        )
        # Geocoder returns no match (all misses — both strategies fail)
        fake_geo = FakeGeocoderClient(fixture_results={})

        with patch("brave.lanes.tripadvisor.atrativos.quarantine_poison") as mock_q:
            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_ta,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=ibge_records,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest.produce("MG", run_rio=False)

        # quarantine_poison called with ibge_unmatched (not some other task_name)
        quarantine_calls = [
            c for c in mock_q.call_args_list
            if c.kwargs.get("task_name") == "brave.ta.atrativos.ibge_unmatched"
        ]
        assert len(quarantine_calls) == 1

    @pytest.mark.asyncio
    async def test_no_geocoder_unchanged(self) -> None:
        """geocoder=None → existing behavior unchanged (no Phase-11/13 regression)."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # Use the existing _IBGE_RECORDS fixture (Uberlândia matches the card name)
        card = _make_card(name="Uberlândia")
        fake_ta = _make_fake_client(card)

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_ta,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
                # geocoder NOT passed — defaults to None
            )
            await ingest.produce("MG", run_rio=False)

        assert mock_store_raw.called


# ---------------------------------------------------------------------------
# TestAtrativosGeoFallback — TA-ftx: fetch_attraction_geo-based IBGE fallback
# ---------------------------------------------------------------------------


class TestAtrativosGeoFallback:
    """Verify the rewired tertiary IBGE fallback using fetch_attraction_geo.

    TA-ftx replaces the broken parents[0].localizedName path (rmz-04) with a
    single GraphQL query (qid d3d4987463b78a39) that returns cityName/stateName
    directly. state_name_to_uf derives the UF; resolve_municipio finishes the
    IBGE match.

    Tests use FakeTripAdvisorClient with fixture_geo + geo_calls to verify the
    correct method is called and the correct behavior follows.
    """

    _FOZ_IBGE = IbgeMunicipio("4108304", "Foz do Iguacu", "PR", -25.5163, -54.5854)

    def _make_destino_rio_map(self) -> dict:
        return {"4108304": (uuid.uuid4(), "tripadvisor:destination:303444")}

    @pytest.mark.asyncio
    async def test_geo_fallback_resolves_ibge_via_fetch_attraction_geo(self) -> None:
        """Coordless card with non-matching name resolves via fetch_attraction_geo.

        'Cachoeira do Tabuleiro' does NOT match any IBGE município name. The
        geo fallback fires, calls fetch_attraction_geo(312332), gets
        {city_name:'Uberlândia', state_name:'State of Minas Gerais'}, derives UF='MG',
        then resolve_municipio resolves to the MG IBGE record. store_raw is called.
        """
        from brave.config.settings import TripAdvisorConfig
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_coordless_card()  # name="Cachoeira do Tabuleiro" — no IBGE match
        fake_client = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            gql_pages=[(0, [card])],  # produce() paginates via fetch_attractions_paginated_gql
            geo_ids={"MG": _GEO_ID_MG},
            fixture_geo={312332: {
                "location_id": 312332,
                "city_name": "Uberlândia",
                "state_name": "State of Minas Gerais",
                "city_geo_id": 303380,
                "state_geo_id": 303383,
            }},
        )
        ta_config = TripAdvisorConfig(page_throttle_seconds=0)

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
                destino_rio_map=_DESTINO_RIO_MAP,
                ta_config=ta_config,
            )
            await ingest.produce("MG", run_rio=False)

        # fetch_attraction_geo was called for locationId 312332
        assert fake_client.geo_calls == [312332], (
            f"Expected geo_calls==[312332], got {fake_client.geo_calls}. "
            "fetch_attraction_geo must be called, not fetch_attraction_detail."
        )
        # record was stored (not quarantined)
        assert mock_store_raw.called, (
            "store_raw must be called — the geo fallback must resolve Uberlândia/MG "
            "and store the record, not quarantine as ibge_unmatched"
        )

    @pytest.mark.asyncio
    async def test_geo_fallback_skipped_when_ta_config_none(self) -> None:
        """When ta_config=None, fetch_attraction_geo is never called.

        Without ta_config the geo fallback is skipped entirely. The coordless card
        may quarantine as ibge_unmatched — that is correct behavior.
        """
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_coordless_card()
        fake_client = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            gql_pages=[(0, [card])],  # produce() paginates via fetch_attractions_paginated_gql
            geo_ids={"MG": _GEO_ID_MG},
            fixture_geo={312332: {
                "location_id": 312332,
                "city_name": "Uberlândia",
                "state_name": "State of Minas Gerais",
                "city_geo_id": 303380,
                "state_geo_id": 303383,
            }},
        )

        with patch("brave.lanes.tripadvisor.atrativos.quarantine_poison"):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
                # ta_config NOT passed → defaults to None → geo fallback is skipped
            )
            await ingest.produce("MG", run_rio=False)

        # fetch_attraction_geo must NOT have been called
        assert fake_client.geo_calls == [], (
            f"Expected geo_calls==[] when ta_config=None, got {fake_client.geo_calls}. "
            "The geo fallback must be gated behind ta_config."
        )

    @pytest.mark.asyncio
    async def test_geo_fallback_returns_none_no_crash(self) -> None:
        """When fetch_attraction_geo returns None (empty fixture), no crash occurs.

        The card quarantines as ibge_unmatched, but no exception is raised.
        geo_calls is still populated (the method was called).
        """
        from brave.config.settings import TripAdvisorConfig
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_coordless_card()
        fake_client = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            gql_pages=[(0, [card])],  # produce() paginates via fetch_attractions_paginated_gql
            geo_ids={"MG": _GEO_ID_MG},
            fixture_geo={},  # geo returns None for all locationIds
        )
        ta_config = TripAdvisorConfig(page_throttle_seconds=0)

        with patch("brave.lanes.tripadvisor.atrativos.quarantine_poison") as mock_q:
            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
                ta_config=ta_config,
            )
            # Must not raise — geo returning None leads to quarantine, not crash
            await ingest.produce("MG", run_rio=False)

        # fetch_attraction_geo was called (but returned None)
        assert fake_client.geo_calls == [312332], (
            f"Expected geo_calls==[312332], got {fake_client.geo_calls}"
        )
        # Quarantine occurred (ibge_unmatched — geo returned None)
        quarantine_calls = [
            c for c in mock_q.call_args_list
            if c.kwargs.get("task_name") == "brave.ta.atrativos.ibge_unmatched"
        ]
        assert len(quarantine_calls) == 1, (
            f"Expected 1 ibge_unmatched quarantine when geo returns None; "
            f"got {len(quarantine_calls)}"
        )


# ---------------------------------------------------------------------------
# TestAtrativosPaginatedGql + review enrichment (Phase G)
#
# produce() now paginates the GraphQL listing (fetch_attractions_paginated_gql,
# replacing single-page fetch_attractions) and, under enrich_reviews=True, calls
# fetch_recent_review per card so atualidade lifts the §7.6 score. These tests use
# a self-contained local stub client so they do NOT couple to FakeTripAdvisorClient's
# fixture attributes.
# ---------------------------------------------------------------------------


class _GqlListingClient:
    """Local TA client stub implementing only the produce()/_ingest_one surface.

    - resolve_geo_id(uf)                     → fixed geoId
    - fetch_attractions_paginated_gql(geo)   → yields the configured (offset, cards) pages
    - fetch_recent_review(location_id)       → configured recency dict or None (records calls)
    - fetch_attraction_geo(location_id)      → None (geo fallback dormant; names IBGE-resolve)
    """

    def __init__(
        self,
        *,
        geo_id: int,
        pages: list[tuple[int, list[dict[str, Any]]]],
        recent: dict[int, dict[str, Any] | None] | None = None,
    ) -> None:
        self._geo_id = geo_id
        self._pages = pages
        self._recent = recent or {}
        self.recent_review_calls: list[int] = []

    async def resolve_geo_id(self, uf: str) -> int:
        return self._geo_id

    async def fetch_attractions_paginated_gql(
        self, geo_id: int, start_page: int = 1, max_pages: int = 334
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        for offset, cards in self._pages:
            yield offset, cards

    async def fetch_recent_review(self, location_id: int) -> dict[str, Any] | None:
        self.recent_review_calls.append(location_id)
        return self._recent.get(location_id)

    async def fetch_attraction_geo(self, location_id: int) -> dict[str, Any] | None:
        return None


def _ub_card(location_id: int) -> dict[str, Any]:
    """A listing card whose name resolves to the Uberlândia IBGE fixture record."""
    return {
        "locationId": location_id,
        "name": "Uberlândia",
        "review_count": 100,
        "rating": 4.0,
        "category": "Parks",
    }


class TestAtrativosPaginatedGql:
    """produce() paginates via fetch_attractions_paginated_gql across multiple pages."""

    @pytest.mark.asyncio
    async def test_produce_ingests_cards_across_multiple_pages(self) -> None:
        """Two pages (30 + 15 cards) → all 45 reach Nascente (not capped at one page)."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        page1 = [_ub_card(100_000 + i) for i in range(30)]
        page2 = [_ub_card(200_000 + i) for i in range(15)]
        client = _GqlListingClient(
            geo_id=_GEO_ID_MG, pages=[(0, page1), (30, page2)]
        )

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())

            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
            )
            await ingest.produce("MG", run_rio=False)

        assert mock_store_raw.call_count == 45, (
            "all 45 cards across both gql pages must reach Nascente (not just the "
            f"first page of 30); got {mock_store_raw.call_count}"
        )
        # No review enrichment when enrich_reviews defaults off.
        assert client.recent_review_calls == []

    @pytest.mark.asyncio
    async def test_produce_commits_once_per_page_for_live_kanban(self) -> None:
        """produce() COMMITS after each yielded page so ingested rows show in the /painel
        board WHILE the per-UF sweep is still running (live kanban).

        Drive the gql client with 2 pages → the session must be committed exactly twice
        (once per page). Before the fix the per-UF producer committed only once at the
        very end, so nothing was visible mid-processing.
        """
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        page1 = [_ub_card(100_000 + i) for i in range(3)]
        page2 = [_ub_card(200_000 + i) for i in range(2)]
        client = _GqlListingClient(
            geo_id=_GEO_ID_MG, pages=[(0, page1), (30, page2)]
        )

        session = MagicMock()

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())

            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=session,
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
            )
            await ingest.produce("MG", run_rio=False)

        assert session.commit.call_count == 2, (
            "produce() must commit once per yielded page (2 pages → 2 commits) so each "
            f"page's rows become visible immediately; got {session.commit.call_count}"
        )


class TestAtrativosReviewEnrichment:
    """enrich_reviews wiring: fetch_recent_review fills atualidade only when enabled."""

    @pytest.mark.asyncio
    async def test_enrich_reviews_true_populates_atualidade(self) -> None:
        """enrich_reviews=True → most_recent_review_at from fetch_recent_review; atualidade > 0.

        The recency container also overrides the card's review_count/rating with the
        precise totalCount/rating from the reviews query.
        """
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        loc_id = 312332
        recent_dt = datetime.now(UTC) - timedelta(days=10)  # ≤30d → atualidade 100
        client = _GqlListingClient(
            geo_id=_GEO_ID_MG,
            pages=[(0, [_ub_card(loc_id)])],
            recent={
                loc_id: {
                    "review_count": 321,
                    "rating": 4.6,
                    "most_recent_review_at": recent_dt,
                }
            },
        )

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())

            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
            )
            await ingest.produce("MG", run_rio=False, enrich_reviews=True)

        assert client.recent_review_calls == [loc_id], (
            "fetch_recent_review must be called once with the numeric locationId"
        )
        assert mock_store_raw.called
        payload = mock_store_raw.call_args.kwargs["payload"]
        assert payload["atualidade_value"] == 100.0, (
            f"atualidade must lift from the fetched recency (≤30d → 100.0); "
            f"got {payload.get('atualidade_value')}"
        )
        # Precise review container overrides the card aggregate.
        assert payload["review_count"] == 321
        assert payload["rating"] == 4.6

    @pytest.mark.asyncio
    async def test_enrich_reviews_false_leaves_atualidade_zero(self) -> None:
        """enrich_reviews=False (default) → no review call; atualidade stays 0.0."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        loc_id = 312332
        client = _GqlListingClient(
            geo_id=_GEO_ID_MG,
            pages=[(0, [_ub_card(loc_id)])],
            recent={
                loc_id: {
                    "review_count": 321,
                    "rating": 4.6,
                    "most_recent_review_at": datetime.now(UTC),
                }
            },
        )

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())

            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
            )
            await ingest.produce("MG", run_rio=False)  # enrich_reviews defaults False

        assert client.recent_review_calls == [], (
            "fetch_recent_review must NOT be called when enrich_reviews is off"
        )
        payload = mock_store_raw.call_args.kwargs["payload"]
        assert payload["atualidade_value"] == 0.0, (
            f"atualidade must stay 0.0 with enrichment off; got {payload.get('atualidade_value')}"
        )
        # Card aggregate untouched (no override).
        assert payload["review_count"] == 100
