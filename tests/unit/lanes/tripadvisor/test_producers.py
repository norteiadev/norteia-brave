"""Tests for TripAdvisor destinos + atrativos producers (TA-02, TA-03).

All tests are 100% offline: FakeTripAdvisorClient provides fixture data,
store_raw and process_nascente_record are mocked (no DB required).

Producer tests verify:
  - Destinos: store_raw called with source="tripadvisor" and origem_value=65
  - Atrativos: parent_rio_id and parent_source_ref carried in payload
  - Atrativos: quarantine called with "parent_destino_absent" when map is empty
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brave.config.settings import ScoreConfig
from brave.lanes.tripadvisor.ibge import IbgeMunicipio
from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIXTURE_DESTINO: dict[str, Any] = {
    "locationId": "303506",
    "name": "Salvador",
    "lat": -12.9714,
    "lng": -38.5014,
    "reviewCount": 1500,
    "rating": 4.7,
    "mostRecentReviewDate": "2026-01-15",
    "address": "Salvador, BA",
    "category": "destination",
    "description": "Bahia capital",
}

_FIXTURE_ATRATIVO: dict[str, Any] = {
    "locationId": "99999",
    "name": "Elevador Lacerda",
    "lat": -12.9714,
    "lng": -38.5142,
    "reviewCount": 200,
    "rating": 4.5,
    "mostRecentReviewDate": "2025-11-01",
    "address": "Praça Cairu, Salvador, BA",
    "category": "attraction",
    "description": "Historic elevator in Salvador",
    "parentLocationId": "303506",
}

_IBGE_RECORDS = [
    IbgeMunicipio("2927408", "Salvador", "BA", -12.9714, -38.5014),
    IbgeMunicipio("3550308", "São Paulo", "SP", -23.5505, -46.6333),
]


def _make_fake_client() -> FakeTripAdvisorClient:
    return FakeTripAdvisorClient(
        fixture_destinations={"BA": [_FIXTURE_DESTINO]},
        fixture_attractions={303506: [_FIXTURE_ATRATIVO]},
        geo_ids={"BA": 303506},
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
# Destinos producer tests
# ---------------------------------------------------------------------------


class TestTripAdvisorDestinosIngest:
    @pytest.mark.asyncio
    async def test_destinos_produce_writes_nascente(self) -> None:
        """produce() calls store_raw with source='tripadvisor' and origem_value=65."""
        from brave.lanes.tripadvisor.destinos import TripAdvisorDestinosIngest

        fake_client = _make_fake_client()
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.lanes.tripadvisor.destinos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.destinos.process_nascente_record"),
        ):
            # Mock store_raw to return a fake NascenteRecord
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorDestinosIngest(
                ta_client=fake_client,
                session=mock_session,
                config=config,
                ibge_records=_IBGE_RECORDS,
            )
            await ingest.produce("BA", run_rio=True)

        # Verify store_raw was called with source='tripadvisor'
        assert mock_store_raw.called, "store_raw should have been called"
        call_kwargs = mock_store_raw.call_args.kwargs
        assert call_kwargs["source"] == "tripadvisor"
        assert call_kwargs["entity_type"] == "destination"
        assert call_kwargs["uf"] == "BA"

        # Verify origem_value=65 in payload
        payload = call_kwargs["payload"]
        assert payload["origem_value"] == 65.0, f"Expected 65.0, got {payload.get('origem_value')}"

        # Verify source_ref format
        source_ref = call_kwargs["source_ref"]
        assert source_ref.startswith("tripadvisor:destination:"), f"Bad source_ref: {source_ref}"

    @pytest.mark.asyncio
    async def test_destinos_produce_run_rio_false_skips_rio(self) -> None:
        """produce(run_rio=False) calls store_raw but not process_nascente_record."""
        from brave.lanes.tripadvisor.destinos import TripAdvisorDestinosIngest

        fake_client = _make_fake_client()
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.lanes.tripadvisor.destinos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.destinos.process_nascente_record") as mock_rio,
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorDestinosIngest(
                ta_client=fake_client,
                session=mock_session,
                config=config,
                ibge_records=_IBGE_RECORDS,
            )
            await ingest.produce("BA", run_rio=False)

        assert mock_store_raw.called
        assert not mock_rio.called, "process_nascente_record should NOT be called when run_rio=False"


# ---------------------------------------------------------------------------
# Atrativos producer tests
# ---------------------------------------------------------------------------


class TestTripAdvisorAtrativosIngest:
    @pytest.mark.asyncio
    async def test_atrativo_carries_parent_rio_id(self) -> None:
        """produce() with destino_rio_map populated includes parent_rio_id in payload."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        fake_client = _make_fake_client()
        mock_session = MagicMock()
        config = _make_config()

        parent_rio_id = uuid.uuid4()
        # Map ibge_code "2927408" (Salvador) → (rio_id, source_ref)
        destino_rio_map: dict[str, tuple[uuid.UUID, str]] = {
            "2927408": (parent_rio_id, "tripadvisor:destination:303506"),
        }

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
                destino_rio_map=destino_rio_map,
            )
            await ingest.produce("BA", run_rio=True)

        assert mock_store_raw.called, "store_raw should have been called"
        payload = mock_store_raw.call_args.kwargs["payload"]
        assert "parent_rio_id" in payload, "parent_rio_id must be in payload"
        assert payload["parent_rio_id"] == str(parent_rio_id)
        assert payload["parent_source_ref"] == "tripadvisor:destination:303506"

    @pytest.mark.asyncio
    async def test_atrativo_parent_absent_quarantines(self) -> None:
        """produce() with empty destino_rio_map quarantines with 'parent_destino_absent'."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        fake_client = _make_fake_client()
        mock_session = MagicMock()
        config = _make_config()

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw"),
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
            patch("brave.lanes.tripadvisor.atrativos.quarantine_poison") as mock_quarantine,
        ):
            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_client,
                session=mock_session,
                config=config,
                ibge_records=_IBGE_RECORDS,
                destino_rio_map={},  # empty map → no parent
            )
            await ingest.produce("BA", run_rio=True)

        assert mock_quarantine.called, "quarantine_poison should be called for missing parent"
        call_kwargs = mock_quarantine.call_args.kwargs
        assert "parent_destino_absent" in str(call_kwargs.get("error", "")) or \
               "parent_destino_absent" in str(call_kwargs.get("task_name", "")), (
            f"Expected 'parent_destino_absent' in quarantine call, got kwargs={call_kwargs}"
        )

    @pytest.mark.asyncio
    async def test_atrativo_source_ref_format(self) -> None:
        """produce() uses source_ref='tripadvisor:attraction:{locationId}'."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        fake_client = _make_fake_client()
        mock_session = MagicMock()
        config = _make_config()

        parent_rio_id = uuid.uuid4()
        destino_rio_map = {
            "2927408": (parent_rio_id, "tripadvisor:destination:303506"),
        }

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
                destino_rio_map=destino_rio_map,
            )
            await ingest.produce("BA", run_rio=True)

        source_ref = mock_store_raw.call_args.kwargs["source_ref"]
        assert source_ref.startswith("tripadvisor:attraction:"), f"Bad source_ref: {source_ref}"
        assert "99999" in source_ref, f"Expected locationId '99999' in source_ref: {source_ref}"
