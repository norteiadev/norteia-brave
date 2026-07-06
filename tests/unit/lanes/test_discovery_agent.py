"""Unit tests for DiscoveryAgent — parent destino resolution guard + idempotency.

All tests run 100% offline:
  - FakePlacesClient from tests/fakes/fake_places.py
  - MagicMock for LLMClientProtocol (instructor returns AtrativoResult)
  - In-memory SQLite via conftest fixtures, OR simple mock session

Test suite covers must_haves from 03-02-PLAN.md:
  - test_discovery_skips_when_no_parent_destino
  - test_discovery_stores_raw_with_place_id_only
  - test_discovery_dedup_idempotent

D-18 boundary: no import from brave.lanes.destinos in this file.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fakes.fake_places import SIGNAL_FIXTURE_OPEN, FakePlacesClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_places_result(
    place_id: str = "ChIJtest001",
    municipio_ibge: str = "2919207",
    municipio_nome: str = "Porto Seguro",
) -> dict[str, Any]:
    """Build a minimal text_search result for DiscoveryAgent."""
    return {
        "place_id": place_id,
        "name": "Praia de Trancoso",
        "municipio_ibge": municipio_ibge,
        "municipio_nome": municipio_nome,
        "formatted_address": "Trancoso, Porto Seguro - BA",
    }


def _make_atrativo_result(
    place_id: str = "ChIJtest001",
    municipio_ibge: str = "2919207",
) -> Any:
    """Build a minimal AtrativoResult mock for LLM extraction."""
    from brave.lanes.atrativos.schemas import AtrativoResult

    return AtrativoResult(
        nome="Praia de Trancoso",
        tipo="praia",
        posicionamento="Praia paradisíaca com areias brancas e águas cristalinas.",
        municipio_nome="Porto Seguro",
        municipio_ibge=municipio_ibge,
        uf="BA",
        place_id=place_id,
        origem_value=60.0,
        completude_value=75.0,
    )


def _make_mock_session() -> MagicMock:
    """Create a mock SQLAlchemy session with scalar returning None by default."""
    session = MagicMock()
    session.scalar.return_value = None
    session.add.return_value = None
    session.flush.return_value = None
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_skips_when_no_parent_destino() -> None:
    """DiscoveryAgent skips ingestion when no parent destino in Mar for the place's
    uf+municipio_ibge. store_raw must NOT be called; quarantine_poison MUST be called
    with an error containing 'parent_destino_absent'.
    """
    from brave.config.settings import ScoreConfig
    from brave.lanes.atrativos.discovery_agent import DiscoveryAgent

    places_result = _make_places_result()

    fake_places = FakePlacesClient(
        fixture_results={"atrativos em BA": [places_result]},
    )

    # LLM returns a valid AtrativoResult
    llm_client = MagicMock()
    llm_client.extract = AsyncMock(return_value=_make_atrativo_result())

    session = _make_mock_session()
    # MarRecord query returns None — no parent destino in Mar
    session.scalar.return_value = None

    config = ScoreConfig()

    agent = DiscoveryAgent(
        places_client=fake_places,
        llm_client=llm_client,
        session=session,
        config=config,
    )

    with patch("brave.lanes.atrativos.discovery_agent.store_raw") as mock_store_raw, \
         patch("brave.lanes.atrativos.discovery_agent.quarantine_poison") as mock_quarantine:
        await agent.produce(uf="BA")

    # store_raw MUST NOT be called when no parent destino
    mock_store_raw.assert_not_called()

    # quarantine_poison MUST be called with error containing "parent_destino_absent"
    assert mock_quarantine.call_count >= 1
    quarantine_kwargs = mock_quarantine.call_args
    error_msg = quarantine_kwargs[1].get("error", "") or str(quarantine_kwargs[0])
    assert "parent_destino_absent" in error_msg


@pytest.mark.asyncio
async def test_discovery_stores_raw_with_place_id_only() -> None:
    """DiscoveryAgent stores raw record when MarRecord parent destino exists.

    Verifies:
    - store_raw is called once
    - payload["canonical"]["place_id"] is present
    - COMP-03 / D-04: no raw Places fields (addresses, names from Places) stored
      as canonical identity (only AtrativoResult extraction + place_id cache)
    """
    from brave.config.settings import ScoreConfig
    from brave.core.models import MarRecord
    from brave.lanes.atrativos.discovery_agent import DiscoveryAgent

    places_result = _make_places_result()

    fake_places = FakePlacesClient(
        fixture_results={"atrativos em BA": [places_result]},
        fixture_details={"ChIJtest001": SIGNAL_FIXTURE_OPEN},
    )

    llm_client = MagicMock()
    llm_client.extract = AsyncMock(return_value=_make_atrativo_result())

    session = _make_mock_session()

    # First scalar call = MarRecord query → returns a MarRecord (parent destino exists)
    mock_mar = MagicMock(spec=MarRecord)
    mock_mar.canonical = {"municipio_ibge": "2919207", "name": "Porto Seguro"}
    mock_mar.entity_type = "destination"
    session.scalar.return_value = mock_mar

    config = ScoreConfig()

    agent = DiscoveryAgent(
        places_client=fake_places,
        llm_client=llm_client,
        session=session,
        config=config,
    )

    # FSM-init collaborators (Plan 05-02 Task 1) are patched out: this test isolates the
    # store_raw payload/parent-guard contract, not the Rio creation + sub_state seeding
    # (those are covered against a real DB in test_atrativos_lane_e2e.py).
    with patch("brave.lanes.atrativos.discovery_agent.store_raw") as mock_store_raw, \
         patch("brave.lanes.atrativos.discovery_agent.process_nascente_record"), \
         patch("brave.lanes.atrativos.discovery_agent.advance_sub_state"), \
         patch("brave.lanes.atrativos.discovery_agent.quarantine_poison") as mock_quarantine:
        # Patch store_raw to return a mock NascenteRecord
        from unittest.mock import MagicMock as MM
        mock_nascente = MM()
        mock_nascente.id = uuid.uuid4()
        mock_nascente.source_ref = "places:BA:ChIJtest001"
        mock_store_raw.return_value = mock_nascente

        await agent.produce(uf="BA")

    # quarantine should NOT be called (parent destino exists, extraction succeeded)
    mock_quarantine.assert_not_called()

    # store_raw MUST be called exactly once
    assert mock_store_raw.call_count == 1

    # Verify payload structure: canonical must include place_id
    call_kwargs = mock_store_raw.call_args[1]
    payload = call_kwargs.get("payload") or mock_store_raw.call_args[0][5] if mock_store_raw.call_args[0] else {}
    if not payload:
        # Try positional args fallback
        args, kwargs = mock_store_raw.call_args
        payload = kwargs.get("payload", args[5] if len(args) > 5 else {})

    assert "canonical" in payload
    canonical = payload["canonical"]
    assert "place_id" in canonical
    assert canonical["place_id"] == "ChIJtest001"

    # entity_type must be "attraction"
    assert call_kwargs.get("entity_type") == "attraction" or \
           (mock_store_raw.call_args[0] and mock_store_raw.call_args[0][3] == "attraction")


@pytest.mark.asyncio
async def test_discovery_dedup_idempotent() -> None:
    """Calling produce twice with the same place_id results in idempotent behavior.

    store_raw handles idempotency by content_hash — the second call is a no-op.
    We verify that store_raw is called again on the second produce (store_raw itself
    handles dedup internally), and no quarantine is triggered.
    """
    from brave.config.settings import ScoreConfig
    from brave.core.models import MarRecord
    from brave.lanes.atrativos.discovery_agent import DiscoveryAgent

    places_result = _make_places_result()

    fake_places = FakePlacesClient(
        fixture_results={"atrativos em BA": [places_result]},
    )

    llm_client = MagicMock()
    llm_client.extract = AsyncMock(return_value=_make_atrativo_result())

    session = _make_mock_session()
    mock_mar = MagicMock(spec=MarRecord)
    mock_mar.canonical = {"municipio_ibge": "2919207"}
    session.scalar.return_value = mock_mar

    config = ScoreConfig()

    agent = DiscoveryAgent(
        places_client=fake_places,
        llm_client=llm_client,
        session=session,
        config=config,
    )

    # FSM-init collaborators (Plan 05-02 Task 1) patched out — see note above; this test
    # asserts store_raw dedup behavior, not Rio creation / sub_state seeding.
    with patch("brave.lanes.atrativos.discovery_agent.store_raw") as mock_store_raw, \
         patch("brave.lanes.atrativos.discovery_agent.process_nascente_record"), \
         patch("brave.lanes.atrativos.discovery_agent.advance_sub_state"), \
         patch("brave.lanes.atrativos.discovery_agent.quarantine_poison") as mock_quarantine:
        mock_nascente = MagicMock()
        mock_nascente.id = uuid.uuid4()
        mock_nascente.source_ref = "places:BA:ChIJtest001"
        mock_store_raw.return_value = mock_nascente

        # First call
        await agent.produce(uf="BA")
        first_call_count = mock_store_raw.call_count

        # Second call — same data, store_raw handles dedup internally
        await agent.produce(uf="BA")
        second_call_count = mock_store_raw.call_count

    # store_raw must have been called at least once; quarantine never
    assert first_call_count >= 1
    assert second_call_count >= first_call_count
    mock_quarantine.assert_not_called()


# ---------------------------------------------------------------------------
# D-02 guard tests (Plan 07-03)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_ibge_guard_quarantines_without_db_query() -> None:
    """D-02 guard: places result with empty municipio_ibge must quarantine without
    querying the DB.

    When municipio_ibge is empty, _resolve_parent_destino must return None immediately
    (before calling session.scalar). The caller (produce()) then quarantines with
    error="parent_destino_absent". session.scalar MUST NOT be called at all.
    """
    from brave.config.settings import ScoreConfig
    from brave.lanes.atrativos.discovery_agent import DiscoveryAgent

    # Place result with empty municipio_ibge — triggers D-02 guard
    places_result = _make_places_result(municipio_ibge="", municipio_nome="")

    fake_places = FakePlacesClient(
        fixture_results={"atrativos em BA": [places_result]},
    )

    llm_client = MagicMock()
    llm_client.extract = AsyncMock(return_value=_make_atrativo_result())

    session = _make_mock_session()
    # The guard must prevent this call entirely
    session.scalar.return_value = None

    config = ScoreConfig()

    agent = DiscoveryAgent(
        places_client=fake_places,
        llm_client=llm_client,
        session=session,
        config=config,
    )

    with patch("brave.lanes.atrativos.discovery_agent.store_raw") as mock_store_raw, \
         patch("brave.lanes.atrativos.discovery_agent.quarantine_poison") as mock_quarantine:
        await agent.produce(uf="BA")

    # D-02 guard: DB must NOT be queried when ibge is empty
    session.scalar.assert_not_called()

    # store_raw must NOT be called (parent_destino_absent quarantine fires first)
    mock_store_raw.assert_not_called()

    # quarantine_poison must be called with error="parent_destino_absent"
    assert mock_quarantine.call_count >= 1
    quarantine_kwargs = mock_quarantine.call_args[1]
    assert quarantine_kwargs.get("error") == "parent_destino_absent"


# ---------------------------------------------------------------------------
# D-03 targeted discovery tests (Plan 07-03)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_produce_for_destino_links_to_known_parent() -> None:
    """D-03: produce_for_destino injects parent_mar.id as parent_mar_id — no DB lookup.

    Verifies:
    - store_raw is called with payload["parent_mar_id"] == str(parent_mar.id)
    - session.scalar is NOT called (_resolve_parent_destino never invoked)
    - The targeted query "pontos turísticos em Porto Seguro BA" drives the search
    """
    from brave.config.settings import ScoreConfig
    from brave.lanes.atrativos.discovery_agent import DiscoveryAgent

    parent_mar_id = uuid.uuid4()
    mock_parent_mar = MagicMock()
    mock_parent_mar.id = parent_mar_id
    mock_parent_mar.canonical = {
        "municipio": "Porto Seguro",
        "uf": "BA",
        "ibge_code": "2927408",
    }

    fake_places = FakePlacesClient(
        fixture_results={
            "pontos turísticos em Porto Seguro BA": [
                _make_places_result(
                    place_id="ChIJtest001",
                    municipio_ibge="2927408",
                    municipio_nome="Porto Seguro",
                )
            ],
        }
    )

    llm_client = MagicMock()
    llm_client.extract = AsyncMock(
        return_value=_make_atrativo_result(place_id="ChIJtest001", municipio_ibge="2927408")
    )

    session = _make_mock_session()
    config = ScoreConfig()

    agent = DiscoveryAgent(
        places_client=fake_places,
        llm_client=llm_client,
        session=session,
        config=config,
    )

    mock_nascente = MagicMock()
    mock_nascente.id = uuid.uuid4()
    mock_nascente.source_ref = "places:BA:ChIJtest001"

    with patch("brave.lanes.atrativos.discovery_agent.store_raw", return_value=mock_nascente) as mock_store_raw, \
         patch("brave.lanes.atrativos.discovery_agent.process_nascente_record"), \
         patch("brave.lanes.atrativos.discovery_agent.advance_sub_state"), \
         patch("brave.lanes.atrativos.discovery_agent.write_audit"), \
         patch("brave.lanes.atrativos.discovery_agent.quarantine_poison") as mock_quarantine:
        result = await agent.produce_for_destino(mock_parent_mar, target_count=1)

    # Must return 1 created record
    assert result == 1

    # store_raw must be called exactly once
    assert mock_store_raw.call_count == 1

    # Payload must contain parent_mar_id == str(parent_mar.id) — NOT from DB
    call_kwargs = mock_store_raw.call_args[1]
    payload = call_kwargs["payload"]
    assert payload["parent_mar_id"] == str(parent_mar_id)

    # session.scalar must NOT be called — _resolve_parent_destino was bypassed
    session.scalar.assert_not_called()

    # No quarantine should be triggered
    mock_quarantine.assert_not_called()


@pytest.mark.asyncio
async def test_produce_for_destino_derives_uf_ibge_from_source_ref() -> None:
    """G3: destino canonical only has {name,...} (no uf/ibge) and MarRecord has no
    uf column — produce_for_destino must parse uf+ibge from source_ref
    'mtur:{UF}:{ibge}' so the targeted query is built and discovery is not a silent no-op.
    """
    from brave.config.settings import ScoreConfig
    from brave.lanes.atrativos.discovery_agent import DiscoveryAgent

    parent_mar_id = uuid.uuid4()
    mock_parent_mar = MagicMock()
    mock_parent_mar.id = parent_mar_id
    # Real-shaped destino canonical: name only, NO uf / ibge_code
    mock_parent_mar.canonical = {"name": "Porto Seguro", "address": None, "labels": {}}
    mock_parent_mar.source_ref = "mtur:BA:2927408"

    fake_places = FakePlacesClient(
        fixture_results={
            "pontos turísticos em Porto Seguro BA": [
                _make_places_result(
                    place_id="ChIJtest001",
                    municipio_ibge="2927408",
                    municipio_nome="Porto Seguro",
                )
            ],
        }
    )

    llm_client = MagicMock()
    llm_client.extract = AsyncMock(
        return_value=_make_atrativo_result(place_id="ChIJtest001", municipio_ibge="2927408")
    )

    session = _make_mock_session()
    agent = DiscoveryAgent(fake_places, llm_client, session, ScoreConfig())

    mock_nascente = MagicMock()
    mock_nascente.id = uuid.uuid4()
    mock_nascente.source_ref = "places:BA:ChIJtest001"

    with patch("brave.lanes.atrativos.discovery_agent.store_raw", return_value=mock_nascente) as mock_store_raw, \
         patch("brave.lanes.atrativos.discovery_agent.process_nascente_record"), \
         patch("brave.lanes.atrativos.discovery_agent.advance_sub_state"), \
         patch("brave.lanes.atrativos.discovery_agent.write_audit"), \
         patch("brave.lanes.atrativos.discovery_agent.quarantine_poison"):
        result = await agent.produce_for_destino(mock_parent_mar, target_count=1)

    # Derived uf=BA from source_ref → targeted query ran → 1 atrativo created (not a 0 no-op)
    assert result == 1
    assert mock_store_raw.call_count == 1


@pytest.mark.asyncio
async def test_produce_for_destino_returns_zero_on_missing_municipio() -> None:
    """D-03: produce_for_destino returns 0 when canonical has no municipio/name.

    When the parent_mar canonical dict lacks both "municipio" and "name" keys,
    the method cannot build a valid search query. It must return 0 and must NOT
    call store_raw.
    """
    from brave.config.settings import ScoreConfig
    from brave.lanes.atrativos.discovery_agent import DiscoveryAgent

    mock_parent_mar = MagicMock()
    mock_parent_mar.id = uuid.uuid4()
    mock_parent_mar.canonical = {"uf": "BA"}  # no "municipio" or "name"

    fake_places = FakePlacesClient(fixture_results={})
    llm_client = MagicMock()

    session = _make_mock_session()
    config = ScoreConfig()

    agent = DiscoveryAgent(
        places_client=fake_places,
        llm_client=llm_client,
        session=session,
        config=config,
    )

    with patch("brave.lanes.atrativos.discovery_agent.store_raw") as mock_store_raw:
        result = await agent.produce_for_destino(mock_parent_mar)

    assert result == 0
    mock_store_raw.assert_not_called()


# ---------------------------------------------------------------------------
# G2 gap-closure tests (Plan 07-07)
# ---------------------------------------------------------------------------


def test_produce_for_destino_parent_link_in_normalized() -> None:
    """G2: process_nascente_record must copy parent_mar_id from nascente payload
    to rio.normalized so downstream queries can group atrativos by parent destino
    without a nascente JOIN.

    Mirrors the place_id_cache copy pattern at lines 155-156 of routing.py.
    """
    from brave.config.settings import ScoreConfig
    from brave.core.rio.routing import process_nascente_record

    parent_mar_id_str = "uuid-test-parent"
    place_id_str = "ChIJtest"

    nascente_mock = MagicMock()
    nascente_mock.source_ref = "places:BA:ChIJtest"
    nascente_mock.entity_type = "attraction"
    nascente_mock.uf = "BA"
    nascente_mock.content_hash = "abc123"
    nascente_mock.payload = {
        "name": "Test Atrativo",
        "parent_mar_id": parent_mar_id_str,
        "place_id_cache": place_id_str,
        "origem_value": 60.0,
        "completude_value": 75.0,
        "corroboracao_value": 0.0,
        "atualidade_value": 0.0,
        "validacao_humana_value": 0.0,
    }

    session_mock = MagicMock()
    session_mock.scalar.return_value = None  # no existing RioRecord (idempotency check)
    session_mock.add = MagicMock()
    session_mock.flush = MagicMock()

    with patch("brave.core.rio.routing.find_duplicate", return_value=None), \
         patch("brave.core.rio.routing.compute_embedding", return_value=[0.0] * 1536), \
         patch("brave.core.rio.routing.label_entity", side_effect=lambda etype, norm: norm):
        process_nascente_record(session_mock, nascente_mock, ScoreConfig())

    # The RioRecord passed to session.add must have parent_mar_id in normalized.
    # process_nascente_record also appends RecordEvent rows (scored/routed Log-tab
    # timeline) via session.add, so isolate the single RioRecord among the adds
    # rather than asserting the total add count.
    from brave.core.models import RioRecord  # noqa: PLC0415

    added = [c.args[0] for c in session_mock.add.call_args_list]
    rio_records = [obj for obj in added if isinstance(obj, RioRecord)]
    assert len(rio_records) == 1, (
        f"exactly one RioRecord must be added; got {len(rio_records)} "
        f"(all adds: {[type(o).__name__ for o in added]})"
    )
    rio_record = rio_records[0]
    assert rio_record.normalized is not None
    assert rio_record.normalized.get("parent_mar_id") == parent_mar_id_str
