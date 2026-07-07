"""Unit tests for the ``manual`` source domain (Phase G).

Fully offline: fakeredis for the engine mode / edit-lock, and store_raw /
process_nascente_record are patched so no DB is required.
"""

from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from brave.core import engine as collection_engine
from brave.domains import get_domain
from brave.domains.base import SourceDomain
from brave.domains.manual.controllers import MANUAL_DOMAIN, ManualDomain
from brave.domains.manual.exceptions import EditingLockedError
from brave.domains.manual.repositories import (
    MANUAL_ORIGEM_VALUE,
    MANUAL_VALIDACAO_HUMANA_VALUE,
    ManualRepository,
)
from brave.domains.manual.services import ManualService

# ---------------------------------------------------------------------------
# Registry + protocol conformance
# ---------------------------------------------------------------------------


def test_manual_domain_registered_and_conformant():
    domain = get_domain("manual")
    assert domain is MANUAL_DOMAIN
    assert domain.name == "manual"
    assert isinstance(domain, SourceDomain)
    assert set(domain.produces) == {"destination", "attraction"}


def test_score_input_is_human_authoritative():
    payload = {
        "origem_value": 100.0,
        "completude_value": 80.0,
        "corroboracao_value": 0.0,
        "atualidade_value": 100.0,
        "validacao_humana_value": 100.0,
    }
    si = MANUAL_DOMAIN.score_input(payload)
    assert si.origem_value == 100.0
    assert si.validacao_humana_value == 100.0
    assert si.completude_value == 80.0


# ---------------------------------------------------------------------------
# Repository payload shaping
# ---------------------------------------------------------------------------


def test_build_payload_pins_origem_and_validacao_humana():
    repo = ManualRepository()
    payload = repo.build_payload(
        entity_type="destination",
        uf="ba",
        name="Praia do Forte",
        municipio_id="2919207",
    )
    assert payload["origem_value"] == MANUAL_ORIGEM_VALUE == 100.0
    assert payload["validacao_humana_value"] == MANUAL_VALIDACAO_HUMANA_VALUE == 100.0
    assert payload["uf"] == "BA"
    assert payload["canonical"]["ibge_code"] == "2919207"
    assert payload["canonical"]["name"] == "Praia do Forte"
    assert payload["municipio_id"] == "2919207"


def test_make_source_ref_prefers_ibge_then_slug():
    repo = ManualRepository()
    assert (
        repo.make_source_ref("attraction", "ba", "2919207", "Praia do Forte")
        == "manual:attraction:BA:2919207"
    )
    assert (
        repo.make_source_ref("destination", "ba", None, "São João del-Rei")
        == "manual:destination:BA:sao-joao-del-rei"
    )


# ---------------------------------------------------------------------------
# Edit-lock gated mutation (Phase C)
# ---------------------------------------------------------------------------


def test_create_blocked_when_engine_ligado():
    """Fresh Redis → mode defaults to LIGADO → create is locked; no write happens."""
    redis = fakeredis.FakeRedis()
    service = ManualService()
    session = MagicMock()

    with (
        patch("brave.domains.manual.services.store_raw") as mock_store,
        patch("brave.domains.manual.services.process_nascente_record") as mock_rio,
        pytest.raises(EditingLockedError),
    ):
        service.create(
            session,
            redis,
            entity_type="destination",
            uf="BA",
            name="Praia do Forte",
        )

    mock_store.assert_not_called()
    mock_rio.assert_not_called()


def test_create_writes_manual_record_when_paused():
    """PAUSADO unlocks editing → store_raw is called with source='manual' + origem=100."""
    redis = fakeredis.FakeRedis()
    collection_engine.set_mode(redis, collection_engine.PAUSADO)
    service = ManualService()
    session = MagicMock()

    with (
        patch("brave.domains.manual.services.store_raw") as mock_store,
        patch("brave.domains.manual.services.process_nascente_record") as mock_rio,
    ):
        mock_store.return_value = MagicMock(name="nascente")
        result = service.create(
            session,
            redis,
            entity_type="attraction",
            uf="BA",
            name="Mirante Bonito",
            municipio_id="2919207",
        )

    assert mock_store.call_count == 1
    kwargs = mock_store.call_args.kwargs
    assert kwargs["source"] == "manual"
    assert kwargs["source_ref"] == "manual:attraction:BA:2919207"
    assert kwargs["entity_type"] == "attraction"
    assert kwargs["payload"]["origem_value"] == 100.0
    assert kwargs["payload"]["validacao_humana_value"] == 100.0
    # run_rio default True → Rio pipeline ran and its result is returned.
    mock_rio.assert_called_once()
    assert result is mock_rio.return_value


def test_create_nascente_only_skips_rio_when_run_rio_false():
    redis = fakeredis.FakeRedis()
    collection_engine.set_mode(redis, collection_engine.DESLIGADO)  # also unlocks
    service = ManualService()
    session = MagicMock()

    with (
        patch("brave.domains.manual.services.store_raw") as mock_store,
        patch("brave.domains.manual.services.process_nascente_record") as mock_rio,
    ):
        nascente = MagicMock(name="nascente")
        mock_store.return_value = nascente
        result = service.create(
            session,
            redis,
            entity_type="destination",
            uf="MG",
            name="Ouro Preto",
            run_rio=False,
        )

    mock_rio.assert_not_called()
    assert result is nascente


def test_update_is_also_edit_lock_gated():
    redis = fakeredis.FakeRedis()  # LIGADO
    service = ManualService()
    session = MagicMock()

    with (
        patch("brave.domains.manual.services.store_raw") as mock_store,
        patch("brave.domains.manual.services.process_nascente_record"),
        pytest.raises(EditingLockedError),
    ):
        service.update(
            session,
            redis,
            source_ref="manual:destination:BA:2919207",
            entity_type="destination",
            uf="BA",
            name="Praia do Forte (rev)",
        )
    mock_store.assert_not_called()


def test_domain_facade_delegates_to_service():
    """ManualDomain.create delegates to its ManualService."""
    fake_service = MagicMock(spec=ManualService)
    domain = ManualDomain(service=fake_service)
    domain.create("s", "r", entity_type="destination", uf="BA", name="X")
    fake_service.create.assert_called_once()
