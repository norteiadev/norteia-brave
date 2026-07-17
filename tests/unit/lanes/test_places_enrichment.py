"""Unit tests for PlacesEnrichmentAgent — Google Places enrichment for TA atrativos.

All tests run 100% offline:
  - FakePlacesClient from tests/fakes/fake_places.py (text_search + place_details)
  - Mock RioRecord objects (no real DB)
  - route_by_score / write_audit / record_event patched (agent isolated from scoring + I/O)

Covers:
  - confident match → weekday_text + atualidade(max) + most_recent_review_at + place_id_cache
  - business_status CLOSED_* on a confident match → descarte (no re-score)
  - no confident match (name/distance) → TA floor kept, still advances + re-scores, no DLQ
  - place_id_cache present (refresh path) → skips Text Search, only Place Details
  - idempotency guard (wrong sub_state) → no-op
  - cross-lane guard (place_id_cache + weekday_text) → Places-FSM record left untouched
  - atualidade = max(TA, Google) keeps the higher TA value
  - dlq bounce clears sub_state

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


def _make_rio(
    *,
    sub_state: str = "description_enriched",
    routing: str = "in_progress",
    extra_normalized: dict | None = None,
) -> MagicMock:
    """Minimal TA-attraction RioRecord mock (no place_id_cache, no weekday_text)."""
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.sub_state = sub_state
    rio.routing = routing
    rio.dlq_reason = None
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


# Google's precise coords for the matched place (slightly off the TA seed coords).
_G_LAT, _G_LNG = -16.4893, -39.0727


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
    """Confident match → hours + atualidade(max) + most_recent_review_at + place_id_cache."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    recent_dt = (_NOW - timedelta(days=10)).replace(microsecond=0)
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details(
            reviews=[{"publishTime": recent_dt.isoformat(), "rating": 5, "text": "ok"}]
        )},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    mock_route = await _run(agent, rio)

    assert rio.normalized["weekday_text"] == ["segunda-feira: 08:00 – 18:00", "domingo: Fechado"]
    assert rio.normalized["atualidade_value"] == 100.0  # recent Google review boosts to 100
    assert rio.normalized["most_recent_review_at"] == recent_dt.isoformat()
    assert rio.normalized["place_id_cache"] == "ChIJmatriz001"
    assert rio.normalized["google_place_id"] == "ChIJmatriz001"  # platform-facing field
    # Google's precise coords adopted over the TA seed coords.
    assert rio.normalized["lat"] == _G_LAT
    assert rio.normalized["lon"] == _G_LNG
    assert rio.sub_state == "places_enriched"
    assert mock_route.called
    assert fake.text_search_calls  # resolved via Text Search
    assert fake.place_details_calls == ["ChIJmatriz001"]


@pytest.mark.asyncio
async def test_closed_place_hard_descarte() -> None:
    """business_status CLOSED_* on a confident match → descarte, no re-score."""
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
    assert rio.sub_state is None
    assert not mock_route.called, "route_by_score must be skipped for a CLOSED place"


@pytest.mark.asyncio
async def test_no_name_match_keeps_floor() -> None:
    """A wrong-name result (< threshold) → TA floor kept, no Place Details, still advances."""
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
    assert rio.sub_state == "places_enriched"
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
    """Refresh path: place_id_cache present (no weekday_text) → skip Text Search."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_details={"ChIJcached": _details()},
    )
    rio = _make_rio(extra_normalized={"place_id_cache": "ChIJcached"})
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    await _run(agent, rio)

    assert fake.text_search_calls == [], "cached place_id must skip Text Search"
    assert fake.place_details_calls == ["ChIJcached"]
    assert rio.normalized["weekday_text"]


@pytest.mark.asyncio
async def test_idempotency_wrong_substate_noop() -> None:
    """sub_state != description_enriched → no-op (no Places calls, normalized untouched)."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_results={"Igreja Matriz": [_search_result()]})
    rio = _make_rio(sub_state="signals_gathered")
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)
    await _run(agent, rio)

    assert rio.sub_state == "signals_gathered"
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

    assert rio.sub_state == "description_enriched"  # untouched
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


@pytest.mark.asyncio
async def test_dlq_bounce_clears_substate() -> None:
    """When the re-score routes to dlq, sub_state is cleared (bounce back to DLQ)."""
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW)

    def _route_to_dlq(session, r, config):
        r.routing = "dlq"
        return r

    with patch("brave.lanes.atrativos.places_enrichment.write_audit"), \
         patch("brave.lanes.atrativos.places_enrichment.record_event"), \
         patch("brave.lanes.atrativos.places_enrichment.route_by_score", side_effect=_route_to_dlq):
        await agent.run(rio)

    assert rio.routing == "dlq"
    assert rio.sub_state is None
