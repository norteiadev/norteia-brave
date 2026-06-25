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
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from brave.config.settings import ScoreConfig
from brave.lanes.tripadvisor.ibge import IbgeMunicipio
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
        threshold_dlq=40.0,
        score_version="v1.1",
        mar_ready_atualidade_bar=70.0,
        mar_ready_corrob_bar=60.0,
    )


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
