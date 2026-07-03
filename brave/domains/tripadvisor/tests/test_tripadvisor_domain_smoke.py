"""Smoke tests for the ``tripadvisor`` SourceDomain contract (Phase G STEP 3).

Co-located with the domain (``brave/domains/tripadvisor/tests/``). Imports ONLY this
domain (+ registry helper + base) so it never trips the cross-domain import guard
(CHECK B). Fully offline: ``sweep_plan``/``beat_entries``/``score_input`` are pure
(``beat_entries`` reads env-only ``TripAdvisorConfig``, no DB); ``discover`` is only
asserted to fail-fast when its injected deps are missing.
"""

from __future__ import annotations

import asyncio

import pytest

from brave.domains import get_domain
from brave.domains.base import SourceDomain, SweepDispatch
from brave.domains.tripadvisor.controllers import TRIPADVISOR_DOMAIN, TripAdvisorDomain

# --- contract / registry conformance ---------------------------------------


def test_tripadvisor_domain_is_registered_and_conformant():
    domain = get_domain("tripadvisor")
    assert domain is TRIPADVISOR_DOMAIN
    assert isinstance(domain, TripAdvisorDomain)
    assert isinstance(domain, SourceDomain)  # structural: has the full contract
    assert domain.name == "tripadvisor"
    assert set(domain.produces) == {"destination", "attraction"}


def test_score_input_maps_the_five_criteria():
    si = TRIPADVISOR_DOMAIN.score_input(
        {"origem_value": 60.0, "corroboracao_value": 40.0}
    )
    assert si.origem_value == 60.0
    assert si.corroboracao_value == 40.0
    assert si.atualidade_value == 0.0


# --- sweep_plan (atrativos-only; lane/depth do not branch the plan) ---------


def test_sweep_plan_is_single_atrativos_producer_regardless_of_lane():
    for lane in ("destinos", "atrativos", "both"):
        for nascente_only in (True, False):
            plan = TRIPADVISOR_DOMAIN.sweep_plan(
                "RJ", depth="nascente_rio", lane=lane, nascente_only=nascente_only
            )
            assert plan == [
                SweepDispatch("brave.sweep_tripadvisor", ("RJ",), {"depth": "nascente_rio"})
            ]


# --- beat_entries (session keep-alive only) ---------------------------------


def test_beat_entries_only_ta_keepalive_no_uf_sweeps():
    entries = TRIPADVISOR_DOMAIN.beat_entries(["AC", "BA"])
    assert set(entries) == {"ta-keepalive"}
    assert entries["ta-keepalive"]["task"] == "brave.ta_keepalive"
    assert entries["ta-keepalive"].get("options", {}).get("queue") in (None, "celery")
    assert not [k for k in entries if k.startswith("sweep-")]


# --- discover fail-fast when task-layer deps are absent ---------------------


def test_discover_requires_injected_client_session_config_ibge():
    with pytest.raises(ValueError, match="ta_client="):
        asyncio.run(TRIPADVISOR_DOMAIN.discover("RJ"))
