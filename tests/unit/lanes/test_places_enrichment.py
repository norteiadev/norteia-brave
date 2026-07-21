"""Unit tests for PlacesEnrichmentAgent — Google Places enrichment for TA atrativos.

All tests run 100% offline:
  - FakePlacesClient from tests/fakes/fake_places.py (text_search + place_details)
  - Mock RioRecord objects (no real DB)
  - route_by_score / write_audit / record_event patched (agent isolated from scoring + I/O)

Design note: the agent runs REGARDLESS of routing/sub_state (a TA atrativo scores
~55 < 80 → dlq, sub_state=None) and keys idempotency on the ``google_enriched``
normalized marker — NOT sub_state. It does not touch sub_state.

Covers:
  - confident match → weekday_text + Google coords + atualidade(max) + most_recent_review_at
    + place_id_cache + google_place_id + google_enriched marker
  - runs on a dlq record (sub_state=None) — the E2E-caught regression
  - business_status CLOSED_* on a confident match → descarte + marker (no re-score)
  - no confident match → TA floor kept, marker set, still re-scores
  - place_id_cache present (refresh path) → skips Text Search, only Place Details
  - idempotency via google_enriched marker → no-op
  - cross-lane guard (place_id_cache + weekday_text) → Places-FSM record left untouched
  - atualidade = max(TA, Google) keeps the higher TA value

D-18 boundary: no import from brave.lanes.destinos.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tests.fakes.fake_places import FakePlacesClient

# Pinned reference clock so atualidade buckets + review recency are deterministic.
_NOW = datetime(2026, 6, 15, tzinfo=UTC)

# The atrativo's own coordinates (TA lat/lng, stored as normalized lat/lon).
_LAT, _LNG = -16.45, -39.06
# Google's precise coords for the matched place (slightly off the TA seed coords).
_G_LAT, _G_LNG = -16.4893, -39.0727


def _make_rio(
    *,
    routing: str = "dlq",
    sub_state=None,
    extra_normalized: dict | None = None,
) -> MagicMock:
    """Minimal TA-attraction RioRecord mock — defaults to the realistic post-description
    state (routing=dlq, sub_state=None), no place_id_cache, no weekday_text, no marker."""
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.sub_state = sub_state
    rio.routing = routing
    rio.dlq_reason = "score=55.50 below threshold_mar=80.0"
    rio.entity_type = "attraction"
    rio.uf = "BA"
    rio.canonical_key = "tripadvisor:attraction:12345"
    normalized = {
        "name": "Igreja Matriz",
        "lat": _LAT,
        "lon": _LNG,
        "municipio": "Porto Seguro",
        "municipio_id": "2925303",
        "origem_value": 65.0,
        "completude_value": 90.0,
        "corroboracao_value": 40.0,
        "atualidade_value": 0.0,
        "validacao_humana_value": 0.0,
    }
    if extra_normalized:
        normalized.update(extra_normalized)
    rio.normalized = normalized
    return rio


def _make_session() -> MagicMock:
    session = MagicMock()
    session.flush.return_value = None
    session.add.return_value = None
    return session


def _search_result(name: str = "Igreja Matriz", lat: float = _LAT, lng: float = _LNG) -> dict:
    return {
        "place_id": "ChIJmatriz001",
        "name": name,
        "location": {"lat": lat, "lng": lng},
    }


def _details(
    *,
    business_status: str = "OPERATIONAL",
    weekday_text: list[str] | None = None,
    reviews: list[dict] | None = None,
    location: dict | None = None,
) -> dict:
    return {
        "place_id": "ChIJmatriz001",
        "business_status": business_status,
        "weekday_text": weekday_text if weekday_text is not None else [
            "segunda-feira: 08:00 – 18:00",
            "domingo: Fechado",
        ],
        "reviews": reviews if reviews is not None else [],
        "location": location if location is not None else {"lat": _G_LAT, "lng": _G_LNG},
    }


async def _run(agent, rio):
    with patch("brave.lanes.atrativos.places_enrichment.write_audit"), \
         patch("brave.lanes.atrativos.places_enrichment.record_event"), \
         patch("brave.lanes.atrativos.places_enrichment.route_by_score") as mock_route:
        await agent.run(rio)
    return mock_route


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_persists_hours_and_liveness() -> None:
    """Confident match on a dlq record → hours + coords + liveness + ids + marker."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    recent_dt = (_NOW - timedelta(days=10)).replace(microsecond=0)
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details(
            reviews=[{"publishTime": recent_dt.isoformat(), "rating": 5, "text": "ok"}]
        )},
    )
    rio = _make_rio()  # routing=dlq, sub_state=None (realistic post-description)
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    mock_route = await _run(agent, rio)

    assert rio.normalized["weekday_text"] == ["segunda-feira: 08:00 – 18:00", "domingo: Fechado"]
    assert rio.normalized["atualidade_value"] == 100.0  # recent Google review boosts to 100
    assert rio.normalized["most_recent_review_at"] == recent_dt.isoformat()
    assert rio.normalized["place_id_cache"] == "ChIJmatriz001"
    assert rio.normalized["google_place_id"] == "ChIJmatriz001"
    assert rio.normalized["lat"] == _G_LAT  # Google coords adopted
    assert rio.normalized["lon"] == _G_LNG
    assert rio.normalized["google_enriched"] is True
    assert rio.sub_state is None  # FSM untouched
    assert mock_route.called
    assert fake.place_details_calls == ["ChIJmatriz001"]


@pytest.mark.asyncio
async def test_runs_on_dlq_record_regression() -> None:
    """Regression (E2E-caught): a dlq'd TA record (sub_state=None) is STILL enriched.

    The prior sub_state gate (== description_enriched) skipped every TA record because
    they dlq-bounce sub_state to None. This asserts Places now fires on that state.
    """
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio(routing="dlq", sub_state=None)
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    await _run(agent, rio)

    assert rio.normalized["weekday_text"]  # hours collected despite dlq
    assert rio.normalized["google_enriched"] is True
    assert fake.place_details_calls == ["ChIJmatriz001"]


@pytest.mark.asyncio
async def test_closed_place_hard_descarte() -> None:
    """business_status CLOSED_* on a confident match → descarte + marker, no re-score."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details(business_status="CLOSED_PERMANENTLY")},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    mock_route = await _run(agent, rio)

    assert rio.routing == "descarte"
    assert rio.dlq_reason == "closed_place"
    assert rio.normalized["google_enriched"] is True  # marker set (no re-enrich)
    assert not mock_route.called, "route_by_score must be skipped for a CLOSED place"


@pytest.mark.asyncio
async def test_no_name_match_keeps_floor() -> None:
    """A wrong-name result (< threshold) → TA floor kept, marker set, still re-scores."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result(name="Restaurante do Zé")]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    mock_route = await _run(agent, rio)

    assert "weekday_text" not in rio.normalized
    assert "place_id_cache" not in rio.normalized
    assert rio.normalized["lat"] == _LAT  # TA coords untouched (no match)
    assert rio.normalized["lon"] == _LNG
    assert rio.normalized["google_enriched"] is True  # marker set → no re-run
    assert rio.routing != "descarte"
    assert mock_route.called
    assert fake.place_details_calls == [], "no confident match → no Place Details call"


@pytest.mark.asyncio
async def test_far_location_rejected_keeps_floor() -> None:
    """Right name but far away (different city) → rejected, floor kept."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    # Same name, ~1100 km away (São Paulo) → outside the match radius (wrong city).
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result(lat=-23.55, lng=-46.63)]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    await _run(agent, rio)

    assert "weekday_text" not in rio.normalized
    assert fake.place_details_calls == []


@pytest.mark.asyncio
async def test_place_id_cache_skips_text_search() -> None:
    """Refresh path: place_id_cache present (no weekday_text, no marker) → skip Text Search."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_details={"ChIJcached": _details()})
    rio = _make_rio(extra_normalized={"place_id_cache": "ChIJcached"})
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    await _run(agent, rio)

    assert fake.text_search_calls == [], "cached place_id must skip Text Search"
    assert fake.place_details_calls == ["ChIJcached"]
    assert rio.normalized["weekday_text"]


@pytest.mark.asyncio
async def test_idempotency_marker_noop() -> None:
    """google_enriched already set → no-op (no Places calls, normalized untouched)."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_results={"Igreja Matriz": [_search_result()]})
    rio = _make_rio(extra_normalized={"google_enriched": True})
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    await _run(agent, rio)

    assert fake.text_search_calls == []
    assert fake.place_details_calls == []


@pytest.mark.asyncio
async def test_cross_lane_places_fsm_record_untouched() -> None:
    """A record with place_id_cache AND weekday_text (Places-FSM) → agent no-ops."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_details={"ChIJx": _details()})
    rio = _make_rio(extra_normalized={
        "place_id_cache": "ChIJx",
        "weekday_text": ["Monday: 9-5"],
    })
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    await _run(agent, rio)

    assert "google_enriched" not in rio.normalized  # untouched
    assert fake.place_details_calls == []


@pytest.mark.asyncio
async def test_atualidade_max_keeps_higher_ta_value() -> None:
    """Existing TA atualidade higher than the Google value → keep the TA value."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    # Google review is old (> 180 days → Google atualidade 0); TA already set 70.
    old_dt = (_NOW - timedelta(days=400)).replace(microsecond=0)
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details(
            reviews=[{"publishTime": old_dt.isoformat(), "rating": 4, "text": "ok"}]
        )},
    )
    rio = _make_rio(extra_normalized={"atualidade_value": 70.0})
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    await _run(agent, rio)

    assert rio.normalized["atualidade_value"] == 70.0  # max(70 TA, 0 Google)
