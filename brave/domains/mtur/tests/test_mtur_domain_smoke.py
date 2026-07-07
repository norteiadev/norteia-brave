"""Smoke tests for the ``mtur`` (default) SourceDomain contract (Phase G STEP 3).

Co-located with the domain (``brave/domains/mtur/tests/``). Imports ONLY this domain
(+ the registry helper + base) so it never trips the cross-domain import guard
(tests/unit/test_domain_boundaries.py CHECK B). Fully offline: no Redis/DB/broker —
``sweep_plan``/``beat_entries``/``score_input`` are pure, and ``discover`` is only
asserted to fail-fast when its injected deps are missing (never actually run).
"""

from __future__ import annotations

import asyncio

import pytest

from brave.domains import get_domain
from brave.domains.base import SourceDomain, SweepDispatch
from brave.domains.mtur.controllers import MTUR_DOMAIN, MturDomain

# --- contract / registry conformance ---------------------------------------


def test_mtur_domain_is_registered_and_conformant():
    domain = get_domain("mtur")
    assert domain is MTUR_DOMAIN
    assert isinstance(domain, MturDomain)
    assert isinstance(domain, SourceDomain)  # structural: has the full contract
    assert domain.name == "mtur"
    assert set(domain.produces) == {"destination", "attraction"}


def test_default_is_an_alias_of_mtur():
    assert get_domain("default") is MTUR_DOMAIN
    assert "default" in MTUR_DOMAIN.aliases


def test_score_input_defaults_missing_criteria_to_zero():
    si = MTUR_DOMAIN.score_input({"origem_value": 70.0})
    assert si.origem_value == 70.0
    assert si.completude_value == 0.0


# --- sweep_plan (lane -> producer routing + depth gate) ---------------------


def test_sweep_plan_nascente_only_is_mtur_seed_regardless_of_lane():
    for lane in ("destinos", "atrativos", "both"):
        plan = MTUR_DOMAIN.sweep_plan(
            "BA", depth="nascente", lane=lane, nascente_only=True
        )
        assert plan == [SweepDispatch("brave.sweep_uf", ("BA",), {"depth": "nascente"})]


def test_sweep_plan_both_fans_out_sweep_uf_and_discover_atrativo():
    plan = MTUR_DOMAIN.sweep_plan(
        "BA", depth="nascente_rio_mar", lane="both", nascente_only=False
    )
    assert [d.task_name for d in plan] == ["brave.sweep_uf", "brave.discover_atrativo"]
    assert all(d.args == ("BA",) for d in plan)
    assert all(d.kwargs == {"depth": "nascente_rio_mar"} for d in plan)


def test_sweep_plan_honors_single_lane_selection():
    destinos = MTUR_DOMAIN.sweep_plan(
        "MG", depth="nascente_rio", lane="destinos", nascente_only=False
    )
    atrativos = MTUR_DOMAIN.sweep_plan(
        "MG", depth="nascente_rio", lane="atrativos", nascente_only=False
    )
    assert [d.task_name for d in destinos] == ["brave.sweep_uf"]
    assert [d.task_name for d in atrativos] == ["brave.discover_atrativo"]


# --- beat_entries (per-UF schedule) -----------------------------------------


def test_beat_entries_two_rows_per_uf_no_pinned_queue():
    ufs = ["AC", "BA", "SP"]
    entries = MTUR_DOMAIN.beat_entries(ufs)
    assert len(entries) == 2 * len(ufs)
    assert "sweep-ac-daily" in entries
    assert "sweep-atrativos-ac-daily" in entries
    assert entries["sweep-ac-daily"]["task"] == "brave.sweep_uf"
    assert entries["sweep-atrativos-ac-daily"]["task"] == "brave.discover_atrativo"
    for entry in entries.values():
        assert entry.get("options", {}).get("queue") in (None, "celery")


# --- discover fail-fast when task-layer deps are absent ---------------------


def test_discover_requires_injected_session_and_config():
    with pytest.raises(ValueError, match="session="):
        asyncio.run(MTUR_DOMAIN.discover("BA"))
