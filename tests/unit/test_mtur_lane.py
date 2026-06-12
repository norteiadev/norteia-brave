"""Tests for MturSeedIngest lane — TDD RED phase for Task 1 (plan 02-05).

All tests run fully offline (no DB, no network).
Uses FakeMturClient and a mock session to verify payload construction.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock, call, patch

import pytest

from tests.fakes.fake_mtur import FakeMturClient


# ---------------------------------------------------------------------------
# Smoke imports (RED: will fail until mtur.py is created)
# ---------------------------------------------------------------------------


def test_mtur_seed_ingest_importable():
    """MturSeedIngest is importable from brave.lanes.destinos.mtur."""
    from brave.lanes.destinos.mtur import MturSeedIngest  # noqa: F401

    assert MturSeedIngest is not None


def test_produce_signature_matches_lane_protocol():
    """produce(uf) is async and matches LaneProtocol signature."""
    from brave.lanes.destinos.mtur import MturSeedIngest

    sig = inspect.signature(MturSeedIngest.produce)
    assert "uf" in sig.parameters, "produce must accept uf param"
    assert inspect.iscoroutinefunction(
        MturSeedIngest.produce
    ), "produce must be async"


def test_mtur_atualidade_constant_exists():
    """MTUR_ATUALIDADE_DEFAULT constant is defined and is 70.0."""
    from brave.lanes.destinos.mtur import MTUR_ATUALIDADE_DEFAULT

    assert MTUR_ATUALIDADE_DEFAULT == 70.0


def test_completude_from_fields_all_present():
    """_completude_from_fields returns 100.0 when all four fields are non-empty."""
    from brave.lanes.destinos.mtur import _completude_from_fields

    mun = {"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "Oferta Principal", "uf": "BA"}
    assert _completude_from_fields(mun) == 100.0


def test_completude_from_fields_three_present():
    """_completude_from_fields returns 75.0 when three fields are non-empty."""
    from brave.lanes.destinos.mtur import _completude_from_fields

    mun = {"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "Oferta Principal", "uf": ""}
    assert _completude_from_fields(mun) == 75.0


def test_completude_from_fields_two_present():
    """_completude_from_fields returns 50.0 when two fields are non-empty."""
    from brave.lanes.destinos.mtur import _completude_from_fields

    mun = {"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "", "uf": ""}
    assert _completude_from_fields(mun) == 50.0


def test_completude_from_fields_one_present():
    """_completude_from_fields returns 25.0 when only one field is non-empty."""
    from brave.lanes.destinos.mtur import _completude_from_fields

    mun = {"ibge_code": "2927408", "name": "", "categoria": "", "uf": ""}
    assert _completude_from_fields(mun) == 25.0


def test_produce_calls_fetch_municipalities():
    """produce("BA") calls mtur_client.fetch_municipalities("BA")."""
    from brave.lanes.destinos.mtur import MturSeedIngest
    from brave.config.settings import ScoreConfig

    fake_mtur = FakeMturClient(fixtures=[
        {"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "Oferta Principal", "uf": "BA"},
    ])

    mock_session = MagicMock()
    mock_session.scalar.return_value = None  # no existing records
    mock_session.flush.return_value = None

    # Mock store_raw and process_nascente_record to isolate unit under test
    with patch("brave.lanes.destinos.mtur.store_raw") as mock_store, \
         patch("brave.lanes.destinos.mtur.process_nascente_record") as mock_process:

        mock_nascente = MagicMock()
        mock_nascente.id = "fake-uuid"
        mock_store.return_value = mock_nascente
        mock_process.return_value = MagicMock()

        lane = MturSeedIngest(mtur_client=fake_mtur, session=mock_session, config=ScoreConfig())
        asyncio.run(lane.produce("BA"))

    assert fake_mtur.calls == ["BA"]


def test_produce_calls_store_raw_with_correct_source_ref():
    """produce writes Nascente with source='mtur' and source_ref='mtur:{uf}:{ibge_code}'."""
    from brave.lanes.destinos.mtur import MturSeedIngest
    from brave.config.settings import ScoreConfig

    fake_mtur = FakeMturClient(fixtures=[
        {"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "Oferta Principal", "uf": "BA"},
    ])
    mock_session = MagicMock()

    with patch("brave.lanes.destinos.mtur.store_raw") as mock_store, \
         patch("brave.lanes.destinos.mtur.process_nascente_record") as mock_process:

        mock_nascente = MagicMock()
        mock_store.return_value = mock_nascente
        mock_process.return_value = MagicMock()

        lane = MturSeedIngest(mtur_client=fake_mtur, session=mock_session, config=ScoreConfig())
        asyncio.run(lane.produce("BA"))

    assert mock_store.called
    _, kwargs = mock_store.call_args
    assert kwargs.get("source") == "mtur" or mock_store.call_args.args[1] == "mtur"
    # Accept both positional and keyword call styles
    call_args = mock_store.call_args
    store_kwargs = {**dict(zip(["session", "source", "source_ref", "entity_type", "uf", "payload"],
                                call_args.args)), **call_args.kwargs}
    assert store_kwargs["source"] == "mtur"
    assert store_kwargs["source_ref"] == "mtur:BA:2927408"
    assert store_kwargs["entity_type"] == "destination"
    assert store_kwargs["uf"] == "BA"


def test_produce_payload_includes_all_value_fields():
    """Nascente payload includes all five *_value criterion fields."""
    from brave.lanes.destinos.mtur import MturSeedIngest
    from brave.config.settings import ScoreConfig

    fake_mtur = FakeMturClient(fixtures=[
        {"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "Oferta Principal", "uf": "BA"},
    ])
    mock_session = MagicMock()

    with patch("brave.lanes.destinos.mtur.store_raw") as mock_store, \
         patch("brave.lanes.destinos.mtur.process_nascente_record") as mock_process:

        mock_nascente = MagicMock()
        mock_store.return_value = mock_nascente
        mock_process.return_value = MagicMock()

        lane = MturSeedIngest(mtur_client=fake_mtur, session=mock_session, config=ScoreConfig())
        asyncio.run(lane.produce("BA"))

    call_args = mock_store.call_args
    store_kwargs = {**dict(zip(["session", "source", "source_ref", "entity_type", "uf", "payload"],
                                call_args.args)), **call_args.kwargs}
    payload = store_kwargs["payload"]

    assert "origem_value" in payload
    assert "completude_value" in payload
    assert "corroboracao_value" in payload
    assert "atualidade_value" in payload
    assert "validacao_humana_value" in payload

    assert payload["origem_value"] == 100.0
    assert payload["corroboracao_value"] == 0.0
    assert payload["atualidade_value"] == 70.0
    assert payload["validacao_humana_value"] == 0.0


def test_produce_payload_includes_canonical_with_ibge_code():
    """Nascente payload includes canonical dict with ibge_code field (Pact contract)."""
    from brave.lanes.destinos.mtur import MturSeedIngest
    from brave.config.settings import ScoreConfig

    fake_mtur = FakeMturClient(fixtures=[
        {"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "Oferta Principal", "uf": "BA"},
    ])
    mock_session = MagicMock()

    with patch("brave.lanes.destinos.mtur.store_raw") as mock_store, \
         patch("brave.lanes.destinos.mtur.process_nascente_record") as mock_process:

        mock_nascente = MagicMock()
        mock_store.return_value = mock_nascente
        mock_process.return_value = MagicMock()

        lane = MturSeedIngest(mtur_client=fake_mtur, session=mock_session, config=ScoreConfig())
        asyncio.run(lane.produce("BA"))

    call_args = mock_store.call_args
    store_kwargs = {**dict(zip(["session", "source", "source_ref", "entity_type", "uf", "payload"],
                                call_args.args)), **call_args.kwargs}
    payload = store_kwargs["payload"]

    assert "canonical" in payload
    canonical = payload["canonical"]
    assert canonical["ibge_code"] == "2927408"
    assert canonical["uf"] == "BA"
    assert canonical["name"] == "Porto Seguro"


def test_produce_payload_includes_municipio_id():
    """Nascente payload includes municipio_id = IBGE code (D-10)."""
    from brave.lanes.destinos.mtur import MturSeedIngest
    from brave.config.settings import ScoreConfig

    fake_mtur = FakeMturClient(fixtures=[
        {"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "Oferta Principal", "uf": "BA"},
    ])
    mock_session = MagicMock()

    with patch("brave.lanes.destinos.mtur.store_raw") as mock_store, \
         patch("brave.lanes.destinos.mtur.process_nascente_record") as mock_process:

        mock_nascente = MagicMock()
        mock_store.return_value = mock_nascente
        mock_process.return_value = MagicMock()

        lane = MturSeedIngest(mtur_client=fake_mtur, session=mock_session, config=ScoreConfig())
        asyncio.run(lane.produce("BA"))

    call_args = mock_store.call_args
    store_kwargs = {**dict(zip(["session", "source", "source_ref", "entity_type", "uf", "payload"],
                                call_args.args)), **call_args.kwargs}
    payload = store_kwargs["payload"]

    assert payload["municipio_id"] == "2927408"


def test_produce_calls_process_nascente_record():
    """After store_raw, produce calls process_nascente_record."""
    from brave.lanes.destinos.mtur import MturSeedIngest
    from brave.config.settings import ScoreConfig

    fake_mtur = FakeMturClient(fixtures=[
        {"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "Oferta Principal", "uf": "BA"},
    ])
    mock_session = MagicMock()

    with patch("brave.lanes.destinos.mtur.store_raw") as mock_store, \
         patch("brave.lanes.destinos.mtur.process_nascente_record") as mock_process:

        mock_nascente = MagicMock()
        mock_store.return_value = mock_nascente
        mock_process.return_value = MagicMock()

        lane = MturSeedIngest(mtur_client=fake_mtur, session=mock_session, config=ScoreConfig())
        asyncio.run(lane.produce("BA"))

    assert mock_process.called
    # First arg is session, second is nascente
    proc_args = mock_process.call_args
    proc_kwargs = {**dict(zip(["session", "nascente", "config"], proc_args.args)), **proc_args.kwargs}
    assert proc_kwargs.get("nascente") is mock_nascente or proc_args.args[1] is mock_nascente


def test_no_imports_from_other_lanes():
    """MturSeedIngest does NOT import from brave.lanes.* (D-18 boundary)."""
    import importlib
    import sys

    # Remove cached module if present
    for key in list(sys.modules.keys()):
        if "brave.lanes.destinos.mtur" in key:
            del sys.modules[key]

    import brave.lanes.destinos.mtur as mtur_module

    module_source_file = mtur_module.__file__
    with open(module_source_file) as f:
        source = f.read()

    # Must not import from other lanes (D-18)
    # Allowed: brave.lanes.destinos.mtur itself (self-reference), brave.lanes.base (LaneProtocol comment)
    import_lines = [line.strip() for line in source.splitlines() if line.strip().startswith(("import brave.lanes", "from brave.lanes"))]
    # Filter: allow base (for type comment) but not other lane modules
    forbidden = [
        l for l in import_lines
        if "brave.lanes" in l and ".destinos.mtur" not in l and "brave.lanes.base" not in l
    ]
    assert not forbidden, f"D-18 violation: found forbidden lane imports: {forbidden}"
