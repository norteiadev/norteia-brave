"""Unit tests for SignalAgent — hard descarte + Apify degradation + sub_state advance.

All tests run 100% offline:
  - FakePlacesClient from tests/fakes/fake_places.py
  - FakeApifyClient from tests/fakes/fake_apify.py
  - Mock RioRecord objects (no real DB)

Test suite covers must_haves from 03-02-PLAN.md:
  - test_signal_agent_hard_descarte_closed_permanently
  - test_signal_agent_hard_descarte_closed_temporarily
  - test_signal_agent_apify_failure_degrades_gracefully
  - test_signal_agent_advances_sub_state_for_open_place

D-18 boundary: no import from brave.lanes.destinos.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.fakes.fake_places import FakePlacesClient, SIGNAL_FIXTURE_OPEN, SIGNAL_FIXTURE_CLOSED
from tests.fakes.fake_apify import FakeApifyClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rio(
    sub_state: str = "contacts_found",
    routing: str = "in_progress",
) -> MagicMock:
    """Build a minimal RioRecord mock for SignalAgent tests."""
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.sub_state = sub_state
    rio.routing = routing
    rio.dlq_reason = None
    rio.entity_type = "attraction"
    rio.uf = "BA"
    rio.normalized = {
        "place_id_cache": "ChIJtest001",
        "origin_value": 60.0,
        "completude_value": 75.0,
        "corroboracao_value": 0.0,
        "atualidade_value": 0.0,
        "validacao_humana_value": 0.0,
        "contacts": {"ig_handle": "@praiatest"},
    }
    return rio


def _make_mock_session() -> MagicMock:
    session = MagicMock()
    session.flush.return_value = None
    session.add.return_value = None
    return session


# ---------------------------------------------------------------------------
# Tests: Hard Descarte (CLOSED_PERMANENTLY / CLOSED_TEMPORARILY)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_agent_hard_descarte_closed_permanently() -> None:
    """CLOSED_PERMANENTLY triggers hard descarte before scoring.

    After SignalAgent.run(rio):
      - rio.routing must be "descarte"
      - rio.sub_state must be None
      - rio.dlq_reason must be "closed_place"
    """
    from brave.lanes.atrativos.signal_agent import SignalAgent

    closed_fixture = {
        **SIGNAL_FIXTURE_CLOSED,
        "place_id": "ChIJtest001",
        "business_status": "CLOSED_PERMANENTLY",
    }

    fake_places = FakePlacesClient(
        fixture_details={"ChIJtest001": closed_fixture},
    )
    fake_apify = FakeApifyClient()

    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(
        places_client=fake_places,
        apify_client=fake_apify,
        session=session,
    )

    with patch("brave.lanes.atrativos.signal_agent.write_audit"):
        await agent.run(rio)

    assert rio.routing == "descarte"
    assert rio.sub_state is None
    assert rio.dlq_reason == "closed_place"


@pytest.mark.asyncio
async def test_signal_agent_hard_descarte_closed_temporarily() -> None:
    """CLOSED_TEMPORARILY triggers hard descarte before scoring.

    Same assertions as CLOSED_PERMANENTLY.
    """
    from brave.lanes.atrativos.signal_agent import SignalAgent

    closed_tmp_fixture = {
        "place_id": "ChIJtest001",
        "business_status": "CLOSED_TEMPORARILY",
        "weekday_text": [],
        "reviews": [],
    }

    fake_places = FakePlacesClient(
        fixture_details={"ChIJtest001": closed_tmp_fixture},
    )
    fake_apify = FakeApifyClient()

    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(
        places_client=fake_places,
        apify_client=fake_apify,
        session=session,
    )

    with patch("brave.lanes.atrativos.signal_agent.write_audit"):
        await agent.run(rio)

    assert rio.routing == "descarte"
    assert rio.sub_state is None
    assert rio.dlq_reason == "closed_place"


# ---------------------------------------------------------------------------
# Tests: Apify degradation (best-effort, non-blocking)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_agent_apify_failure_degrades_gracefully() -> None:
    """Apify failure does NOT raise; SignalAgent advances to signals_gathered.

    FakeApifyClient(raise_on_call=RuntimeError("timeout")) — scrape_ig raises.
    After SignalAgent.run(rio):
      - No exception propagated
      - rio.sub_state must be "signals_gathered"
    """
    from brave.lanes.atrativos.signal_agent import SignalAgent

    open_fixture = {**SIGNAL_FIXTURE_OPEN, "place_id": "ChIJtest001"}

    fake_places = FakePlacesClient(
        fixture_details={"ChIJtest001": open_fixture},
    )
    # Apify raises on every call
    fake_apify = FakeApifyClient(raise_on_call=RuntimeError("timeout"))

    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(
        places_client=fake_places,
        apify_client=fake_apify,
        session=session,
    )

    with patch("brave.lanes.atrativos.signal_agent.write_audit"), \
         patch("brave.lanes.atrativos.signal_agent.route_by_score"):
        # Must NOT raise — Apify failure degrades signal, never fails record
        await agent.run(rio)

    # sub_state must have advanced to signals_gathered
    assert rio.sub_state == "signals_gathered"


# ---------------------------------------------------------------------------
# Tests: Open place happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_agent_advances_sub_state_for_open_place() -> None:
    """OPERATIONAL place: sub_state advances to signals_gathered + atualidade set.

    SIGNAL_FIXTURE_OPEN has a review from 2026-06-01 (≤30 days before 2026-06-15).
    atualidade_value in normalized should be 100 after run.
    """
    from brave.lanes.atrativos.signal_agent import SignalAgent

    open_fixture = {**SIGNAL_FIXTURE_OPEN, "place_id": "ChIJtest001"}

    fake_places = FakePlacesClient(
        fixture_details={"ChIJtest001": open_fixture},
    )
    fake_apify = FakeApifyClient(fixture_data={"@praiatest": {"followers": 1200}})

    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(
        places_client=fake_places,
        apify_client=fake_apify,
        session=session,
    )

    with patch("brave.lanes.atrativos.signal_agent.write_audit"), \
         patch("brave.lanes.atrativos.signal_agent.route_by_score"):
        await agent.run(rio)

    # sub_state must be "signals_gathered"
    assert rio.sub_state == "signals_gathered"

    # atualidade_value should be set (>0) because review is recent
    assert rio.normalized is not None
    # The agent sets atualidade_value in the normalized dict
    atualidade = rio.normalized.get("atualidade_value", 0)
    assert atualidade > 0, f"Expected atualidade_value > 0 for recent review, got {atualidade}"


# ---------------------------------------------------------------------------
# WR-08: _compute_corroboracao — no catch-all, correct posts_count key
# ---------------------------------------------------------------------------


def test_corroboracao_empty_dict_is_zero() -> None:
    from brave.lanes.atrativos.signal_agent import _compute_corroboracao

    assert _compute_corroboracao({}) == 0.0


def test_corroboracao_inactive_profile_is_zero() -> None:
    """WR-08: a found-but-inactive / error-shaped dict (0 followers, no posts)
    must score 0.0 — not 40.0 via the old `or len(ig_data) > 0` catch-all."""
    from brave.lanes.atrativos.signal_agent import _compute_corroboracao

    assert _compute_corroboracao({"handle": "@x", "followers": 0, "posts_count": 0}) == 0.0
    assert _compute_corroboracao({"error": "not_found"}) == 0.0


def test_corroboracao_active_followers_is_forty() -> None:
    from brave.lanes.atrativos.signal_agent import _compute_corroboracao

    assert _compute_corroboracao({"followers": 1200}) == 40.0


def test_corroboracao_uses_posts_count_key_not_post_count() -> None:
    """WR-08: the apify client writes 'posts_count' — the old code read 'post_count'."""
    from brave.lanes.atrativos.signal_agent import _compute_corroboracao

    # Correct key drives the signal.
    assert _compute_corroboracao({"followers": 0, "posts_count": 12}) == 40.0
    # Old (wrong) key must NOT drive the signal.
    assert _compute_corroboracao({"followers": 0, "post_count": 12}) == 0.0


def test_corroboracao_recent_last_post_is_forty() -> None:
    from brave.lanes.atrativos.signal_agent import _compute_corroboracao

    assert _compute_corroboracao({"followers": 0, "last_post": "2026-06-01"}) == 40.0
