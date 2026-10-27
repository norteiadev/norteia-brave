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
from brave.domains.tripadvisor.ibge import IbgeMunicipio
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
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(review_count=500, reviewCount=999)  # camelCase MUST be ignored
        fake_client = _make_fake_client(card)
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # Card with no mostRecentReviewDate key — simulates real AttractionsFusion card
        card = _make_card(review_count=200, rating=4.5)
        assert "mostRecentReviewDate" not in card, "Fixture must not include mostRecentReviewDate"

        fake_client = _make_fake_client(card)
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(category="Waterfalls")
        fake_client = _make_fake_client(card)
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card(review_count=200, rating=4.5)
        # lat/lng/address/description are absent from a listing card
        fake_client = _make_fake_client(card)
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

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
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

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
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        ibge_records = [IbgeMunicipio("3550308", "São Paulo", "SP", -23.55, -46.63)]
        card = _make_coordless_card()
        fake_ta = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            gql_pages=[(0, [card])],  # produce() paginates via fetch_attractions_paginated_gql
            geo_ids={"MG": _GEO_ID_MG},
        )
        # Geocoder returns no match (all misses — both strategies fail)
        fake_geo = FakeGeocoderClient(fixture_results={})

        with patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as mock_q:
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
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # Use the existing _IBGE_RECORDS fixture (Uberlândia matches the card name)
        card = _make_card(name="Uberlândia")
        fake_ta = _make_fake_client(card)

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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
# TestAtrativosGeoFallback — TA's attraction page (fetch_attraction_address) places the card
# ---------------------------------------------------------------------------


class TestAtrativosGeoFallback:
    """Verify the município step that reads TA's own attraction page.

    fetch_attraction_address returns addressLocality/addressRegion (city_name/state_name);
    state_name_to_uf derives the UF; resolve_municipio finishes the IBGE match.

    Tests use FakeTripAdvisorClient with fixture_address + address_calls to verify the
    correct method is called and the correct behavior follows.
    """

    _FOZ_IBGE = IbgeMunicipio("4108304", "Foz do Iguacu", "PR", -25.5163, -54.5854)

    def _make_destino_rio_map(self) -> dict:
        return {"4108304": (uuid.uuid4(), "tripadvisor:destination:303444")}

    @pytest.mark.asyncio
    async def test_geo_fallback_resolves_ibge_via_fetch_attraction_address(self) -> None:
        """Coordless card with non-matching name resolves via TA's attraction page.

        'Cachoeira do Tabuleiro' does NOT match any IBGE município name. The page
        step calls fetch_attraction_address(312332), gets
        {city_name:'Uberlândia', state_name:'Minas Gerais'}, derives UF='MG',
        then resolve_municipio resolves to the MG IBGE record. store_raw is called.
        """
        from brave.config.settings import TripAdvisorConfig
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_coordless_card()  # name="Cachoeira do Tabuleiro" — no IBGE match
        fake_client = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            gql_pages=[(0, [card])],  # produce() paginates via fetch_attractions_paginated_gql
            geo_ids={"MG": _GEO_ID_MG},
            fixture_address={312332: {"city_name": "Uberlândia", "state_name": "Minas Gerais"}},
        )
        ta_config = TripAdvisorConfig(page_throttle_seconds=0)

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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

        assert fake_client.address_calls == [312332]
        # record was stored (not quarantined)
        assert mock_store_raw.called, (
            "store_raw must be called — TA's page must resolve Uberlândia/MG "
            "and store the record, not quarantine as ibge_unmatched"
        )

    @pytest.mark.asyncio
    async def test_geo_fallback_skipped_when_ta_config_none(self) -> None:
        """When ta_config=None, fetch_attraction_address is never called.

        Without ta_config the page step is skipped entirely. The coordless card
        may quarantine as ibge_unmatched — that is correct behavior.
        """
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_coordless_card()
        fake_client = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            gql_pages=[(0, [card])],  # produce() paginates via fetch_attractions_paginated_gql
            geo_ids={"MG": _GEO_ID_MG},
            fixture_address={312332: {"city_name": "Uberlândia", "state_name": "Minas Gerais"}},
        )

        with patch("brave.domains.tripadvisor.atrativos.quarantine_poison"):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
                # ta_config NOT passed → defaults to None → geo fallback is skipped
            )
            await ingest.produce("MG", run_rio=False)

        assert fake_client.address_calls == [], "TA's page must be gated behind ta_config"

    @pytest.mark.asyncio
    async def test_geo_fallback_returns_none_no_crash(self) -> None:
        """When TA has no page for the card ({}), no crash occurs.

        The card quarantines as ibge_unmatched, but no exception is raised.
        address_calls is still populated (the method was called).
        """
        from brave.config.settings import TripAdvisorConfig
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_coordless_card()
        fake_client = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            gql_pages=[(0, [card])],  # produce() paginates via fetch_attractions_paginated_gql
            geo_ids={"MG": _GEO_ID_MG},
            fixture_address={},  # no page for any locationId
        )
        ta_config = TripAdvisorConfig(page_throttle_seconds=0)

        with patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as mock_q:
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

        assert fake_client.address_calls == [312332]
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
# fetch_recent_review per card so atualidade lifts the reliability score. These tests use
# a self-contained local stub client so they do NOT couple to FakeTripAdvisorClient's
# fixture attributes.
# ---------------------------------------------------------------------------


class _GqlListingClient:
    """Local TA client stub implementing only the produce()/_ingest_one surface.

    - resolve_geo_id(uf)                     → fixed geoId
    - fetch_attractions_paginated_gql(geo)   → yields the configured (offset, cards) pages
    - fetch_recent_review(location_id)       → configured recency dict or None (records calls)
    - fetch_attraction_address(location_id)  → {} (no page; names IBGE-resolve)
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

    async def fetch_attraction_address(self, location_id: int) -> dict[str, Any]:
        return {}


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
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        page1 = [_ub_card(100_000 + i) for i in range(30)]
        page2 = [_ub_card(200_000 + i) for i in range(15)]
        client = _GqlListingClient(
            geo_id=_GEO_ID_MG, pages=[(0, page1), (30, page2)]
        )

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        page1 = [_ub_card(100_000 + i) for i in range(3)]
        page2 = [_ub_card(200_000 + i) for i in range(2)]
        client = _GqlListingClient(
            geo_id=_GEO_ID_MG, pages=[(0, page1), (30, page2)]
        )

        session = MagicMock()

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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

        The recency container overrides the card's review_count with the precise
        totalCount; its rating is None (per-review stars, not the aggregate), so the
        card's aggregate rating survives.
        """
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        loc_id = 312332
        recent_dt = datetime.now(UTC) - timedelta(days=10)  # ≤30d → atualidade 100
        client = _GqlListingClient(
            geo_id=_GEO_ID_MG,
            pages=[(0, [_ub_card(loc_id)])],
            recent={
                loc_id: {
                    "review_count": 321,
                    "rating": None,
                    "most_recent_review_at": recent_dt,
                }
            },
        )

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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
        # Precise totalCount overrides the card count; the card's aggregate rating stays.
        assert payload["review_count"] == 321
        assert payload["rating"] == 4.0

    @pytest.mark.asyncio
    async def test_enrich_reviews_false_leaves_atualidade_zero(self) -> None:
        """enrich_reviews=False (default) → no review call; atualidade stays 0.0."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

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
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
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


# ---------------------------------------------------------------------------
# TestAtrativosEnrichCommitGranularity (Phase — per-atrativo enrich commit)
#
# On the enrich path (enrich_reviews=True) produce() commits PER-ATRATIVO:
#   - one commit per SUCCESSFUL _ingest_one (durability)
#   - one commit per poison (rollback → cache eviction → poison → commit)
# and NEVER a page-level commit. The bulk path (enrich_reviews=False) stays
# per-page (verified by TestAtrativosPaginatedGql above; unchanged).
# ---------------------------------------------------------------------------


class TestAtrativosEnrichCommitGranularity:
    """enrich_reviews=True → per-atrativo commit + failure isolation (rollback/evict/poison)."""

    @pytest.mark.asyncio
    async def test_enrich_commits_once_per_successful_atrativo(self) -> None:
        """enrich_reviews=True, 5 atrativos across 2 pages → 5 commits (one per success).

        No page-level commit on the enrich path — every SUCCESSFUL _ingest_one is
        committed immediately for per-atrativo durability. All 5 cards resolve to the
        pre-cached Uberlândia destino, so none fail → exactly 5 commits, 0 rollbacks.
        """
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        page1 = [_ub_card(100_000 + i) for i in range(3)]
        page2 = [_ub_card(200_000 + i) for i in range(2)]
        client = _GqlListingClient(
            geo_id=_GEO_ID_MG, pages=[(0, page1), (30, page2)]
        )
        session = MagicMock()

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())

            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=session,
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
            )
            await ingest.produce("MG", run_rio=False, enrich_reviews=True)

        assert mock_store_raw.call_count == 5, (
            f"all 5 cards must reach Nascente; got {mock_store_raw.call_count}"
        )
        assert session.commit.call_count == 5, (
            "enrich path must commit once per SUCCESSFUL atrativo (5 successes → 5 "
            f"commits, no page-level commit); got {session.commit.call_count}"
        )
        assert session.rollback.call_count == 0, (
            f"no failures → no rollback; got {session.rollback.call_count}"
        )

    @pytest.mark.asyncio
    async def test_enrich_failure_rolls_back_evicts_cache_and_persists_poison(self) -> None:
        """enrich_reviews=True + a failing atrativo → rollback, poison persisted, cache evicted.

        Core regression: with a FRESH empty destino_rio_map, _ingest_one caches the
        Uberlândia destino via _ensure_destino, then the atrativo's own store_raw
        raises. produce() must:
          1. rollback() the failed atrativo's partial writes,
          2. EVICT the destino cached during this rolled-back iteration (so the next
             same-município atrativo re-creates it — no dangling parent rio),
          3. quarantine_poison(task_name="brave.ta.atrativos.produce"),
          4. commit() the poison row independently.
        """
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        loc_id = 555_001
        client = _GqlListingClient(
            geo_id=_GEO_ID_MG, pages=[(0, [_ub_card(loc_id)])]
        )
        session = MagicMock()
        # Fresh, empty map so _ensure_destino fires for Uberlândia (3170107) and caches it.
        fresh_map: dict[str, tuple[uuid.UUID, str]] = {}

        # store_raw: first call (destino inside _ensure_destino) succeeds; second call
        # (the atrativo itself) raises → drives the failure AFTER the map was cached.
        calls = {"n": 0}

        def _store_raw_side_effect(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                return MagicMock(id=uuid.uuid4())  # destino nascente
            raise RuntimeError("boom: atrativo store_raw failed")

        # _ensure_destino delegates the parent-destino store_raw / rio to
        # brave.shared.destino — patch BOTH namespaces with the SAME mock so the
        # destino call (call 1) and the atrativo call (call 2) share one counter.
        mock_store_raw = MagicMock(side_effect=_store_raw_side_effect)
        mock_rio = MagicMock(return_value=MagicMock(id=uuid.uuid4()))
        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw", new=mock_store_raw),
            patch("brave.shared.destino.store_raw", new=mock_store_raw),
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record", new=mock_rio),
            patch("brave.shared.destino.process_nascente_record", new=mock_rio),
            patch(
                "brave.domains.tripadvisor.atrativos.quarantine_poison"
            ) as mock_quarantine,
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=session,
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=fresh_map,
            )
            await ingest.produce("MG", run_rio=False, enrich_reviews=True)

        # 1. The failed atrativo's partial writes were rolled back.
        assert session.rollback.call_count == 1, (
            f"the failing atrativo must trigger exactly one rollback; "
            f"got {session.rollback.call_count}"
        )

        # 2. Cache coherence: the Uberlândia destino cached during the rolled-back
        #    iteration must be EVICTED so the next same-município atrativo re-creates it.
        #    Assert on the INGEST's own map — `destino_rio_map or {}` makes an empty passed
        #    dict fall through to a fresh internal dict, so the external `fresh_map` would
        #    never observe the cache and the assertion would pass vacuously.
        assert "3170107" not in ingest._destino_rio_map, (
            "the destino cached during the rolled-back iteration must be evicted; "
            f"stale map still has it: {ingest._destino_rio_map}"
        )

        # 3. Poison quarantined under the produce task_name.
        produce_poison = [
            c for c in mock_quarantine.call_args_list
            if c.kwargs.get("task_name") == "brave.ta.atrativos.produce"
        ]
        assert len(produce_poison) == 1, (
            f"exactly one produce-level poison expected; got {len(produce_poison)}"
        )

        # 4. Poison row persisted independently (commit after quarantine). No success
        #    ran, so the single commit is the poison-durability commit.
        assert session.commit.call_count == 1, (
            "the poison row must be committed independently on the enrich path; "
            f"got {session.commit.call_count}"
        )

    @pytest.mark.asyncio
    async def test_enrich_interleaved_success_failure_isolates(self) -> None:
        """enrich=True, 3 cards, middle fails → prior/later successes survive its rollback.

        All 3 cards resolve to the PRE-CACHED Uberlândia destino (no _ensure_destino), so each
        _ingest_one does exactly one atrativo store_raw; store_raw raises on the 2nd only. The
        durability contract: card 1 (committed before the failure) and card 3 (committed after)
        are NOT lost when card 2 rolls back → 2 success commits + 1 poison commit, exactly one
        rollback, and card 1's commit is ordered BEFORE card 2's rollback (survival).
        """
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        cards = [_ub_card(700_000 + i) for i in range(3)]
        client = _GqlListingClient(geo_id=_GEO_ID_MG, pages=[(0, cards)])
        session = MagicMock()
        n = {"i": 0}

        def _store_raw_side_effect(*args: Any, **kwargs: Any) -> Any:
            n["i"] += 1
            if n["i"] == 2:  # the middle atrativo
                raise RuntimeError("boom: middle atrativo failed")
            return MagicMock(id=uuid.uuid4())

        with (
            patch(
                "brave.domains.tripadvisor.atrativos.store_raw",
                side_effect=_store_raw_side_effect,
            ),
            patch(
                "brave.domains.tripadvisor.atrativos.process_nascente_record",
                return_value=MagicMock(id=uuid.uuid4()),
            ),
            patch(
                "brave.domains.tripadvisor.atrativos.quarantine_poison"
            ) as mock_quarantine,
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=session,
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=dict(_DESTINO_RIO_MAP),  # copy: pre-cached, no _ensure_destino
            )
            await ingest.produce("MG", run_rio=False, enrich_reviews=True)

        assert session.rollback.call_count == 1, (
            f"one failure → one rollback; got {session.rollback.call_count}"
        )
        assert session.commit.call_count == 3, (
            "2 successful atrativos + 1 poison → 3 commits (no page-level commit); "
            f"got {session.commit.call_count}"
        )
        assert mock_quarantine.call_count == 1
        # Survival ordering: card 1 commits BEFORE card 2 rolls back — a committed atrativo
        # is durable and cannot be undone by a later atrativo's rollback.
        names = [c[0] for c in session.mock_calls]
        assert names.index("commit") < names.index("rollback"), (
            f"card 1 must commit before card 2 rolls back; call order was {names}"
        )

    @pytest.mark.asyncio
    async def test_enrich_eviction_forces_destino_recreation(self) -> None:
        """After a failed atrativo evicts its destino, the NEXT same-município card re-creates it.

        Two Uberlândia cards, FRESH empty map. Card 1: _ensure_destino caches 3170107, then the
        atrativo store_raw raises → rollback evicts 3170107. Card 2 (same município) therefore
        finds the map empty and _ensure_destino fires AGAIN → the destino (source='ibge') is
        stored twice total, proving eviction forced re-creation (no dangling parent rio).
        """
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        cards = [_ub_card(800_001), _ub_card(800_002)]
        client = _GqlListingClient(geo_id=_GEO_ID_MG, pages=[(0, cards)])
        session = MagicMock()
        fresh_map: dict[str, tuple[uuid.UUID, str]] = {}
        ta = {"n": 0}

        def _store_raw_side_effect(*args: Any, **kwargs: Any) -> Any:
            if kwargs.get("source") == "tripadvisor":
                ta["n"] += 1
                if ta["n"] == 1:  # card 1's atrativo fails AFTER the destino was cached
                    raise RuntimeError("boom: first atrativo failed")
            return MagicMock(id=uuid.uuid4())

        # _ensure_destino delegates the parent-destino store_raw (source="ibge") to
        # brave.shared.destino — patch BOTH namespaces with the SAME mock so the
        # ibge-source calls are recorded and counted here.
        mock_store_raw = MagicMock(side_effect=_store_raw_side_effect)
        mock_rio = MagicMock(return_value=MagicMock(id=uuid.uuid4()))
        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw", new=mock_store_raw),
            patch("brave.shared.destino.store_raw", new=mock_store_raw),
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record", new=mock_rio),
            patch("brave.shared.destino.process_nascente_record", new=mock_rio),
            patch("brave.domains.tripadvisor.atrativos.quarantine_poison"),
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=session,
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=fresh_map,
            )
            await ingest.produce("MG", run_rio=False, enrich_reviews=True)

        ibge_stores = [
            c for c in mock_store_raw.call_args_list if c.kwargs.get("source") == "ibge"
        ]
        assert len(ibge_stores) == 2, (
            "the destino must be RE-CREATED for card 2 after card 1's rollback evicted it "
            f"(2 ibge-source store_raw expected); got {len(ibge_stores)}"
        )
        # Card 2 succeeded → its re-created destino is cached again (no dangling parent).
        # Assert on the ingest's internal map (empty passed dict → fresh internal dict).
        assert "3170107" in ingest._destino_rio_map, (
            "card 2 must re-cache the re-created destino; "
            f"map={ingest._destino_rio_map}"
        )
        assert session.rollback.call_count == 1

    @pytest.mark.asyncio
    async def test_enrich_eviction_spares_prior_committed_municipio(self) -> None:
        """A failed atrativo evicts ONLY its own iteration's destino — a prior município survives.

        Card 1 (município A = Belo Horizonte) succeeds and caches A. Card 2 (município B =
        Uberlândia) fails after caching B → the set-difference eviction must drop ONLY B, leaving
        A cached (A's rio rows are already committed and valid). A too-broad eviction that dropped
        A would dangle the next A-atrativo's parent rio — this guards the boundary at
        atrativos.py `set(self._destino_rio_map) - keys_before`.
        """
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        ibge = [
            IbgeMunicipio("3106200", "Belo Horizonte", "MG", -19.9167, -43.9345),
            IbgeMunicipio("3170107", "Uberlândia", "MG", -18.9186, -48.2772),
        ]
        card_a = {**_ub_card(900_001), "name": "Belo Horizonte"}  # município A (succeeds)
        card_b = {**_ub_card(900_002), "name": "Uberlândia"}  # município B (fails)
        client = _GqlListingClient(geo_id=_GEO_ID_MG, pages=[(0, [card_a, card_b])])
        session = MagicMock()
        # ensure_destino reads the MTur categorization off the `municipios` reference
        # table. A bare MagicMock hands back a MagicMock row, which lands in the destino
        # canonical and makes store_raw's payload unserializable. `one_or_none` is used
        # nowhere else in brave/, so pinning it here is surgical: no MTur row.
        session.query.return_value.filter.return_value.one_or_none.return_value = None
        fresh_map: dict[str, tuple[uuid.UUID, str]] = {}
        ta = {"n": 0}

        def _store_raw_side_effect(*args: Any, **kwargs: Any) -> Any:
            if kwargs.get("source") == "tripadvisor":
                ta["n"] += 1
                if ta["n"] == 2:  # card B's atrativo fails (A already committed)
                    raise RuntimeError("boom: municipio B atrativo failed")
            return MagicMock(id=uuid.uuid4())

        with (
            patch(
                "brave.domains.tripadvisor.atrativos.store_raw",
                side_effect=_store_raw_side_effect,
            ),
            patch(
                "brave.domains.tripadvisor.atrativos.process_nascente_record",
                return_value=MagicMock(id=uuid.uuid4()),
            ),
            patch("brave.domains.tripadvisor.atrativos.quarantine_poison"),
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=session,
                config=_make_config(),
                ibge_records=ibge,
                destino_rio_map=fresh_map,
            )
            await ingest.produce("MG", run_rio=False, enrich_reviews=True)

        # Assert on the ingest's internal map (empty passed dict → fresh internal dict).
        assert "3106200" in ingest._destino_rio_map, (
            "município A (prior committed) must remain cached — eviction must not drop it; "
            f"map={ingest._destino_rio_map}"
        )
        assert "3170107" not in ingest._destino_rio_map, (
            "município B (failed iteration) must be evicted"
        )
        assert session.rollback.call_count == 1


# ---------------------------------------------------------------------------
# TestAtrativosMaxPerUf — operator test-run cap (max_per_uf)
# ---------------------------------------------------------------------------


class TestAtrativosMaxPerUf:
    """produce(max_per_uf=N) stops after N attractions are processed and does not
    paginate further; max_per_uf=None ingests every card (regression)."""

    def _multi_page_client(self) -> FakeTripAdvisorClient:
        """Two pages of cards (3 + 2 = 5 total) for the MG geoId."""
        page1 = [_make_card() for _ in range(3)]
        page2 = [_make_card() for _ in range(2)]
        # Distinct locationIds so each card is its own entity (defensive; store_raw
        # is mocked so identity does not gate the count, but keeps the fixture honest).
        for i, c in enumerate(page1 + page2):
            c["locationId"] = 400000 + i
        return FakeTripAdvisorClient(
            gql_pages=[(0, page1), (30, page2)],
            geo_ids={"MG": _GEO_ID_MG},
        )

    async def _run(self, max_per_uf: int | None) -> int:
        """Run produce() with the cap; return the store_raw call count (attractions ingested)."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        client = self._multi_page_client()
        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=dict(_DESTINO_RIO_MAP),
            )
            await ingest.produce("MG", run_rio=False, max_per_uf=max_per_uf)
        return mock_store_raw.call_count

    @pytest.mark.asyncio
    async def test_cap_stops_after_n_and_halts_pagination(self) -> None:
        """max_per_uf=2 ingests exactly 2 of the 5 available cards (page 2 never fetched)."""
        count = await self._run(max_per_uf=2)
        assert count == 2, f"expected exactly 2 attractions ingested under the cap, got {count}"

    @pytest.mark.asyncio
    async def test_cap_none_ingests_all(self) -> None:
        """max_per_uf=None is the full sweep — all 5 cards across both pages ingested."""
        count = await self._run(max_per_uf=None)
        assert count == 5, f"expected all 5 attractions ingested with no cap, got {count}"

    @pytest.mark.asyncio
    async def test_cap_above_total_ingests_all(self) -> None:
        """A cap larger than the available cards ingests everything (no premature stop)."""
        count = await self._run(max_per_uf=99)
        assert count == 5, f"expected all 5 ingested when cap exceeds total, got {count}"


class TestAtrativosProduceReturnsRioIds:
    """produce() returns the Rio ids of atrativos ingested this sweep so the task layer
    can dispatch description enrichment (the TA lane's only path to descricao_editorial).
    """

    @pytest.mark.asyncio
    async def test_run_rio_returns_ingested_rio_ids(self) -> None:
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        cards = [_make_card() for _ in range(3)]
        for i, c in enumerate(cards):
            c["locationId"] = 500000 + i
        client = FakeTripAdvisorClient(
            gql_pages=[(0, cards)],
            geo_ids={"MG": _GEO_ID_MG},
        )
        rio_ids = [uuid.uuid4() for _ in cards]

        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch(
                "brave.domains.tripadvisor.atrativos.process_nascente_record"
            ) as mock_process,
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())
            # Each ingested card yields a distinct RioRecord id.
            mock_process.side_effect = [MagicMock(id=rid) for rid in rio_ids]

            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=dict(_DESTINO_RIO_MAP),
            )
            returned = await ingest.produce("MG", run_rio=True)

        assert returned == [str(rid) for rid in rio_ids], (
            "produce must return one Rio id (str) per atrativo ingested with run_rio=True"
        )

    @pytest.mark.asyncio
    async def test_nascente_only_returns_empty(self) -> None:
        """run_rio=False → no Rio records created → empty id list (no enrich dispatched)."""
        from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        card = _make_card()
        client = _make_fake_client(card)
        with (
            patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_store_raw.return_value = MagicMock(id=uuid.uuid4())
            ingest = TripAdvisorAtrativosIngest(
                ta_client=client,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=dict(_DESTINO_RIO_MAP),
            )
            returned = await ingest.produce("MG", run_rio=False)

        assert returned == []


# ---------------------------------------------------------------------------
# Skip already-synced atrativos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_produce_skips_already_synced_before_any_ta_call() -> None:
    """A card already in Rio is skipped before fetch_recent_review, and does not eat the cap."""
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    known = _make_card(locationId=1)
    new = _make_card(locationId=2)
    fake_client = FakeTripAdvisorClient(gql_pages=[(0, [known, new])], geo_ids={"MG": _GEO_ID_MG})
    mock_session = MagicMock()
    # Rio canonical keys; unmatched quarantines (none); Rio rows missing the backfill
    # marker (none — card 1 is fully synced).
    mock_session.scalars.side_effect = [["tripadvisor:attraction:1"], [], []]

    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
        patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
    ):
        ingest = TripAdvisorAtrativosIngest(
            ta_client=fake_client,
            session=mock_session,
            config=_make_config(),
            ibge_records=_IBGE_RECORDS,
            destino_rio_map=_DESTINO_RIO_MAP,
        )
        await ingest.produce("MG", run_rio=True, enrich_reviews=True, max_per_uf=1)

    assert fake_client.recent_review_calls == [2]
    assert mock_store_raw.call_count == 1
    assert mock_store_raw.call_args.kwargs["source_ref"] == "tripadvisor:attraction:2"


# ---------------------------------------------------------------------------
# Município fallbacks C (distrito by TA's page locality) and D (Places Text Search), and
# the resolver-version skip of cards already quarantined as ibge_unmatched.
# ---------------------------------------------------------------------------

_GEO_ID_GO = 303323
_ALTO_PARAISO = IbgeMunicipio("5200605", "Alto Paraíso de Goiás", "GO", -14.1305, -47.51)
_GO_DESTINO_MAP: dict[str, tuple[uuid.UUID, str]] = {
    "5200605": (uuid.uuid4(), "ibge:destination:5200605"),
}


def _go_client(card: dict[str, Any], city_name: str | None) -> FakeTripAdvisorClient:
    return FakeTripAdvisorClient(
        gql_pages=[(0, [card])],
        geo_ids={"GO": _GEO_ID_GO},
        fixture_address={312332: {"city_name": city_name, "state_name": "Goiás"}},
    )


class _LocatingAgent:
    """PlacesEnrichmentAgent stand-in: locate() answers from a fixture, run() is a no-op."""

    def __init__(self, place: dict[str, Any] | None) -> None:
        self._place = place
        self.locate_calls: list[tuple[str, str]] = []

    async def locate(self, nome: str, uf: str, category: Any = None) -> dict[str, Any] | None:
        self.locate_calls.append((nome, uf))
        return self._place

    async def run(self, rio: Any) -> None:  # pragma: no cover — run_rio=False here
        return None


async def _produce_go(card, city_name, *, places_agent=None, distritos=None):
    from brave.config.settings import TripAdvisorConfig
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
        patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as mock_q,
    ):
        mock_store_raw.return_value = MagicMock(id=uuid.uuid4())
        ingest = TripAdvisorAtrativosIngest(
            ta_client=_go_client(card, city_name),
            session=MagicMock(),
            config=_make_config(),
            ibge_records=[_ALTO_PARAISO],
            destino_rio_map=_GO_DESTINO_MAP,
            ta_config=TripAdvisorConfig(page_throttle_seconds=0),
            places_agent=places_agent,
            distritos=distritos,
        )
        await ingest.produce("GO", run_rio=False)
    return mock_store_raw, mock_q


@pytest.mark.asyncio
async def test_fallback_c_links_card_through_the_distrito_the_ta_page_named() -> None:
    from brave.shared.ibge_distritos import IbgeDistrito

    sao_jorge = IbgeDistrito("520060525", "São Jorge", "5200605", "Alto Paraíso de Goiás", "GO")
    agent = _LocatingAgent(None)
    store, quarantine = await _produce_go(
        _make_coordless_card("Cânion do Rio Preto"),
        "Vila de Sao Jorge",
        places_agent=agent,
        distritos=[sao_jorge],
    )

    assert not quarantine.called
    payload = store.call_args.kwargs["payload"]
    assert payload["municipio_id"] == "5200605"
    assert payload["canonical"]["distrito_code"] == "520060525"
    assert payload["canonical"]["distrito_municipio_ibge"] == "5200605"
    assert agent.locate_calls == []  # the free path won — no paid Places search


@pytest.mark.asyncio
async def test_fallback_d_places_links_card_and_hands_over_the_place_id() -> None:
    agent = _LocatingAgent({
        "place_id": "ChIJ-macaquinho",
        "name": "Cachoeira do Macaquinho",
        "municipio_ibge": "5200605",
        "location": {"lat": -14.07, "lng": -47.63},
    })
    store, quarantine = await _produce_go(
        _make_coordless_card("Cachoeira do Macaquinho"), None, places_agent=agent
    )

    assert not quarantine.called
    assert agent.locate_calls == [("Cachoeira do Macaquinho", "GO")]
    payload = store.call_args.kwargs["payload"]
    assert payload["municipio_id"] == "5200605"
    assert payload["place_id_cache"] == "ChIJ-macaquinho"
    assert payload["lat"] == -14.07


@pytest.mark.asyncio
async def test_unmatched_quarantine_is_stamped_with_the_resolver_version() -> None:
    """Every fallback ran with live clients (TA page, distritos, Places) → current stamp."""
    from brave.domains.tripadvisor.atrativos import IBGE_RESOLVER_VERSION
    from brave.shared.ibge_distritos import IbgeDistrito

    store, quarantine = await _produce_go(
        _make_coordless_card("Jardim de Maytreia"),
        None,
        places_agent=_LocatingAgent(None),
        distritos=[IbgeDistrito("520060525", "São Jorge", "5200605", "Alto Paraíso de Goiás", "GO")],
    )

    assert not store.called
    assert quarantine.call_args.kwargs["payload"]["resolver_version"] == IBGE_RESOLVER_VERSION


class _NullPlacesAgent(_LocatingAgent):
    """An agent built on the flag-off NullPlacesClient: its locate() never searched."""

    def __init__(self) -> None:
        from brave.clients.null_places import NullPlacesClient

        super().__init__(None)
        self._places_client = NullPlacesClient()


class _FailingLocateAgent(_LocatingAgent):
    def __init__(self) -> None:
        super().__init__(None)

    async def locate(self, nome: str, uf: str, category: Any = None) -> dict[str, Any] | None:
        raise RuntimeError("places 503")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_factory",
    [lambda: None, _NullPlacesAgent, _FailingLocateAgent],
    ids=["no-agent", "null-places", "locate-failed"],
)
async def test_unmatched_without_a_real_places_answer_is_retryable(agent_factory) -> None:
    """F08/F28: a miss because Places was off / missing / failing is NOT stamped with the
    current resolver — the next sweep must retry the card once Places is back."""
    from brave.shared.ibge_distritos import IbgeDistrito

    store, quarantine = await _produce_go(
        _make_coordless_card("Jardim de Maytreia"),
        None,
        places_agent=agent_factory(),
        distritos=[IbgeDistrito("520060525", "São Jorge", "5200605", "Alto Paraíso de Goiás", "GO")],
    )

    assert not store.called
    assert quarantine.call_args.kwargs["payload"]["resolver_version"] == 0


@pytest.mark.asyncio
async def test_produce_skips_card_already_unmatched_under_the_current_resolver() -> None:
    """Neither in Rio nor retry-worthy: quarantined under THIS chain → no TA call, no cap slot."""
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    poisoned = _make_card(locationId=1)
    new = _make_card(locationId=2)
    fake_client = FakeTripAdvisorClient(gql_pages=[(0, [poisoned, new])], geo_ids={"MG": _GEO_ID_MG})
    mock_session = MagicMock()
    # 1st query: already in Rio (none); 2nd: locationIds unmatched under this resolver;
    # 3rd: Rio rows awaiting the TA backfill (none).
    mock_session.scalars.side_effect = [[], ["1"], []]

    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
        patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
    ):
        ingest = TripAdvisorAtrativosIngest(
            ta_client=fake_client,
            session=mock_session,
            config=_make_config(),
            ibge_records=_IBGE_RECORDS,
            destino_rio_map=_DESTINO_RIO_MAP,
        )
        await ingest.produce("MG", run_rio=True, enrich_reviews=True, max_per_uf=1)

    assert fake_client.recent_review_calls == [2]
    assert mock_store_raw.call_args.kwargs["source_ref"] == "tripadvisor:attraction:2"
    from sqlalchemy.dialects import postgresql

    poison_sql = str(
        mock_session.scalars.call_args_list[1].args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "resolver_version" in poison_sql


# ---------------------------------------------------------------------------
# TA's own attraction page places the card before any name-only step
# ---------------------------------------------------------------------------

_BH = IbgeMunicipio("3106200", "Belo Horizonte", "MG", -19.9167, -43.9345)
_PORTO_VELHO = IbgeMunicipio("1100205", "Porto Velho", "RO", -8.7612, -63.9004)
_PIMENTA_BUENO = IbgeMunicipio("1100189", "Pimenta Bueno", "RO", -11.672, -61.198)
_MERCADO_PAGE = {
    "city_name": "Belo Horizonte", "state_name": "Minas Gerais",
    "lat": -19.922617, "lng": -43.94297,
    "address": "Avenida Augusto de Lima, 744, Belo Horizonte, 30190-922",
    "phone": "+55 31 3274 9434", "website": "http://mercadocentral.com.br",
}


async def _produce_page(uf, card, *, page=None, records, address_error=None, geocoder=None,
                        places_agent=None, run_rio=False, process=None, distritos=None):
    from brave.config.settings import TripAdvisorConfig
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    client = FakeTripAdvisorClient(
        gql_pages=[(0, [card])], geo_ids={uf: 1},
        fixture_address={card["locationId"]: page} if page else {},
        address_error=address_error,
        recent_review={"review_count": 100, "rating": None,
                       "most_recent_review_at": datetime.now(UTC) - timedelta(days=3)},
    )
    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as store,
        patch("brave.domains.tripadvisor.atrativos.quarantine_poison") as quarantine,
        patch("brave.domains.tripadvisor.atrativos.process_nascente_record",
              return_value=process),
        patch("brave.domains.tripadvisor.atrativos.persist_normalized") as persist,
        patch("brave.domains.tripadvisor.atrativos.TripAdvisorAtrativosIngest._ensure_destino",
              return_value=(uuid.uuid4(), "ibge:destination:x")),
    ):
        store.return_value = MagicMock(id=uuid.uuid4())
        ingest = TripAdvisorAtrativosIngest(
            ta_client=client, session=MagicMock(), config=_make_config(),
            ibge_records=records, ta_config=TripAdvisorConfig(page_throttle_seconds=0),
            geocoder=geocoder, places_agent=places_agent, distritos=distritos,
        )
        await ingest.produce(uf, run_rio=run_rio, enrich_reviews=True)
    return store, quarantine, persist


@pytest.mark.asyncio
async def test_ta_page_places_the_card_before_any_name_fallback() -> None:
    geocoder = FakeGeocoderClient()
    agent = _LocatingAgent(None)
    store, quarantine, _ = await _produce_page(
        "MG", _make_coordless_card("Mercado Central"), page=_MERCADO_PAGE,
        records=[_BH, *_IBGE_RECORDS], geocoder=geocoder, places_agent=agent,
    )

    assert not quarantine.called
    assert geocoder.geocode_calls == [] and agent.locate_calls == []
    kwargs = store.call_args.kwargs
    payload = kwargs["payload"]
    assert payload["municipio_id"] == "3106200"
    assert (payload["lat"], payload["lng"]) == (-19.922617, -43.94297)
    assert payload["address"] == _MERCADO_PAGE["address"]
    assert payload["phone"] == "+55 31 3274 9434"
    assert payload["website"] == "http://mercadocentral.com.br"
    # name, uf, location_id, lat, lng, rating, review_count, address, category.
    assert payload["completude_value"] == 90.0
    vias = [e["data"]["via"] for e in kwargs["timeline"] if e["stage"] == "geo_enriched"]
    assert vias == ["ta_page"]


@pytest.mark.asyncio
async def test_ta_page_coords_contradicting_the_locality_are_dropped() -> None:
    """Balneário Água Gelada (RO 10/2026): locality Porto Velho, point at Pimenta Bueno."""
    page = {"city_name": "Porto Velho", "state_name": "Rondônia",
            "lat": -11.652802, "lng": -61.19651}
    store, _, _ = await _produce_page(
        "RO", _make_coordless_card("Balneário Água Gelada"), page=page,
        records=[_PORTO_VELHO, _PIMENTA_BUENO],
    )

    payload = store.call_args.kwargs["payload"]
    assert payload["municipio_id"] == "1100205"
    assert payload["lat"] is None and payload["lng"] is None


@pytest.mark.asyncio
async def test_ta_page_far_point_without_another_seat_is_kept() -> None:
    """Fortaleza do Abunã: a Porto Velho distrito ~210 km from the seat, no other seat near."""
    page = {"city_name": "Porto Velho", "state_name": "Rondônia",
            "lat": -9.77477, "lng": -65.51704}
    store, _, _ = await _produce_page(
        "RO", _make_coordless_card("Praia De Fortaleza Do Abunã"), page=page,
        records=[_PORTO_VELHO, _PIMENTA_BUENO],
    )

    payload = store.call_args.kwargs["payload"]
    assert payload["municipio_id"] == "1100205"
    assert (payload["lat"], payload["lng"]) == (-9.77477, -65.51704)


@pytest.mark.asyncio
async def test_nominatim_never_overwrites_the_ta_point() -> None:
    # Locality TA names no IBGE seat; its point is ~25 km from BH (out of the name step's
    # 15 km, inside Nominatim's 50 km).
    page = {"city_name": "Lugar Nenhum", "state_name": "Minas Gerais", "lat": -20.14, "lng": -43.94}
    geocoder = FakeGeocoderClient(fixture_results={"312332": {
        "lat": -19.92, "lon": -43.94, "osm_id": 1, "municipio_name": "Belo Horizonte"}})
    store, _, _ = await _produce_page(
        "MG", _make_coordless_card("Mirante Sem Nome"), page=page, records=[_BH],
        geocoder=geocoder,
    )

    payload = store.call_args.kwargs["payload"]
    assert len(geocoder.geocode_calls) == 1
    assert payload["municipio_id"] == "3106200"
    assert (payload["lat"], payload["lng"]) == (-20.14, -43.94)


class _RunRecordingAgent(_LocatingAgent):
    def __init__(self) -> None:
        super().__init__(None)
        self.run_calls: list[Any] = []

    async def run(self, rio: Any) -> None:
        self.run_calls.append(rio)


@pytest.mark.asyncio
async def test_failed_ta_page_falls_back_without_marker_or_places() -> None:
    from brave.shared.exceptions import SourceRecordError
    from brave.shared.ibge_distritos import IbgeDistrito

    card = _make_card()  # "Uberlândia": the name step still places it
    rio = MagicMock(canonical_key="tripadvisor:attraction:312332", normalized={},
                    id=uuid.uuid4())
    agent = _RunRecordingAgent()
    store, _, persist = await _produce_page(
        "MG", card, records=_IBGE_RECORDS, address_error=SourceRecordError("no block"),
        places_agent=agent, run_rio=True, process=rio,
    )

    assert store.call_args.kwargs["payload"]["municipio_id"] == _IBGE_CODE_UB
    assert not persist.called  # no ta_backfilled_at: the backfill redoes the page
    assert agent.run_calls == []  # placed by name: Places would pin a possibly wrong seat

    # A card nothing places: the quarantine is retryable (the chain did not fully run).
    _, quarantine, _ = await _produce_page(
        "GO", _make_coordless_card("Jardim de Maytreia"), records=[_ALTO_PARAISO],
        address_error=SourceRecordError("no block"), places_agent=_LocatingAgent(None),
        distritos=[IbgeDistrito("520060525", "São Jorge", "5200605", "Alto Paraíso de Goiás",
                                "GO")],
    )
    assert quarantine.call_args.kwargs["payload"]["resolver_version"] == 0


@pytest.mark.asyncio
async def test_ta_page_403_degrades_the_sweep_to_the_listing() -> None:
    """24/10 (PR): DataDome 403'd the attraction pages while the listing answered 200, and
    the fail-fast stopped the sweep 5 min in. The sweep goes on: the card lands by name,
    without the backfill marker, so the backfill redoes its page with a fresh session."""
    from brave.domains.tripadvisor.client import SessionExpiredError

    store, quarantine, persist = await _produce_page(
        "MG", _make_card(), records=_IBGE_RECORDS, address_error=SessionExpiredError("403"),
        run_rio=True, process=MagicMock(canonical_key="tripadvisor:attraction:x"),
    )

    assert store.called and not quarantine.called
    assert not persist.called  # no ta_backfilled_at: the backfill gap check catches it


@pytest.mark.asyncio
async def test_blocked_pages_are_not_retried_until_the_window_ends(monkeypatch) -> None:
    """Once blocked, the next cards skip the page (no 403 storm on the cookies), and one
    page is tried again after _PAGE_BLOCK_RETRY_SECONDS; a 200 lifts the block."""
    import brave.domains.tripadvisor.atrativos as atrativos_mod
    from brave.config.settings import TripAdvisorConfig
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest
    from brave.domains.tripadvisor.client import SessionExpiredError

    import fakeredis

    now = [1000.0]
    monkeypatch.setattr(atrativos_mod.time, "monotonic", lambda: now[0])
    client = FakeTripAdvisorClient(address_error=SessionExpiredError("403"))
    ingest = TripAdvisorAtrativosIngest(
        ta_client=client, session=MagicMock(), config=_make_config(), ibge_records=[],
        ta_config=TripAdvisorConfig(page_throttle_seconds=0),
    )
    redis = fakeredis.FakeRedis()
    ingest._page_block_degrade, ingest._redis = True, redis

    assert (await ingest._ta_fetch(1, page=True, reviews=False))[0] is None
    assert (await ingest._ta_fetch(2, page=True, reviews=False))[0] is None
    assert client.address_calls == [1]  # card 2 never asked
    assert redis.exists(atrativos_mod.TA_PAGES_BLOCKED_KEY)

    now[0] += atrativos_mod._PAGE_BLOCK_RETRY_SECONDS
    assert (await ingest._ta_fetch(5, page=True, reviews=False))[0] is None  # retry: 403
    assert (await ingest._ta_fetch(6, page=True, reviews=False))[0] is None  # re-armed
    assert client.address_calls == [1, 5]

    now[0] += atrativos_mod._PAGE_BLOCK_RETRY_SECONDS
    client._address_error = None
    client._fixture_address = {3: _MERCADO_PAGE}
    assert (await ingest._ta_fetch(3, page=True, reviews=False))[0] == _MERCADO_PAGE
    assert ingest._pages_blocked_at is None
    assert not redis.exists(atrativos_mod.TA_PAGES_BLOCKED_KEY)
    assert (await ingest._ta_fetch(4, page=True, reviews=False))[0] == {}
    assert client.address_calls == [1, 5, 3, 4]


@pytest.mark.asyncio
async def test_page_403_outside_a_sweep_still_raises() -> None:
    """The TA backfill run stops on a dead page session (its whole job is the page)."""
    from brave.config.settings import TripAdvisorConfig
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest
    from brave.domains.tripadvisor.client import SessionExpiredError

    ingest = TripAdvisorAtrativosIngest(
        ta_client=FakeTripAdvisorClient(address_error=SessionExpiredError("403")),
        session=MagicMock(), config=_make_config(), ibge_records=[],
        ta_config=TripAdvisorConfig(page_throttle_seconds=0),
    )
    with pytest.raises(SessionExpiredError):
        await ingest._ta_fetch(1, page=True, reviews=False)


@pytest.fixture
def tx_session(db_engine):
    from sqlalchemy.orm import Session

    conn = db_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.mark.integration
def test_resolver_version_releases_the_v7_quarantines(tx_session) -> None:
    """v8 (TA page's canonical URL city): a card quarantined as ibge_unmatched under v7 is
    retried; one stamped with the CURRENT version is skipped (it would fail the same)."""
    from brave.core.models import PoisonQuarantine
    from brave.domains.tripadvisor.atrativos import (
        _IBGE_UNMATCHED_TASK,
        IBGE_RESOLVER_VERSION,
        TripAdvisorAtrativosIngest,
    )

    assert IBGE_RESOLVER_VERSION == 8
    for location_id, version in (("990001", 7), ("990002", IBGE_RESOLVER_VERSION)):
        tx_session.add(PoisonQuarantine(
            task_name=_IBGE_UNMATCHED_TASK,
            error_message="ibge_unmatched",
            payload={"uf": "AL", "locationId": location_id, "name": "x",
                     "resolver_version": version},
        ))
    tx_session.flush()
    ingest = TripAdvisorAtrativosIngest(
        ta_client=MagicMock(), session=tx_session, config=_make_config(), ibge_records=[]
    )
    cards = [{"locationId": 990001}, {"locationId": 990002}]
    assert ingest._already_synced(cards, run_rio=True) == {"tripadvisor:attraction:990002"}


@pytest.mark.asyncio
async def test_produce_starts_at_start_page_and_tracks_resume_page() -> None:
    """A time-budget continuation lists from its start_page; resume_page is the page in flight."""
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    card = _make_card()
    fake_client = FakeTripAdvisorClient(
        gql_pages=[(2400, [card])], geo_ids={"MG": _GEO_ID_MG}
    )
    with (
        patch("brave.domains.tripadvisor.atrativos.store_raw") as mock_store_raw,
        patch("brave.domains.tripadvisor.atrativos.process_nascente_record"),
    ):
        mock_store_raw.return_value = MagicMock(id=uuid.uuid4())
        ingest = TripAdvisorAtrativosIngest(
            ta_client=fake_client,
            session=MagicMock(),
            config=_make_config(),
            ibge_records=_IBGE_RECORDS,
            destino_rio_map=_DESTINO_RIO_MAP,
        )
        await ingest.produce("MG", run_rio=False, start_page=81)

    assert fake_client.paginated_gql_calls[0]["start_page"] == 81
    assert ingest.resume_page == 81  # offset 2400 = page 81
