"""Unit tests for SignalAgent — hard descarte + corroboração constant + sub_state advance.

All tests run 100% offline:
  - FakePlacesClient from tests/fakes/fake_places.py
  - Mock RioRecord objects (no real DB)

Test suite covers must_haves from 03-02-PLAN.md (post Phase E — Apify/IG source retired):
  - test_signal_agent_hard_descarte_closed_permanently
  - test_signal_agent_hard_descarte_closed_temporarily
  - test_signal_agent_advances_sub_state_for_open_place
  - test_signal_agent_writes_corroboracao_constant_zero

Corroboração note (Phase E): the Apify IG signal was removed. SignalAgent now writes a
deterministic corroboracao_value=0.0 (documented constant) — no Places field feeds it —
which matches the prior offline (Null) behaviour and keeps reliability routing stable.

D-18 boundary: no import from brave.lanes.destinos.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from tests.fakes.fake_places import SIGNAL_FIXTURE_CLOSED, SIGNAL_FIXTURE_OPEN, FakePlacesClient

# Pinned reference clock so the atualidade buckets AND the Phase F 90-day
# no-recent-reviews rule are fully deterministic offline (SIGNAL_FIXTURE_OPEN's
# review is 2026-06-01 → 14 days before this, i.e. recent AND well within 90d).
_NOW = datetime(2026, 6, 15, tzinfo=timezone.utc)

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

    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(
        places_client=fake_places,
        session=session,
        now=_NOW,
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

    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(
        places_client=fake_places,
        session=session,
        now=_NOW,
    )

    with patch("brave.lanes.atrativos.signal_agent.write_audit"):
        await agent.run(rio)

    assert rio.routing == "descarte"
    assert rio.sub_state is None
    assert rio.dlq_reason == "closed_place"


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

    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(
        places_client=fake_places,
        session=session,
        now=_NOW,
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
# Phase E: corroboração is a deterministic 0.0 constant + it feeds routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_agent_writes_corroboracao_constant_zero() -> None:
    """SignalAgent writes corroboracao_value=0.0 deterministically, then routes.

    Post Phase E the Apify/IG corroboration source is retired: no Places field feeds
    corroboração, so the lane must write the documented 0.0 constant regardless of any
    stale prior value. It then hands off to route_by_score (the reliability routing path).
    """
    from brave.lanes.atrativos.signal_agent import SignalAgent

    open_fixture = {**SIGNAL_FIXTURE_OPEN, "place_id": "ChIJtest001"}

    fake_places = FakePlacesClient(
        fixture_details={"ChIJtest001": open_fixture},
    )

    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")
    # Seed a stale non-zero value to prove SignalAgent overwrites it to the constant.
    rio.normalized["corroboracao_value"] = 99.0

    agent = SignalAgent(
        places_client=fake_places,
        session=session,
        now=_NOW,
    )

    with patch("brave.lanes.atrativos.signal_agent.write_audit"), \
         patch("brave.lanes.atrativos.signal_agent.route_by_score") as mock_route:
        await agent.run(rio)

    # Deterministic constant: corroboracao_value is exactly 0.0 (Apify retired).
    assert rio.normalized["corroboracao_value"] == 0.0
    # Routing path still runs after signals are gathered.
    assert rio.sub_state == "signals_gathered"
    assert mock_route.called, "route_by_score must be invoked (the reliability routing path)"


# ---------------------------------------------------------------------------
# Phase F: no-recent-reviews rule (no reviews OR newest > 90 days → terminal DLQ)
# ---------------------------------------------------------------------------


def _open_fixture(reviews: list[dict]) -> dict:
    """OPERATIONAL place_details fixture with the given reviews list."""
    return {
        "place_id": "ChIJtest001",
        "business_status": "OPERATIONAL",
        "weekday_text": ["Monday: 9:00 AM – 5:00 PM"],
        "reviews": reviews,
    }


@pytest.mark.asyncio
async def test_signal_agent_with_recent_reviews_is_scored() -> None:
    """Phase F: a review within 90 days is NOT stale → proceeds to reliability scoring.

    The no-recent-reviews rule must NOT fire; the record advances to
    signals_gathered, route_by_score runs, and most_recent_review_at is persisted.
    """
    from brave.lanes.atrativos.signal_agent import SignalAgent

    # Review 20 days before the pinned clock → recent (≤ 90 days).
    recent_dt = (_NOW - timedelta(days=20)).replace(microsecond=0)
    fixture = _open_fixture([{"publishTime": recent_dt.isoformat(), "rating": 5, "text": "ok"}])

    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(places_client=fake_places, session=session, now=_NOW)

    with patch("brave.lanes.atrativos.signal_agent.write_audit"), \
         patch("brave.lanes.atrativos.signal_agent.route_by_score") as mock_route:
        await agent.run(rio)

    assert rio.sub_state == "signals_gathered"
    assert rio.dlq_reason != "no_recent_reviews"
    assert mock_route.called, "route_by_score must run for a non-stale attraction"
    # most_recent_review_at is persisted for the promote_to_mar recency backstop.
    assert rio.normalized["most_recent_review_at"] == recent_dt.isoformat()


@pytest.mark.asyncio
async def test_signal_agent_no_reviews_routes_to_terminal_dlq() -> None:
    """Phase F: NO reviews → terminal DLQ (dlq_reason='no_recent_reviews'), NOT the gate.

    route_by_score must NOT run (the rule short-circuits before scoring), and the
    record must land at sub_state=None — never sub_state='aguardando_consulta_whatsapp'.
    """
    from brave.lanes.atrativos.signal_agent import SignalAgent

    fixture = _open_fixture([])  # zero reviews
    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(places_client=fake_places, session=session, now=_NOW)

    with patch("brave.lanes.atrativos.signal_agent.write_audit"), \
         patch("brave.lanes.atrativos.signal_agent.route_by_score") as mock_route:
        await agent.run(rio)

    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_recent_reviews"
    assert rio.sub_state is None
    assert rio.sub_state != "aguardando_consulta_whatsapp"
    assert not mock_route.called, "route_by_score must be skipped for a no-review attraction"


@pytest.mark.asyncio
async def test_signal_agent_stale_reviews_over_90d_routes_to_terminal_dlq() -> None:
    """Phase F: newest review older than 90 days → terminal DLQ, NOT the gate."""
    from brave.lanes.atrativos.signal_agent import SignalAgent

    # Newest review 120 days before the pinned clock → stale (> 90 days).
    stale_dt = (_NOW - timedelta(days=120)).replace(microsecond=0)
    fixture = _open_fixture([{"publishTime": stale_dt.isoformat(), "rating": 4, "text": "old"}])

    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(places_client=fake_places, session=session, now=_NOW)

    with patch("brave.lanes.atrativos.signal_agent.write_audit"), \
         patch("brave.lanes.atrativos.signal_agent.route_by_score") as mock_route:
        await agent.run(rio)

    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_recent_reviews"
    assert rio.sub_state is None
    assert not mock_route.called, "route_by_score must be skipped for a stale attraction"


@pytest.mark.asyncio
async def test_signal_agent_no_recent_reviews_never_reaches_whatsapp_gate() -> None:
    """Phase F guard: a review-less attraction must NEVER enter the WhatsApp gate.

    Regression pin for the 'manual now' requirement: the terminal-DLQ short-circuit
    replaces the old auto-enrollment into sub_state='aguardando_consulta_whatsapp'.
    """
    from brave.lanes.atrativos.signal_agent import SignalAgent

    fixture = _open_fixture([])
    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(places_client=fake_places, session=session, now=_NOW)

    with patch("brave.lanes.atrativos.signal_agent.write_audit"), \
         patch("brave.lanes.atrativos.signal_agent.route_by_score"):
        await agent.run(rio)

    assert rio.sub_state is None
    assert rio.sub_state != "aguardando_consulta_whatsapp"
