"""Smoke tests for the ``manual`` SourceDomain contract + CRUD (Phase G STEP 3).

Co-located with the domain (``brave/domains/manual/tests/``). Imports ONLY this domain
(+ registry helper + base + the kernel engine for the edit-lock mode) so it never trips
the cross-domain import guard (CHECK B). Fully offline: fakeredis drives the engine
mode / edit-lock and ``store_raw`` / ``process_nascente_record`` are patched at
``brave.domains.manual.services.*`` so no DB is touched.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from brave.core import engine as collection_engine
from brave.domains import get_domain
from brave.domains.base import SourceDomain
from brave.domains.manual.controllers import MANUAL_DOMAIN, ManualDomain
from brave.domains.manual.exceptions import EditingLockedError

# --- contract / registry conformance ---------------------------------------


def test_manual_domain_is_registered_and_conformant():
    domain = get_domain("manual")
    assert domain is MANUAL_DOMAIN
    assert isinstance(domain, ManualDomain)
    assert isinstance(domain, SourceDomain)  # structural: has the full contract
    assert domain.name == "manual"
    assert set(domain.produces) == {"destination", "attraction"}


def test_manual_is_a_non_sweep_source():
    """No producers and no beat rows — manual records are authored on demand."""
    assert MANUAL_DOMAIN.sweep_plan("BA", depth="nascente", lane="both", nascente_only=False) == []
    assert MANUAL_DOMAIN.beat_entries(["BA", "SP"]) == {}
    # discover is a no-op coroutine (returns None), never fans out per UF.
    assert asyncio.run(MANUAL_DOMAIN.discover("BA")) is None


def test_score_input_is_human_authoritative():
    si = MANUAL_DOMAIN.score_input(
        {"origem_value": 100.0, "validacao_humana_value": 100.0, "completude_value": 90.0}
    )
    assert si.origem_value == 100.0
    assert si.validacao_humana_value == 100.0


# --- CRUD facade: edit-lock gated mutation (Phase C parity) ------------------


def test_create_via_facade_blocked_when_engine_ligado():
    """Fresh Redis → mode LIGADO → the edit-lock refuses the write."""
    redis = fakeredis.FakeRedis()  # unset mode → LIGADO default (locked)
    session = MagicMock()

    with (
        patch("brave.domains.manual.services.store_raw") as mock_store,
        patch("brave.domains.manual.services.process_nascente_record"),
        pytest.raises(EditingLockedError),
    ):
        MANUAL_DOMAIN.create(
            session, redis, entity_type="destination", uf="BA", name="Praia do Forte"
        )
    mock_store.assert_not_called()


def test_create_via_facade_writes_manual_record_when_paused():
    """PAUSADO unlocks editing → store_raw is called with source='manual'."""
    redis = fakeredis.FakeRedis()
    collection_engine.set_mode(redis, collection_engine.PAUSADO)
    session = MagicMock()

    with (
        patch("brave.domains.manual.services.store_raw") as mock_store,
        patch("brave.domains.manual.services.process_nascente_record") as mock_rio,
    ):
        mock_store.return_value = MagicMock(name="nascente")
        result = MANUAL_DOMAIN.create(
            session,
            redis,
            entity_type="attraction",
            uf="BA",
            name="Mirante Bonito",
            municipio_id="2919207",
        )

    assert mock_store.call_count == 1
    assert mock_store.call_args.kwargs["source"] == "manual"
    assert mock_store.call_args.kwargs["payload"]["origem_value"] == 100.0
    # run_rio default True → the Rio result is what the facade returns.
    assert result is mock_rio.return_value


def test_get_via_facade_delegates_to_service_repository():
    """Reads are ungated — ManualDomain.get returns the repo's active row."""
    fake_repo = MagicMock()
    fake_repo.get_active.return_value = MagicMock(name="nascente")
    from brave.domains.manual.services import ManualService

    domain = ManualDomain(service=ManualService(repository=fake_repo))
    got = domain.get(MagicMock(), "manual:destination:BA:2919207")

    fake_repo.get_active.assert_called_once()
    assert got is fake_repo.get_active.return_value
