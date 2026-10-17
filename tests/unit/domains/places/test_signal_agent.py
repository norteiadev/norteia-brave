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

D-18 boundary: no import from other domains.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from brave.config.settings import ScoreConfig
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
# Tests: Hard Descarte (CLOSED_PERMANENTLY) / DLQ (CLOSED_TEMPORARILY)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_agent_hard_descarte_closed_permanently() -> None:
    """CLOSED_PERMANENTLY triggers hard descarte before scoring.

    After SignalAgent.run(rio):
      - rio.routing must be "descarte"
      - rio.sub_state must be None
      - rio.dlq_reason must be "closed_place"
    """
    from brave.domains.places.signal_agent import SignalAgent

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
        config=ScoreConfig(),
    )

    with patch("brave.domains.places.signal_agent.write_audit"):
        await agent.run(rio)

    assert rio.routing == "descarte"
    assert rio.sub_state is None
    assert rio.dlq_reason == "closed_place"


@pytest.mark.asyncio
async def test_signal_agent_closed_temporarily_parks_in_dlq() -> None:
    """CLOSED_TEMPORARILY is a steward's call, not a descarte (2026-09-18): DLQ with
    dlq_reason "closed_temporarily" — the Painel badges it "Fechado Temporariamente"."""
    from brave.domains.places.signal_agent import SignalAgent

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
        config=ScoreConfig(),
    )

    with patch("brave.domains.places.signal_agent.write_audit"):
        await agent.run(rio)

    assert rio.routing == "dlq"
    assert rio.sub_state is None
    assert rio.dlq_reason == "closed_temporarily"


@pytest.mark.asyncio
async def test_signal_agent_reopened_place_leaves_closed_temporarily() -> None:
    """The exit side of C5: a fresh OPERATIONAL business_status re-decides the verdict
    (redecided), else the reopened attraction stays dlq/closed_temporarily forever."""
    from brave.domains.places.signal_agent import SignalAgent

    fake_places = FakePlacesClient(
        fixture_details={"ChIJtest001": {**SIGNAL_FIXTURE_OPEN, "place_id": "ChIJtest001",
                                         "business_status": "OPERATIONAL"}},
    )
    rio = _make_rio(routing="dlq")
    rio.dlq_reason = "closed_temporarily"
    rio.normalized.update(origem_value=100.0, completude_value=100.0, validacao_humana_value=100.0)

    agent = SignalAgent(
        places_client=fake_places, session=_make_mock_session(), now=_NOW, config=ScoreConfig()
    )
    with patch("brave.domains.places.signal_agent.write_audit"):
        await agent.run(rio)

    assert rio.dlq_reason != "closed_temporarily"


# ---------------------------------------------------------------------------
# Tests: Open place happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_agent_advances_sub_state_for_open_place() -> None:
    """OPERATIONAL place: sub_state advances to signals_gathered + atualidade set.

    SIGNAL_FIXTURE_OPEN has a review from 2026-06-01 (≤30 days before 2026-06-15).
    atualidade_value in normalized should be 100 after run.
    """
    from brave.domains.places.signal_agent import SignalAgent

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
        config=ScoreConfig(),
    )

    with patch("brave.domains.places.signal_agent.write_audit"), \
         patch("brave.domains.places.signal_agent.route_by_score"):
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
    from brave.domains.places.signal_agent import SignalAgent

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
        config=ScoreConfig(),
    )

    with patch("brave.domains.places.signal_agent.write_audit"), \
         patch("brave.domains.places.signal_agent.route_by_score") as mock_route:
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
    from brave.domains.places.signal_agent import SignalAgent

    # Review 20 days before the pinned clock → recent (≤ 90 days).
    recent_dt = (_NOW - timedelta(days=20)).replace(microsecond=0)
    fixture = _open_fixture([{"publishTime": recent_dt.isoformat(), "rating": 5, "text": "ok"}])

    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(places_client=fake_places, session=session, now=_NOW, config=ScoreConfig())

    with patch("brave.domains.places.signal_agent.write_audit"), \
         patch("brave.domains.places.signal_agent.route_by_score") as mock_route:
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
    from brave.domains.places.signal_agent import SignalAgent

    fixture = _open_fixture([])  # zero reviews
    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(places_client=fake_places, session=session, now=_NOW, config=ScoreConfig())

    with patch("brave.domains.places.signal_agent.write_audit"), \
         patch("brave.domains.places.signal_agent.route_by_score") as mock_route:
        await agent.run(rio)

    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_recent_reviews"
    assert rio.sub_state is None
    assert rio.sub_state != "aguardando_consulta_whatsapp"
    assert not mock_route.called, "route_by_score must be skipped for a no-review attraction"


@pytest.mark.asyncio
async def test_signal_agent_stale_reviews_over_90d_routes_to_terminal_dlq() -> None:
    """Phase F: newest review older than 90 days → terminal DLQ, NOT the gate."""
    from brave.domains.places.signal_agent import SignalAgent

    # Newest review 120 days before the pinned clock → stale (> 90 days).
    stale_dt = (_NOW - timedelta(days=120)).replace(microsecond=0)
    fixture = _open_fixture([{"publishTime": stale_dt.isoformat(), "rating": 4, "text": "old"}])

    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(places_client=fake_places, session=session, now=_NOW, config=ScoreConfig())

    with patch("brave.domains.places.signal_agent.write_audit"), \
         patch("brave.domains.places.signal_agent.route_by_score") as mock_route:
        await agent.run(rio)

    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_recent_reviews"
    assert rio.sub_state is None
    assert not mock_route.called, "route_by_score must be skipped for a stale attraction"


@pytest.mark.asyncio
async def test_signal_agent_persists_formatted_address() -> None:
    """Places formatted_address → normalized['address'] (the flat push field)."""
    from brave.domains.places.signal_agent import SignalAgent

    recent_dt = (_NOW - timedelta(days=20)).replace(microsecond=0)
    fixture = {
        **_open_fixture([{"publishTime": recent_dt.isoformat(), "rating": 5, "text": "ok"}]),
        "formatted_address": "Praça Central, Porto Seguro - BA",
    }

    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(places_client=fake_places, session=session, now=_NOW, config=ScoreConfig())

    with patch("brave.domains.places.signal_agent.write_audit"), \
         patch("brave.domains.places.signal_agent.route_by_score"):
        await agent.run(rio)

    assert rio.normalized["address"] == "Praça Central, Porto Seguro - BA"


@pytest.mark.asyncio
async def test_signal_agent_missing_address_keeps_nascente_value() -> None:
    """Places without formatted_address must never wipe an address already carried in."""
    from brave.domains.places.signal_agent import SignalAgent

    recent_dt = (_NOW - timedelta(days=20)).replace(microsecond=0)
    fixture = _open_fixture([{"publishTime": recent_dt.isoformat(), "rating": 5, "text": "ok"}])

    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")
    rio.normalized["address"] = "Rua do Nascente, 100"

    agent = SignalAgent(places_client=fake_places, session=session, now=_NOW, config=ScoreConfig())

    with patch("brave.domains.places.signal_agent.write_audit"), \
         patch("brave.domains.places.signal_agent.route_by_score"):
        await agent.run(rio)

    assert rio.normalized["address"] == "Rua do Nascente, 100"


@pytest.mark.asyncio
async def test_signal_agent_no_recent_reviews_never_reaches_whatsapp_gate() -> None:
    """Phase F guard: a review-less attraction must NEVER enter the WhatsApp gate.

    Regression pin for the 'manual now' requirement: the terminal-DLQ short-circuit
    replaces the old auto-enrollment into sub_state='aguardando_consulta_whatsapp'.
    """
    from brave.domains.places.signal_agent import SignalAgent

    fixture = _open_fixture([])
    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    session = _make_mock_session()
    rio = _make_rio(sub_state="contacts_found")

    agent = SignalAgent(places_client=fake_places, session=session, now=_NOW, config=ScoreConfig())

    with patch("brave.domains.places.signal_agent.write_audit"), \
         patch("brave.domains.places.signal_agent.route_by_score"):
        await agent.run(rio)

    assert rio.sub_state is None
    assert rio.sub_state != "aguardando_consulta_whatsapp"


# ---------------------------------------------------------------------------
# One rule with the Mar backstop (_attraction_review_recent) over the merged
# TA + Google recency view; the merged date/count is persisted on both paths.
# ---------------------------------------------------------------------------


async def _run_signal(rio: MagicMock, fixture: dict) -> MagicMock:
    """Run SignalAgent on rio with fixture; returns the route_by_score mock."""
    from brave.domains.places.signal_agent import SignalAgent

    fake_places = FakePlacesClient(fixture_details={"ChIJtest001": fixture})
    agent = SignalAgent(
        places_client=fake_places, session=_make_mock_session(), now=_NOW, config=ScoreConfig()
    )
    with patch("brave.domains.places.signal_agent.write_audit"), \
         patch("brave.domains.places.signal_agent.route_by_score") as mock_route:
        await agent.run(rio)
    return mock_route


@pytest.mark.asyncio
async def test_signal_agent_fresh_ta_date_passes_without_google_reviews() -> None:
    """A fresh TripAdvisor date already in normalized passes with zero Google reviews."""
    ta_dt = (_NOW - timedelta(days=10)).replace(microsecond=0)
    rio = _make_rio()
    rio.normalized["most_recent_review_at"] = ta_dt.isoformat()

    mock_route = await _run_signal(rio, _open_fixture([]))

    assert rio.sub_state == "signals_gathered"
    assert rio.dlq_reason != "no_recent_reviews"
    assert mock_route.called
    assert rio.normalized["most_recent_review_at"] == ta_dt.isoformat()


@pytest.mark.asyncio
async def test_signal_agent_established_place_gets_365d_window() -> None:
    """Newest Google review 200d old + TA review_count 30 → established, passes."""
    old_dt = (_NOW - timedelta(days=200)).replace(microsecond=0)
    fixture = _open_fixture([{"publishTime": old_dt.isoformat(), "rating": 5, "text": "ok"}])
    rio = _make_rio()
    rio.normalized["review_count"] = 30

    mock_route = await _run_signal(rio, fixture)

    assert rio.sub_state == "signals_gathered"
    assert mock_route.called
    assert rio.normalized["most_recent_review_at"] == old_dt.isoformat()
    assert rio.normalized["review_count"] == 30


@pytest.mark.asyncio
async def test_signal_agent_stale_low_volume_dlq_persists_recency() -> None:
    """200d + no TA review_count → DLQ no_recent_reviews, merged date still written; no
    review_count is invented (the established window stays TA-calibrated)."""
    old_dt = (_NOW - timedelta(days=200)).replace(microsecond=0)
    fixture = _open_fixture([{"publishTime": old_dt.isoformat(), "rating": 4, "text": "old"}])
    rio = _make_rio()

    mock_route = await _run_signal(rio, fixture)

    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_recent_reviews"
    assert rio.sub_state is None
    assert not mock_route.called
    assert rio.normalized["most_recent_review_at"] == old_dt.isoformat()
    assert "review_count" not in rio.normalized


@pytest.mark.asyncio
async def test_signal_agent_older_google_date_never_regresses_existing() -> None:
    """An existing newer date (TA) is kept over an older Google review; atualidade too."""
    ta_dt = (_NOW - timedelta(days=5)).replace(microsecond=0)
    google_dt = (_NOW - timedelta(days=60)).replace(microsecond=0)
    rio = _make_rio()
    rio.normalized["most_recent_review_at"] = ta_dt.isoformat()
    rio.normalized["review_count"] = 42
    rio.normalized["atualidade_value"] = 100.0
    fixture = _open_fixture([{"publishTime": google_dt.isoformat(), "rating": 5, "text": "ok"}])

    await _run_signal(rio, fixture)

    assert rio.sub_state == "signals_gathered"
    assert rio.normalized["most_recent_review_at"] == ta_dt.isoformat()
    assert rio.normalized["review_count"] == 42  # TA's count untouched
    assert rio.normalized["atualidade_value"] == 100.0  # Google alone would give 50


@pytest.mark.asyncio
async def test_signal_agent_no_recency_data_at_all_routes_to_dlq() -> None:
    """No date anywhere (and no count) → DLQ; nothing None is written over normalized."""
    rio = _make_rio()

    await _run_signal(rio, _open_fixture([]))

    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_recent_reviews"
    assert "most_recent_review_at" not in rio.normalized
    assert "review_count" not in rio.normalized


# ---------------------------------------------------------------------------
# Every non-descarte exit persists the signals delta from the (already paid)
# place_details call — not only the scored path. Antigravity finding #3.
# ---------------------------------------------------------------------------

_HOURS = ["Segunda-feira: 08:00–17:00"]
_ADDRESS = "Praça Central, Porto Seguro - BA"


async def _run_signal_with_session(rio: MagicMock, fixture: dict) -> tuple[MagicMock, MagicMock]:
    """Like _run_signal, but also returns the session (to assert the locked refresh)."""
    from brave.domains.places.signal_agent import SignalAgent

    session = _make_mock_session()
    agent = SignalAgent(
        places_client=FakePlacesClient(fixture_details={"ChIJtest001": fixture}),
        session=session,
        now=_NOW,
        config=ScoreConfig(),
    )
    with patch("brave.domains.places.signal_agent.write_audit"), \
         patch("brave.domains.places.signal_agent.route_by_score") as mock_route:
        await agent.run(rio)
    return mock_route, session


@pytest.mark.asyncio
async def test_signal_agent_no_recent_reviews_persists_signals() -> None:
    """Stale (120d, no TA count) → DLQ no_recent_reviews, but hours/address/signal/atualidade
    from the paid call land on normalized — so the record is NOT 'sem horário' for WhatsApp."""
    from brave.api.routers.dlq import _is_whatsapp_eligible

    stale_dt = (_NOW - timedelta(days=120)).replace(microsecond=0)
    fixture = {
        **_open_fixture([{"publishTime": stale_dt.isoformat(), "rating": 4, "text": "old"}]),
        "weekday_text": _HOURS,
        "formatted_address": _ADDRESS,
    }
    rio = _make_rio()

    mock_route, session = await _run_signal_with_session(rio, fixture)

    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_recent_reviews"
    assert rio.sub_state is None
    assert not mock_route.called
    n = rio.normalized
    assert n["weekday_text"] == _HOURS
    assert n["address"] == _ADDRESS
    assert n["signal"]["business_status"] == "OPERATIONAL"
    assert n["signal"]["weekday_text"] == _HOURS
    assert n["signal"]["reviews_recent_count"] == 0
    assert n["atualidade_value"] == 50.0  # 91–180d bucket
    assert n["corroboracao_value"] == 0.0
    assert n["most_recent_review_at"] == stale_dt.isoformat()
    assert "reviews" not in n and "old" not in str(n)  # no review text persisted (LGPD)
    assert _is_whatsapp_eligible(n) is False  # Google already gave the hours
    session.refresh.assert_called_once_with(rio, ["normalized"], with_for_update=True)


@pytest.mark.asyncio
async def test_signal_agent_no_reviews_at_all_still_persists_hours_and_signal() -> None:
    """Zero reviews anywhere → DLQ; hours/signal/address still persisted, no None date."""
    fixture = {**_open_fixture([]), "weekday_text": _HOURS, "formatted_address": _ADDRESS}
    rio = _make_rio()

    _, session = await _run_signal_with_session(rio, fixture)

    assert rio.dlq_reason == "no_recent_reviews"
    assert rio.normalized["weekday_text"] == _HOURS
    assert rio.normalized["address"] == _ADDRESS
    assert rio.normalized["signal"]["business_status"] == "OPERATIONAL"
    assert "most_recent_review_at" not in rio.normalized
    session.refresh.assert_called_once_with(rio, ["normalized"], with_for_update=True)


@pytest.mark.asyncio
async def test_signal_agent_closed_temporarily_persists_signals_and_recency() -> None:
    """CLOSED_TEMPORARILY → DLQ closed_temporarily (unchanged), with the paid signals and the
    merged recency persisted — same as PlacesEnrichmentAgent's temporarily-closed path."""
    recent_dt = (_NOW - timedelta(days=10)).replace(microsecond=0)
    fixture = {
        "place_id": "ChIJtest001",
        "business_status": "CLOSED_TEMPORARILY",
        "weekday_text": _HOURS,
        "formatted_address": _ADDRESS,
        "reviews": [{"publishTime": recent_dt.isoformat(), "rating": 5, "text": "ok"}],
    }
    rio = _make_rio()

    mock_route, session = await _run_signal_with_session(rio, fixture)

    assert rio.routing == "dlq"
    assert rio.dlq_reason == "closed_temporarily"
    assert rio.sub_state is None
    assert not mock_route.called
    n = rio.normalized
    assert n["signal"]["business_status"] == "CLOSED_TEMPORARILY"
    assert n["weekday_text"] == _HOURS
    assert n["address"] == _ADDRESS
    assert n["atualidade_value"] == 100.0
    assert n["most_recent_review_at"] == recent_dt.isoformat()
    session.refresh.assert_called_once_with(rio, ["normalized"], with_for_update=True)


@pytest.mark.asyncio
async def test_signal_agent_closed_permanently_writes_nothing() -> None:
    """Regression pin: the hard descarte still persists nothing onto normalized."""
    fixture = {
        "place_id": "ChIJtest001",
        "business_status": "CLOSED_PERMANENTLY",
        "weekday_text": _HOURS,
        "formatted_address": _ADDRESS,
        "reviews": [],
    }
    rio = _make_rio()
    before = dict(rio.normalized)

    _, session = await _run_signal_with_session(rio, fixture)

    assert rio.routing == "descarte"
    assert rio.normalized == before
    session.refresh.assert_not_called()
