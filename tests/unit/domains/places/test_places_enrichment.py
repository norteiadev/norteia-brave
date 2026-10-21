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
  - cross-lane guard (place_id_cache + weekday_text) → no Places spend, marker written
  - Null client / Places failure → no marker; CostGuardError propagates
  - atualidade = max(TA, Google) keeps the higher TA value
  - description-only backfill (paid Places sub-step already done) → ZERO Places SKU spent

D-18 boundary: no import from other domains.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brave.config.settings import ScoreConfig
from tests.fakes.fake_llm import FakeLLMClient
from tests.fakes.fake_places import FakePlacesClient

# Pinned reference clock so atualidade buckets + review recency are deterministic.
_NOW = datetime(2026, 6, 15, tzinfo=UTC)

# The atrativo's own coordinates (TA lat/lng, stored as normalized lat/lon).
_LAT, _LNG = -16.45, -39.06
# Google's precise coords for the matched place (slightly off the TA seed coords).
_G_LAT, _G_LNG = -16.4893, -39.0727


def _make_rio(
    *,
    sub_state: str | None = None,
    routing: str = "dlq",
    extra_normalized: dict | None = None,
) -> MagicMock:
    """Minimal TA-attraction RioRecord mock — defaults to the realistic post-description
    state (routing=dlq, sub_state=None), no place_id_cache, no weekday_text, no marker."""
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.sub_state = sub_state
    rio.routing = routing
    # Explicit: a MagicMock attribute is truthy, and the inline description gate reads this
    # column ("is a paid request already in flight?"). None is the state of every record that
    # is not inside a batch.
    rio.descricao_batch_id = None
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


def _search_result(
    name: str = "Igreja Matriz",
    lat: float = _LAT,
    lng: float = _LNG,
    place_id: str = "ChIJmatriz001",
    types: list[str] | None = None,
) -> dict:
    return {
        "place_id": place_id,
        "name": name,
        "location": {"lat": lat, "lng": lng},
        # Default is the shape Places returns for a real POI. Tests that care about the
        # geographic guard pass `types` explicitly.
        "types": types
        if types is not None
        else ["tourist_attraction", "point_of_interest", "establishment"],
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
        "formatted_address": "Praça Central, Porto Seguro - BA",
        "weekday_text": weekday_text if weekday_text is not None else [
            "segunda-feira: 08:00 – 18:00",
            "domingo: Fechado",
        ],
        "reviews": reviews if reviews is not None else [],
        "location": location if location is not None else {"lat": _G_LAT, "lng": _G_LNG},
    }


async def _run(agent, rio):
    with patch("brave.domains.places.places_enrichment.write_audit"), \
         patch("brave.domains.places.places_enrichment.record_event"), \
         patch("brave.domains.places.places_enrichment.route_by_score") as mock_route:
        await agent.run(rio)
    return mock_route


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_persists_hours_and_liveness() -> None:
    """Confident match on a dlq record → hours + coords + liveness + ids + marker."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    recent_dt = (_NOW - timedelta(days=10)).replace(microsecond=0)
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details(
            reviews=[{"publishTime": recent_dt.isoformat(), "rating": 5, "text": "ok"}]
        )},
    )
    rio = _make_rio()  # routing=dlq, sub_state=None (realistic post-description)
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    mock_route = await _run(agent, rio)

    assert rio.normalized["weekday_text"] == ["segunda-feira: 08:00 – 18:00", "domingo: Fechado"]
    assert rio.normalized["atualidade_value"] == 100.0  # recent Google review boosts to 100
    assert rio.normalized["most_recent_review_at"] == recent_dt.isoformat()
    assert rio.normalized["place_id_cache"] == "ChIJmatriz001"
    assert rio.normalized["google_place_id"] == "ChIJmatriz001"
    assert rio.normalized["lat"] == _G_LAT  # Google coords adopted
    assert rio.normalized["lon"] == _G_LNG
    assert rio.normalized["google_enriched"] is True
    assert rio.normalized["address"] == "Praça Central, Porto Seguro - BA"
    # build_push_payload reads business_status + reviews_recent_count off this block.
    assert rio.normalized["signal"]["business_status"] == "OPERATIONAL"
    assert rio.normalized["signal"]["reviews_recent_count"] == 1  # the 10-day-old review
    assert rio.sub_state is None  # FSM untouched
    assert mock_route.called
    assert fake.place_details_calls == ["ChIJmatriz001"]


@pytest.mark.asyncio
async def test_runs_on_dlq_record_regression() -> None:
    """Regression (E2E-caught): a dlq'd TA record (sub_state=None) is STILL enriched.

    The prior sub_state gate (== description_enriched) skipped every TA record because
    they dlq-bounce sub_state to None. This asserts Places now fires on that state.
    """
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio(routing="dlq", sub_state=None)
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert rio.normalized["weekday_text"]  # hours collected despite dlq
    assert rio.normalized["google_enriched"] is True
    assert fake.place_details_calls == ["ChIJmatriz001"]


@pytest.mark.asyncio
async def test_closed_place_hard_descarte() -> None:
    """business_status CLOSED_* on a confident match → descarte + marker, no re-score."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details(business_status="CLOSED_PERMANENTLY")},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    mock_route = await _run(agent, rio)

    assert rio.routing == "descarte"
    assert rio.dlq_reason == "closed_place"
    assert rio.normalized["google_enriched"] is True  # marker set (no re-enrich)
    assert not mock_route.called, "route_by_score must be skipped for a CLOSED place"


@pytest.mark.asyncio
async def test_google_business_type_hard_descarte() -> None:
    """A confident match Google files as a business (Faraó na Praia, TA "Praias") →
    descarte nao_atrativo, types kept on the record, no re-score."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    details = {**_details(), "primary_type": "restaurant", "types": ["restaurant", "bar", "food"]}
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": details},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    mock_route = await _run(agent, rio)

    assert rio.routing == "descarte"
    assert rio.dlq_reason == "nao_atrativo"
    assert rio.normalized["google_primary_type"] == "restaurant"
    assert not mock_route.called


@pytest.mark.parametrize(
    ("primary", "types", "expected"),
    [
        ("restaurant", ["restaurant", "pub", "bar", "food"], True),  # Faraó na Praia
        ("condominium_complex", ["condominium_complex", "service"], True),  # Ecoville Park
        ("tour_agency", ["tour_agency", "travel_agency", "service"], True),
        ("clothing_store", ["clothing_store", "store"], True),
        ("deli", ["deli", "food", "store"], True),  # Marietta Delicatessen
        ("pizza_restaurant", ["pizza_restaurant", "restaurant"], True),
        ("coffee_shop", ["coffee_shop", "cafe"], True),
        ("shopping_mall", ["shopping_mall", "tourist_attraction"], True),  # malls always out
        ("pet_care", ["tourist_attraction", "park", "pet_care"], False),  # Parque dos Falcões
        ("non_profit_organization", ["non_profit_organization"], False),  # Projeto Tamar
        ("church", ["church", "place_of_worship"], False),
        # Google's generic "an address" type, not a business (Usina de Xingó, Farol da Ponta Verde)
        ("premise", ["premise", "establishment"], False),
        ("", ["restaurant", "museum", "park"], False),  # no primaryType → not judged
    ],
)
def test_google_not_atrativo(primary: str, types: list[str], expected: bool) -> None:
    from brave.domains.places.places_enrichment import _google_not_atrativo

    assert _google_not_atrativo({"primary_type": primary, "types": types}) is expected


@pytest.mark.asyncio
async def test_no_name_match_keeps_floor() -> None:
    """A wrong-name result (< threshold) → TA floor kept, marker set, still re-scores."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result(name="Restaurante do Zé")]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    mock_route = await _run(agent, rio)

    assert "weekday_text" not in rio.normalized
    assert "place_id_cache" not in rio.normalized
    assert "signal" not in rio.normalized  # no Places facts → no signal block
    assert rio.normalized["lat"] == _LAT  # TA coords untouched (no match)
    assert rio.normalized["lon"] == _LNG
    assert rio.normalized["google_enriched"] is True  # marker set → no re-run
    assert rio.routing != "descarte"
    assert mock_route.called
    assert fake.place_details_calls == [], "no confident match → no Place Details call"


@pytest.mark.asyncio
async def test_far_location_rejected_keeps_floor() -> None:
    """Right name but far away (different city) → rejected, floor kept."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    # Same name, ~1100 km away (São Paulo) → outside the match radius (wrong city).
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result(lat=-23.55, lng=-46.63)]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert "weekday_text" not in rio.normalized
    assert fake.place_details_calls == []


@pytest.mark.asyncio
async def test_geographic_entity_rejected_keeps_floor() -> None:
    """A município/bairro result is rejected even at a perfect name + distance match.

    Regression for the PR #24 finding: 3 of 15 sampled atrativos resolved to a `political`
    entity (Paraty → sublocality_level_1, Convento da Penha → neighborhood, Praia dos
    Carneiros → locality). Those carry no hours, no reviews and no business_status, yet they
    sit at the atrativo's own coordinates and share its name — so both existing guards pass
    and the wrong place_id used to be stamped onto the canonical record.
    """
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={
            "Igreja Matriz": [
                _search_result(types=["neighborhood", "political"]),
            ]
        },
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert fake.place_details_calls == [], "a political entity must never be fetched"
    assert "place_id_cache" not in rio.normalized
    assert "google_place_id" not in rio.normalized
    assert "weekday_text" not in rio.normalized
    assert rio.normalized["lat"] == _LAT  # TA coords untouched


@pytest.mark.asyncio
async def test_real_poi_wins_over_a_higher_scoring_geographic_entity() -> None:
    """The guard is per-candidate, so a real POI still wins when the município scores higher.

    The município matches the atrativo name exactly (score 100) and the POI only partially
    (< 100). Rejecting the winner after the fact would drop the whole result set; skipping
    per-candidate keeps the POI.
    """
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={
            "Igreja Matriz": [
                _search_result(
                    name="Igreja Matriz",
                    place_id="ChIJbairro",
                    types=["sublocality_level_1", "sublocality", "political"],
                ),
                _search_result(
                    name="Igreja Matriz de Nossa Senhora da Pena",
                    place_id="ChIJpoi",
                    types=["church", "tourist_attraction", "point_of_interest", "establishment"],
                ),
            ]
        },
        fixture_details={"ChIJpoi": _details()},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert fake.place_details_calls == ["ChIJpoi"]
    assert rio.normalized["google_place_id"] == "ChIJpoi"
    assert rio.normalized["weekday_text"]


@pytest.mark.asyncio
async def test_natural_feature_is_not_rejected() -> None:
    """`beach`/`natural_feature` is a legitimate atrativo — the guard must not over-reach.

    Measured: Praia de Camburi resolved to ["beach","natural_feature","establishment"] and
    returned an editorialSummary. Only the administrative entity is worthless.
    """
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={
            "Igreja Matriz": [
                _search_result(types=["beach", "natural_feature", "establishment"]),
            ]
        },
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert fake.place_details_calls == ["ChIJmatriz001"]
    assert rio.normalized["weekday_text"]


@pytest.mark.asyncio
async def test_result_without_types_is_still_matched() -> None:
    """A candidate with no `types` key at all is kept — the guard fails OPEN by design."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    bare = {
        "place_id": "ChIJmatriz001",
        "name": "Igreja Matriz",
        "location": {"lat": _LAT, "lng": _LNG},
    }
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [bare]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert fake.place_details_calls == ["ChIJmatriz001"]


@pytest.mark.asyncio
async def test_place_id_cache_skips_text_search() -> None:
    """Refresh path: place_id_cache present (no weekday_text, no marker) → skip Text Search."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_details={"ChIJcached": _details()})
    rio = _make_rio(extra_normalized={"place_id_cache": "ChIJcached"})
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert fake.text_search_calls == [], "cached place_id must skip Text Search"
    assert fake.place_details_calls == ["ChIJcached"]
    assert rio.normalized["weekday_text"]


@pytest.mark.asyncio
async def test_refresh_refetches_details_of_an_enriched_record() -> None:
    """refresh=True: an already-enriched record pays one Place Details (no Text Search),
    gets the fresh hours/review date and the refresh stamp."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJcached": _details(weekday_text=["Monday: 8-18"])},
    )
    rio = _make_rio(extra_normalized={
        "google_enriched": True,
        "place_id_cache": "ChIJcached",
        "weekday_text": ["Monday: 9-17"],
        "descricao_editorial": "Prosa já escrita.",
    })
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    with patch("brave.domains.places.places_enrichment.write_audit"), \
         patch("brave.domains.places.places_enrichment.record_event"), \
         patch("brave.domains.places.places_enrichment.route_by_score") as mock_route:
        await agent.run(rio, refresh=True)

    assert fake.text_search_calls == []
    assert fake.place_details_calls == ["ChIJcached"]
    assert rio.normalized["weekday_text"] == ["Monday: 8-18"]
    assert rio.normalized["places_refreshed_at"] == _NOW.isoformat()
    assert mock_route.called


@pytest.mark.asyncio
async def test_idempotency_marker_noop() -> None:
    """google_enriched set AND a description already written → nothing left to do."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_results={"Igreja Matriz": [_search_result()]})
    fake_llm = FakeLLMClient()
    rio = _make_rio(extra_normalized={
        "google_enriched": True,
        "descricao_editorial": "Prosa já escrita.",
    })
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), llm_client=fake_llm, now=_NOW, config=ScoreConfig()
    )
    mock_route = await _run(agent, rio)

    assert fake.text_search_calls == []
    assert fake.place_details_calls == []
    assert fake_llm.generate_calls == []
    assert not mock_route.called, "nothing left to do → no re-score"


@pytest.mark.asyncio
async def test_cross_lane_places_fsm_record_marked_without_spend() -> None:
    """Places-FSM record that already has a description → no Places/LLM spend, no re-score,
    but the marker IS written (F32/F62): without it the stalled-chain sweeper re-selected the
    record every pass (google_enriched NULL) and describe_uf never saw it."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_details={"ChIJx": _details()})
    fake_llm = FakeLLMClient()
    rio = _make_rio(extra_normalized={
        "place_id_cache": "ChIJx",
        "weekday_text": ["Monday: 9-5"],
        "descricao_editorial": "Prosa já escrita.",
    })
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), llm_client=fake_llm, now=_NOW, config=ScoreConfig()
    )
    mock_route = await _run(agent, rio)

    assert rio.normalized["google_enriched"] is True
    assert fake.text_search_calls == []
    assert fake.place_details_calls == []
    assert fake_llm.generate_calls == []
    assert not mock_route.called


@pytest.mark.asyncio
async def test_description_backfill_spends_no_places_sku() -> None:
    """GAP-5 regression: marker set + missing description → description-only pass.

    The paid Places sub-step is one-shot, the description sub-step is not: an already
    Places-enriched record whose copywriter pass never landed gets its prose on a later
    pass at ZERO Places spend (no Text Search, no Place Details).
    """
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    fake_llm = FakeLLMClient(generate_result="Prosa da Norteia.")
    rio = _make_rio(extra_normalized={"google_enriched": True, "completude_value": 75.0})
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), llm_client=fake_llm, now=_NOW, config=ScoreConfig()
    )
    mock_route = await _run(agent, rio)

    assert rio.normalized["descricao_editorial"] == "Prosa da Norteia."
    assert rio.normalized["completude_value"] == 90.0  # description degrau
    assert mock_route.called, "the backfilled description must be re-scored"
    assert fake.text_search_calls == [], "backfill pass must spend no Text Search SKU"
    assert fake.place_details_calls == [], "backfill pass must spend no Place Details SKU"


@pytest.mark.asyncio
async def test_cross_lane_record_gets_description_without_redetails() -> None:
    """Places-FSM record (place_id_cache + weekday_text, no marker) → description only.

    This agent is the ONLY writer of descricao_editorial, so the discovery lane would
    otherwise never get one — and the Details SKU it already spent is not re-spent.
    """
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_details={"ChIJx": _details()})
    fake_llm = FakeLLMClient(generate_result="Prosa da Norteia.")
    rio = _make_rio(extra_normalized={
        "place_id_cache": "ChIJx",
        "weekday_text": ["Monday: 9-5"],
    })
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), llm_client=fake_llm, now=_NOW, config=ScoreConfig()
    )
    await _run(agent, rio)

    assert rio.normalized["descricao_editorial"] == "Prosa da Norteia."
    assert rio.normalized["weekday_text"] == ["Monday: 9-5"]  # SignalAgent's hours kept
    assert fake.text_search_calls == []
    assert fake.place_details_calls == []


@pytest.mark.asyncio
async def test_descarte_record_never_earns_a_description() -> None:
    """A discarded record (closed place) must not earn an LLM call on a second pass."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_results={"Igreja Matriz": [_search_result()]})
    fake_llm = FakeLLMClient(generate_result="Prosa da Norteia.")
    rio = _make_rio(routing="descarte", extra_normalized={"google_enriched": True})
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), llm_client=fake_llm, now=_NOW, config=ScoreConfig()
    )
    mock_route = await _run(agent, rio)

    assert "descricao_editorial" not in rio.normalized
    assert fake_llm.generate_calls == []
    assert fake.text_search_calls == []
    assert not mock_route.called


@pytest.mark.asyncio
async def test_failing_copywriter_retries_are_bounded() -> None:
    """A copywriter that never returns prose is retried at most 3x, then the record no-ops.

    Without the ``descricao_attempts`` budget, "description absent" re-arms the backfill
    pass forever: a systemic copywriter failure (outage, cost guard) would re-spend an LLM
    call + audit + re-score on EVERY sweep, for every already-enriched atrativo.
    """
    from brave.domains.places.places_enrichment import (
        _MAX_DESCRIPTION_ATTEMPTS,
        PlacesEnrichmentAgent,
    )

    fake = FakePlacesClient(fixture_results={"Igreja Matriz": [_search_result()]})
    fake_llm = FakeLLMClient(generate_result="")  # always yields no prose
    rio = _make_rio(extra_normalized={"google_enriched": True})
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), llm_client=fake_llm, now=_NOW, config=ScoreConfig()
    )

    for _ in range(_MAX_DESCRIPTION_ATTEMPTS):
        assert (await _run(agent, rio)).called

    assert len(fake_llm.generate_calls) == _MAX_DESCRIPTION_ATTEMPTS
    assert rio.normalized["descricao_attempts"] == _MAX_DESCRIPTION_ATTEMPTS

    # Budget spent → full no-op: no LLM call, no re-score, no Places spend.
    mock_route = await _run(agent, rio)
    assert len(fake_llm.generate_calls) == _MAX_DESCRIPTION_ATTEMPTS
    assert not mock_route.called
    assert fake.text_search_calls == []


@pytest.mark.asyncio
async def test_exhausted_description_budget_is_logged() -> None:
    """An exhausted budget must be visible in the logs — not a silent no-op forever."""
    from brave.domains.places import places_enrichment as pe

    fake = FakePlacesClient(fixture_results={"Igreja Matriz": [_search_result()]})
    fake_llm = FakeLLMClient(generate_result="")
    rio = _make_rio(extra_normalized={
        "google_enriched": True,
        "descricao_attempts": pe._MAX_DESCRIPTION_ATTEMPTS,
    })
    agent = pe.PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), llm_client=fake_llm, now=_NOW, config=ScoreConfig()
    )
    with patch.object(pe.logger, "warning") as mock_warn:
        await _run(agent, rio)

    event, kwargs = mock_warn.call_args[0][0], mock_warn.call_args[1]
    assert event == "description_attempts_exhausted"
    assert kwargs["rio_id"] == str(rio.id)
    assert kwargs["attempts"] == pe._MAX_DESCRIPTION_ATTEMPTS


@pytest.mark.asyncio
async def test_cost_guard_block_never_burns_attempt_budget() -> None:
    """Budget tripped BEFORE dispatch → zero spend, so zero attempts consumed.

    The guard raises CostGuardError from pre_dispatch_check, before any API call. Counting
    that as an attempt would let one budget trip per sweep lock the ENTIRE backlog out of
    descriptions after _MAX_DESCRIPTION_ATTEMPTS sweeps, at zero LLM spend.
    """
    from brave.domains.places.places_enrichment import (
        _MAX_DESCRIPTION_ATTEMPTS,
        PlacesEnrichmentAgent,
    )
    from brave.shared.exceptions import CostGuardError

    fake = FakePlacesClient(fixture_results={"Igreja Matriz": [_search_result()]})
    fake_llm = FakeLLMClient(raise_on_call=CostGuardError("daily budget exceeded"))
    rio = _make_rio(extra_normalized={"google_enriched": True})
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), llm_client=fake_llm, now=_NOW, config=ScoreConfig()
    )

    for _ in range(_MAX_DESCRIPTION_ATTEMPTS + 2):
        await _run(agent, rio)  # never raises out of the agent (TA floor kept)

    assert "descricao_attempts" not in rio.normalized  # no permanent lockout
    # Still re-attempted every pass once the budget resets.
    assert len(fake_llm.generate_calls) == _MAX_DESCRIPTION_ATTEMPTS + 2


@pytest.mark.asyncio
async def test_successful_description_never_burns_attempt_budget() -> None:
    """Prose landed → no attempt counter written (the budget is for failures only)."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_results={"Igreja Matriz": [_search_result()]})
    fake_llm = FakeLLMClient(generate_result="Prosa da Norteia.")
    rio = _make_rio(extra_normalized={"google_enriched": True})
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), llm_client=fake_llm, now=_NOW, config=ScoreConfig()
    )
    await _run(agent, rio)

    assert rio.normalized["descricao_editorial"] == "Prosa da Norteia."
    assert "descricao_attempts" not in rio.normalized


@pytest.mark.asyncio
async def test_a_record_inside_a_live_batch_is_never_billed_inline() -> None:
    """A record holding a batch stamp already has a PAID description request in flight.

    The operator action this guards is the one the flag exists for: batches are slow, so
    atrativo_description_batch_enabled goes OFF mid-flight. Within one sweep the inline
    copywriter is rebuilt and sees exactly what a virgin record looks like — no
    descricao_editorial, descricao_attempts=0 — and fires a second full-price Sonnet +
    web_search call (~$0.09-0.12) for prose Anthropic is already producing. Collect then
    overwrites it an hour later: one description, two bills, plus two LLMGeneration rows.
    """
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    fake_llm = FakeLLMClient(generate_result="Prosa cobrada duas vezes.")
    rio = _make_rio()
    rio.descricao_batch_id = "msgbatch_01"  # submitted; Anthropic is billing already
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), llm_client=fake_llm, now=_NOW, config=ScoreConfig()
    )
    await _run(agent, rio)

    assert fake_llm.generate_calls == []  # no second bill
    assert "descricao_editorial" not in rio.normalized  # collect writes it, not us
    assert "descricao_attempts" not in rio.normalized  # not an attempt: nothing was tried
    # The unpaid rest of the pass still runs (hours are free of the copywriter).
    assert rio.normalized["weekday_text"]
    assert rio.normalized["google_enriched"] is True


@pytest.mark.asyncio
async def test_atualidade_max_keeps_higher_ta_value() -> None:
    """Existing TA atualidade higher than the Google value → keep the TA value."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    # Google review is old (> 180 days → Google atualidade 0); TA already set 70.
    old_dt = (_NOW - timedelta(days=400)).replace(microsecond=0)
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details(
            reviews=[{"publishTime": old_dt.isoformat(), "rating": 4, "text": "ok"}]
        )},
    )
    rio = _make_rio(extra_normalized={"atualidade_value": 70.0})
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert rio.normalized["atualidade_value"] == 70.0  # max(70 TA, 0 Google)


# ---------------------------------------------------------------------------
# Lost update: the whole-column write erases a concurrent writer
# ---------------------------------------------------------------------------


class _RacingPlacesClient:
    """Places client that commits a batched description WHILE the agent is in network I/O.

    That is the real timeline: enrich_places_task reads `normalized` at T0, spends ~2s in
    text_search/place_details, and copy_batch.apply_result commits its own transaction at
    T0+1s. Workers genuinely run in parallel (prefetch 1 is not concurrency 1).
    """

    def __init__(self, inner: FakePlacesClient, rio) -> None:  # noqa: ANN001
        self._inner = inner
        self._rio = rio

    async def text_search(self, query: str, uf: str):  # noqa: ANN201
        return await self._inner.text_search(query, uf)

    async def place_details(self, place_id: str, *, include_reviews: bool = True):  # noqa: ANN201
        details = await self._inner.place_details(place_id, include_reviews=include_reviews)
        self._rio.normalized = {
            **self._rio.normalized,
            "descricao_editorial": "Prosa paga pelo batch.",
            "completude_value": 90.0,
        }
        return details


@pytest.mark.asyncio
async def test_a_description_committed_during_the_places_call_is_not_erased() -> None:
    """The agent writes the WHOLE normalized column back from a pre-I/O snapshot. A paid
    batched description landing in that window is silently wiped — and since apply_result
    already cleared descricao_batch_id, the record is instantly eligible again and the next
    tick pays for the same description a second time. Merge onto the CURRENT value instead.
    """
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    inner = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio()
    session = _make_session()
    agent = PlacesEnrichmentAgent(
        places_client=_RacingPlacesClient(inner, rio), session=session, now=_NOW, config=ScoreConfig()
    )
    await _run(agent, rio)

    # The concurrent writer's keys survive...
    assert rio.normalized["descricao_editorial"] == "Prosa paga pelo batch."
    assert rio.normalized["completude_value"] == 90.0
    # ...and this run's own keys still landed.
    assert rio.normalized["weekday_text"]
    assert rio.normalized["google_enriched"] is True
    # Re-read under a row lock — merging a value read without one just narrows the window.
    session.refresh.assert_called_once_with(rio, ["normalized"], with_for_update=True)


@pytest.mark.asyncio
async def test_hard_descarte_writes_the_cause_to_the_atrativo_log() -> None:
    """The Log tab says why the record left the pipeline, and the audit's before_state
    is the routing BEFORE the descarte (it used to read the already-mutated value)."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    details = _details(business_status="CLOSED_PERMANENTLY")
    details["name"] = "Water Park Itumbiara"
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": details},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    with patch("brave.domains.places.places_enrichment.write_audit") as audit, \
         patch("brave.domains.places.places_enrichment.record_event") as event, \
         patch("brave.domains.places.places_enrichment.route_by_score"):
        await agent.run(rio)

    ev = event.call_args.kwargs
    assert ev["stage"] == "places_descarte"
    assert ev["status"] == "fail"
    assert ev["message"] == "Google Places marca como fechado permanentemente (Water Park Itumbiara)"
    assert ev["data"]["business_status"] == "CLOSED_PERMANENTLY"
    assert ev["data"]["place_id"] == "ChIJmatriz001"
    assert ev["data"]["routing_before"] == "dlq"
    assert audit.call_args.kwargs["before_state"] == {"routing": "dlq"}


@pytest.mark.asyncio
async def test_locate_only_accepts_a_confident_match_placed_in_the_uf() -> None:
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    in_uf = {**_search_result(name="Cachoeira do Macaquinho"), "municipio_ibge": "5200605"}
    other_uf = {**_search_result(name="Cachoeira do Macaquinho"), "municipio_ibge": ""}
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Cachoeira do Macaquinho": [in_uf]}),
        session=_make_session(),
        config=ScoreConfig(),
    )
    assert (await agent.locate("Cachoeira do Macaquinho", "GO"))["municipio_ibge"] == "5200605"

    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Cachoeira do Macaquinho": [other_uf]}),
        session=_make_session(),
        config=ScoreConfig(),
    )
    assert await agent.locate("Cachoeira do Macaquinho", "GO") is None


@pytest.mark.asyncio
async def test_locate_flags_a_swallowed_failure_and_clears_it_on_the_next_answer() -> None:
    """locate() never raises on a Places defect, so the TA ingest reads locate_failed to tell
    "Places could not place it" (stamp the quarantine) from "Places failed" (retry)."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    client = MagicMock()
    client.text_search = AsyncMock(side_effect=[RuntimeError("boom"), []])
    agent = PlacesEnrichmentAgent(places_client=client, session=_make_session(),
                                  config=ScoreConfig())
    assert agent.locate_failed is False
    assert await agent.locate("Cachoeira do Macaquinho", "GO") is None
    assert agent.locate_failed is True
    assert await agent.locate("Cachoeira do Macaquinho", "GO") is None
    assert agent.locate_failed is False


@pytest.mark.asyncio
async def test_locate_falls_back_to_the_municipio_every_in_uf_result_agrees_on() -> None:
    """No name match, but all in-UF results cluster in one município → município only,
    never a place_id (none of them is provably the atrativo). Disagreement → None."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    def res(name, ibge):
        return {**_search_result(name=name), "municipio_ibge": ibge}

    agree = [res("Mirante Jardim de Maytrea", "5200605"), res("Rio Preto", ""),
             res("Gota Sat Som", "5200605")]
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Jardim de Maytreia": agree}),
        session=_make_session(),
        config=ScoreConfig(),
    )
    assert await agent.locate("Jardim de Maytreia", "GO") == {
        "municipio_ibge": "5200605",
        "consensus": True,
    }

    split = [res("Cachoeira do Macacão", "5200605"), res("Cachoeira dos Macacos", "5220207")]
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Cachoeira do Vale do Rio Macaco": split}),
        session=_make_session(),
        config=ScoreConfig(),
    )
    assert await agent.locate("Cachoeira do Vale do Rio Macaco", "GO") is None


@pytest.mark.asyncio
async def test_coordless_record_never_matches_a_place_outside_its_uf() -> None:
    """Without coords there is no distance guard: a same-name church in another state
    (municipio_ibge "" — the client resolves it within the UF only) must not match."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    elsewhere = {**_search_result(), "municipio_ibge": ""}
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [elsewhere]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    rio = _make_rio()
    rio.normalized = {k: v for k, v in rio.normalized.items() if k not in ("lat", "lon")}
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert fake.place_details_calls == []
    assert "google_place_id" not in rio.normalized


@pytest.mark.asyncio
async def test_coordless_record_matches_only_near_its_municipio_seat() -> None:
    """No coords: the município seat stands in for them, with a wider radius — a same-name
    place 190 km away in the same UF is rejected, one near the seat is accepted."""
    from brave.core.models import Municipio
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    seat = Municipio(ibge_code="5212501", nome="Luziânia", uf="GO", lat=-16.2525, lng=-47.95)
    far = {**_search_result(lat=-14.4497, lng=-46.9469), "municipio_ibge": "5208152"}
    near = {**_search_result(lat=-16.30, lng=-47.90, place_id="ChIJnear"), "municipio_ibge": "5212501"}

    for results, expected in (([far], []), ([far, near], ["ChIJnear"])):
        fake = FakePlacesClient(
            fixture_results={"Igreja Matriz": results},
            fixture_details={"ChIJnear": _details()},
        )
        session = _make_session()
        session.get.return_value = seat
        rio = _make_rio(extra_normalized={"municipio_id": "5212501"})
        rio.normalized = {k: v for k, v in rio.normalized.items() if k not in ("lat", "lon")}
        await _run(PlacesEnrichmentAgent(places_client=fake, session=session, now=_NOW, config=ScoreConfig()), rio)
        assert fake.place_details_calls == expected


@pytest.mark.asyncio
async def test_temporarily_closed_is_enriched_but_parked_in_dlq() -> None:
    """CLOSED_TEMPORARILY: no descarte — hours/coords are kept, the record goes to the DLQ
    with dlq_reason "closed_temporarily" even when the re-score would promote it."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details(business_status="CLOSED_TEMPORARILY")},
    )
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())

    def promote(_session, r, _config, **_kw):
        r.routing = "mar"

    with patch("brave.domains.places.places_enrichment.write_audit"), \
         patch("brave.domains.places.places_enrichment.record_event") as event, \
         patch("brave.domains.places.places_enrichment.route_by_score", side_effect=promote):
        await agent.run(rio)

    assert rio.routing == "dlq"
    assert rio.dlq_reason == "closed_temporarily"
    assert rio.normalized["weekday_text"]  # enriched, not discarded
    assert rio.normalized["signal"]["business_status"] == "CLOSED_TEMPORARILY"
    ev = event.call_args.kwargs
    assert ev["stage"] == "places_enriched"
    assert ev["data"]["business_status"] == "CLOSED_TEMPORARILY"
    assert "fechado temporariamente" in ev["message"]


# ---------------------------------------------------------------------------
# Conditional reviews: Place Details drops the reviews field (Enterprise $20/1k instead of
# Enterprise+Atmosphere $25/1k) only when nothing in the run needs it.
# ---------------------------------------------------------------------------

_GOOGLE_REVIEW_DT = (_NOW - timedelta(days=3)).replace(microsecond=0)


def _reviews_fake(**details_kw) -> FakePlacesClient:
    return FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": {
            **_details(reviews=[{"publishTime": _GOOGLE_REVIEW_DT.isoformat(), "text": "ok"}]),
            **details_kw,
        }},
    )


@pytest.mark.asyncio
async def test_record_passing_backstop_skips_reviews_and_keeps_recency() -> None:
    """TA date ≤30d (atualidade 100) with backstop headroom → reviews dropped from the mask;
    the TA date, atualidade and the last known reviews_recent_count stand."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    ta_dt = (_NOW - timedelta(days=20)).replace(microsecond=0).isoformat()
    fake = _reviews_fake()
    rio = _make_rio(extra_normalized={
        "most_recent_review_at": ta_dt,
        "atualidade_value": 100.0,
        "signal": {"business_status": "OPERATIONAL", "reviews_recent_count": 2},
    })
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    with patch("brave.domains.places.places_enrichment.logger") as log:
        await _run(agent, rio)

    assert fake.place_details_include_reviews == [False]
    assert rio.normalized["most_recent_review_at"] == ta_dt
    assert rio.normalized["atualidade_value"] == 100.0
    assert rio.normalized["signal"]["reviews_recent_count"] == 2
    assert rio.normalized["weekday_text"]  # the rest of the Details call still lands
    assert any(c.args[0] == "places_details_reviews_skipped" for c in log.info.call_args_list)

    # No earlier signal block → the count is unknown (push sends null), never a fake 0.
    rio = _make_rio(extra_normalized={"most_recent_review_at": ta_dt, "atualidade_value": 100.0})
    await _run(PlacesEnrichmentAgent(places_client=_reviews_fake(), session=_make_session(), now=_NOW, config=ScoreConfig()), rio)
    assert rio.normalized["signal"]["reviews_recent_count"] is None
    # ...and the persisted block still round-trips through its schema.
    from brave.domains.places.schemas import SignalResult

    assert SignalResult.model_validate(rio.normalized["signal"]).reviews_recent_count is None


@pytest.mark.asyncio
async def test_record_failing_backstop_fetches_reviews_and_merges_google_date() -> None:
    """Stale (or missing) TA date → reviews kept, Google's newer date max-merged as before."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    stale = (_NOW - timedelta(days=200)).isoformat()
    for extra in ({"most_recent_review_at": stale}, {}):
        fake = _reviews_fake()
        rio = _make_rio(extra_normalized=extra)
        await _run(PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig()), rio)

        assert fake.place_details_include_reviews == [True]
        assert rio.normalized["most_recent_review_at"] == _GOOGLE_REVIEW_DT.isoformat()
        assert rio.normalized["signal"]["reviews_recent_count"] == 1


@pytest.mark.asyncio
async def test_backstop_passing_record_that_google_could_still_help_keeps_reviews() -> None:
    """Passing the backstop TODAY is not enough to drop reviews — the step is one-shot and
    promote_to_mar re-checks later (steward DLQ validation). Reviews are kept when:
      - TA atualidade < 100, even with ample backstop headroom (established, 60d / 200d):
        Google's fresh review lifts it 70/40 → 100, +4.5/+9 score pts;
      - the TA date is near the 90d edge (85d, review_count 8: fails 10 days later);
      - the established date is near the 365d edge (360d, review_count 25)."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    cases = (
        {"most_recent_review_at": (_NOW - timedelta(days=60)).isoformat(), "atualidade_value": 70.0,
         "review_count": 30},
        {"most_recent_review_at": (_NOW - timedelta(days=200)).isoformat(), "atualidade_value": 40.0,
         "review_count": 30},
        {"most_recent_review_at": (_NOW - timedelta(days=85)).isoformat(), "atualidade_value": 70.0,
         "review_count": 8},
        {"most_recent_review_at": (_NOW - timedelta(days=360)).isoformat(), "atualidade_value": 40.0,
         "review_count": 25},
    )
    for extra in cases:
        fake = _reviews_fake()
        rio = _make_rio(extra_normalized=extra)
        await _run(PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig()), rio)

        assert fake.place_details_include_reviews == [True], extra
        assert rio.normalized["most_recent_review_at"] == _GOOGLE_REVIEW_DT.isoformat()
        assert rio.normalized["atualidade_value"] == 100.0


@pytest.mark.asyncio
async def test_reviews_skip_follows_the_ta_60_day_rule() -> None:
    """Operator rule: a TA review under 60 days (atualidade 100) skips Google's reviews,
    whatever the backstop headroom; without one, the reviews are fetched."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    def at(days: int) -> str:
        return (_NOW - timedelta(days=days)).isoformat()

    for extra, expected in (
        ({"most_recent_review_at": at(59), "atualidade_value": 100.0, "review_count": 5}, False),
        ({"most_recent_review_at": at(61), "atualidade_value": 70.0, "review_count": 5}, True),
        ({}, True),  # no TA date at all
    ):
        fake = _reviews_fake()
        rio = _make_rio(extra_normalized=extra)
        await _run(PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig()), rio)
        assert fake.place_details_include_reviews == [expected], extra


@pytest.mark.asyncio
async def test_enrichment_never_writes_review_count() -> None:
    """review_count drives the backstop's established window and is calibrated on TA review
    counts — the Places enrichment leaves it exactly as the TA ingest wrote it."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    for extra, expected in (({}, None), ({"review_count": 12}, 12)):
        rio = _make_rio(extra_normalized=extra)
        fake = _reviews_fake()
        await _run(PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig()), rio)
        assert rio.normalized.get("review_count") == expected
        assert "google_rating_count" not in rio.normalized


@pytest.mark.asyncio
async def test_description_only_pass_keeps_closed_temporarily() -> None:
    """F40: a describe pass (Places already done → no Details) must not let the re-score erase
    the closed_temporarily verdict nor carry the record to Mar — only fresh Details re-decide it."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    top = {k: 100.0 for k in ("origem_value", "completude_value", "corroboracao_value",
                              "atualidade_value", "validacao_humana_value")}
    rio = _make_rio(extra_normalized={
        **top, "google_enriched": True,
        "most_recent_review_at": (_NOW - timedelta(days=5)).isoformat(),
    })
    rio.dlq_reason = "closed_temporarily"
    agent = PlacesEnrichmentAgent(
        places_client=_reviews_fake(), session=_make_session(),
        llm_client=FakeLLMClient(generate_result="Prosa."), now=_NOW, config=ScoreConfig(),
    )
    with patch("brave.domains.places.places_enrichment.write_audit"), \
         patch("brave.domains.places.places_enrichment.record_event"):
        await agent.run(rio, description=("Prosa.", None, False))

    assert rio.normalized["descricao_editorial"] == "Prosa."
    assert (rio.routing, rio.dlq_reason) == ("dlq", "closed_temporarily")


_TOP = {k: 100.0 for k in ("origem_value", "completude_value", "corroboracao_value",
                           "atualidade_value", "validacao_humana_value")}


@pytest.mark.asyncio
async def test_reopened_place_leaves_closed_temporarily() -> None:
    """The exit side of C5: fresh Details say OPERATIONAL → the closed_temporarily verdict is
    re-decided and dropped (redecided), else the record sits in the DLQ forever."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    rio = _make_rio(extra_normalized={
        **_TOP, "place_id_cache": "ChIJmatriz001",
        "most_recent_review_at": (_NOW - timedelta(days=5)).isoformat(),
    })
    rio.dlq_reason = "closed_temporarily"
    agent = PlacesEnrichmentAgent(
        places_client=_reviews_fake(business_status="OPERATIONAL"), session=_make_session(),
        now=_NOW, config=ScoreConfig(),
    )
    with patch("brave.domains.places.places_enrichment.write_audit"), \
         patch("brave.domains.places.places_enrichment.record_event"):
        await agent.run(rio)

    assert rio.dlq_reason != "closed_temporarily"
    assert rio.routing == "mar"


@pytest.mark.asyncio
async def test_grounded_rewrite_releases_descricao_nao_fundamentada() -> None:
    """A later describe pass whose cascade comes back grounded (motivo None) re-decides the
    old verdict: the record must not stay in the DLQ with a reason that no longer holds."""
    from brave.domains.places.copywriter import CascadeResult
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    rio = _make_rio(extra_normalized={
        **_TOP, "google_enriched": True,
        "most_recent_review_at": (_NOW - timedelta(days=5)).isoformat(),
    })
    rio.dlq_reason = "descricao_nao_fundamentada"
    agent = PlacesEnrichmentAgent(
        places_client=_reviews_fake(), session=_make_session(),
        llm_client=FakeLLMClient(generate_result="Prosa."), now=_NOW, config=ScoreConfig(),
    )
    with patch("brave.domains.places.places_enrichment.write_audit"), \
         patch("brave.domains.places.places_enrichment.record_event"):
        await agent.run(
            rio, description=("Prosa.", CascadeResult(prose="Prosa.", groundedness=0.95), False)
        )

    assert rio.normalized["descricao_editorial"] == "Prosa."
    assert rio.dlq_reason != "descricao_nao_fundamentada"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("paid", "motivo"),
    [(False, None), (True, None), (True, "sem_mencao")],
    ids=["search_failed", "writer_failed", "sem_mencao"],
)
async def test_transient_cascade_failure_keeps_verdict_and_draft(paid, motivo) -> None:
    """A search/writer failure (no prose, no motivo) judged nothing, and a sem_mencao pass (the
    search missed the place, the model never wrote) says nothing about an earlier draft: none
    may release the sticky descricao_nao_fundamentada verdict nor wipe the steward's draft."""
    from brave.clients.parallel import ParallelSearch
    from brave.domains.places.copywriter import CascadeResult
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    busca = ParallelSearch(None, "turbo", "", [], [], None, None, 0.0, 0) if paid else None
    rio = _make_rio(extra_normalized={
        **_TOP, "google_enriched": True,
        "most_recent_review_at": (_NOW - timedelta(days=5)).isoformat(),
        "descricao_gate": "nao_fundamentada", "descricao_rascunho": "Rascunho.",
    })
    rio.routing, rio.dlq_reason = "dlq", "descricao_nao_fundamentada"
    agent = PlacesEnrichmentAgent(
        places_client=_reviews_fake(), session=_make_session(),
        llm_client=FakeLLMClient(generate_result="Prosa."), now=_NOW, config=ScoreConfig(),
    )
    with patch("brave.domains.places.places_enrichment.write_audit"), \
         patch("brave.domains.places.places_enrichment.record_event"):
        await agent.run(
            rio, description=(None, CascadeResult(None, motivo=motivo, busca=busca), False)
        )

    assert rio.normalized["descricao_rascunho"] == "Rascunho."
    assert rio.normalized["descricao_gate"] == "nao_fundamentada"
    assert (rio.routing, rio.dlq_reason) == ("dlq", "descricao_nao_fundamentada")


@pytest.mark.asyncio
async def test_ta_unplaced_record_is_not_enriched_nor_described() -> None:
    """TA put the card in another UF (ta_uf != uf, municipio_nao_confirmado): a Places search
    or description in the stored UF can only find a homonym, and the cascade re-decision used
    to drop the TA verdict — the agent must not touch the record at all."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    rio = _make_rio(extra_normalized={"ta_uf": "MG", "ta_backfilled_at": _NOW.isoformat()})
    rio.dlq_reason = "municipio_nao_confirmado"
    fake = _reviews_fake()
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(),
        llm_client=FakeLLMClient(generate_result="Prosa."), now=_NOW, config=ScoreConfig(),
    )
    assert agent.wants_description(rio) is False
    await agent.run(rio)

    assert fake.text_search_calls == [] and fake.place_details_calls == []
    assert "google_enriched" not in rio.normalized and "descricao_editorial" not in rio.normalized
    assert (rio.routing, rio.dlq_reason) == ("dlq", "municipio_nao_confirmado")


@pytest.mark.asyncio
async def test_locate_consensus_then_run_spends_one_text_search() -> None:
    """locate() consensus (município, no place_id) → the inline run() re-matches the SAME
    results with the seat distance guard locate lacked — a result whose município name did
    not resolve ("") near the seat can still match (Terra Ronca: the park sits in the next
    município) — without paying Text Search a second time. Out-of-UF places never get here:
    RealPlacesClient.text_search drops them."""
    from brave.core.models import Municipio
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    seat = Municipio(ibge_code="5200605", nome="Alto Paraíso de Goiás", uf="GO", lat=-14.13, lng=-47.51)
    results = [
        # Name matches but its município name did not resolve ("") → locate drops it.
        {**_search_result(lat=-14.10, lng=-47.50, place_id="ChIJborder"), "municipio_ibge": ""},
        {**_search_result(name="Pousada Qualquer", place_id="ChIJother"), "municipio_ibge": "5200605"},
    ]
    fake = FakePlacesClient(
        fixture_results={"Igreja da Matriz": results},
        fixture_details={"ChIJborder": _details()},
    )
    session = _make_session()
    session.get.return_value = seat
    agent = PlacesEnrichmentAgent(places_client=fake, session=session, now=_NOW, config=ScoreConfig())

    # locate() gets the raw TA card name; run() reads the Rio's normalize_name()d one
    # (str.title() → "Da") — the cached search must still be reused.
    assert await agent.locate("Igreja da Matriz", "BA") == {"municipio_ibge": "5200605", "consensus": True}
    rio = _make_rio(extra_normalized={"municipio_id": "5200605", "name": "Igreja Da Matriz"})
    rio.normalized = {k: v for k, v in rio.normalized.items() if k not in ("lat", "lon")}
    await _run(agent, rio)

    assert len(fake.text_search_calls) == 1
    assert fake.place_details_calls == ["ChIJborder"]

    # The slot is one-shot: the next record pays its own search.
    rio = _make_rio()
    await _run(agent, rio)
    assert len(fake.text_search_calls) == 2


# ---------------------------------------------------------------------------
# google_enriched only after Places really answered (F10/F27) + pause errors (C4)
# ---------------------------------------------------------------------------


class _RaisingPlaces(FakePlacesClient):
    """FakePlacesClient whose calls raise ``exc`` (records the call first)."""

    def __init__(self, exc: BaseException, **kwargs) -> None:
        super().__init__(**kwargs)
        self._exc = exc

    async def text_search(self, query: str, uf: str) -> list[dict]:
        await super().text_search(query, uf)
        raise self._exc

    async def place_details(self, place_id: str, *, include_reviews: bool = True) -> dict:
        await super().place_details(place_id, include_reviews=include_reviews)
        raise self._exc


@pytest.mark.asyncio
async def test_null_client_never_marks_google_enriched() -> None:
    """places_enrichment_enabled=False / offline → NullPlacesClient: the paid sub-step did not
    run, so the marker must stay unset (it used to be written, making turning Places back on
    a no-op for every record the sweep had touched)."""
    from brave.clients.null_places import NullPlacesClient
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    rio = _make_rio()
    agent = PlacesEnrichmentAgent(
        places_client=NullPlacesClient(), session=_make_session(), now=_NOW, config=ScoreConfig()
    )
    await _run(agent, rio)

    assert "google_enriched" not in rio.normalized


@pytest.mark.asyncio
async def test_places_failure_keeps_floor_without_marker() -> None:
    """A Places defect (5xx after retries, 403 API not enabled, ...) keeps the TA floor and
    leaves the record unmarked so a later pass retries it."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = _RaisingPlaces(RuntimeError("PermissionDenied: Places API (New) not enabled"))
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert len(fake.text_search_calls) == 1
    assert "google_enriched" not in rio.normalized
    assert "weekday_text" not in rio.normalized


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["run", "locate"])
async def test_cost_guard_propagates_and_writes_nothing(where: str) -> None:
    """The Places daily cap (CostGuardError, raised before dispatch) must reach the caller so
    it pauses — not be read as "no Google data" and marked enriched."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent
    from brave.shared.exceptions import CostGuardError

    fake = _RaisingPlaces(CostGuardError("Daily Google Places budget exceeded."))
    rio = _make_rio()
    session = _make_session()
    agent = PlacesEnrichmentAgent(places_client=fake, session=session, now=_NOW, config=ScoreConfig())

    with pytest.raises(CostGuardError):
        if where == "run":
            await _run(agent, rio)
        else:
            await agent.locate("Igreja Matriz", "BA")
    assert "google_enriched" not in rio.normalized


@pytest.mark.asyncio
async def test_retired_cached_place_id_is_cleared_for_a_re_search() -> None:
    """Details NotFound on a cached place_id → the dead id is cleared (not marked), so the
    next pass re-resolves by Text Search instead of failing on it forever."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    class NotFound(Exception):  # noqa: N818 — name-matched, like google.api_core.exceptions.NotFound
        pass

    fake = _RaisingPlaces(NotFound("place not found"))
    rio = _make_rio(extra_normalized={"place_id_cache": "ChIJdead", "google_place_id": "ChIJdead"})
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig())
    await _run(agent, rio)

    assert fake.place_details_calls == ["ChIJdead"]
    assert rio.normalized["place_id_cache"] is None
    assert rio.normalized["google_place_id"] is None
    assert "google_enriched" not in rio.normalized


# ---------------------------------------------------------------------------
# Review fixes: no Text Search paid for nothing
# ---------------------------------------------------------------------------


class _BudgetedFake(FakePlacesClient):
    """FakePlacesClient with RealPlacesClient's public budget check: room for ``room`` USD."""

    def __init__(self, room: float, **kw) -> None:
        super().__init__(**kw)
        self._room = room
        self.checked: list[float] = []

    def check_budget(self, usd: float) -> None:
        from brave.shared.exceptions import CostGuardError

        self.checked.append(usd)
        if usd > self._room:
            raise CostGuardError("cap")


@pytest.mark.asyncio
async def test_pair_is_budgeted_before_the_text_search_is_paid() -> None:
    """Room for a Text Search but not for Text Search + Details: stop BEFORE paying the
    search — it used to be paid, then the Details tripped the cap and the search was paid
    again on resume."""
    from brave.clients.places import USD_DETAILS_ENTERPRISE_ATMOSPHERE, USD_TEXT_SEARCH_PRO
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent
    from brave.shared.exceptions import CostGuardError

    fake = _BudgetedFake(
        room=0.04,
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": _details()},
    )
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig()
    )

    with pytest.raises(CostGuardError):
        await _run(agent, _make_rio())

    assert fake.text_search_calls == []
    assert fake.checked == [pytest.approx(USD_TEXT_SEARCH_PRO + USD_DETAILS_ENTERPRISE_ATMOSPHERE)]


@pytest.mark.asyncio
async def test_details_failure_keeps_the_place_id_the_search_found() -> None:
    """Text Search matched (paid) but Details failed transiently: keep the id so the next
    pass goes straight to Details; no marker (google_enriched) — it is retried."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_results={"Igreja Matriz": [_search_result()]})
    fake.place_details = AsyncMock(side_effect=RuntimeError("503 ServiceUnavailable"))
    rio = _make_rio()
    agent = PlacesEnrichmentAgent(
        places_client=fake, session=_make_session(), now=_NOW, config=ScoreConfig()
    )

    await _run(agent, rio)

    assert rio.normalized["place_id_cache"] == "ChIJmatriz001"
    assert rio.normalized.get("google_enriched") is not True


@pytest.mark.asyncio
async def test_rate_limited_description_never_burns_attempt_budget() -> None:
    """A 429 past the retries keeps the budget: the quota, not the atrativo, failed."""
    from brave.domains.places.copywriter import CascadeResult
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    fake = FakePlacesClient(fixture_results={"Igreja Matriz": [_search_result()]})
    rio = _make_rio(extra_normalized={"google_enriched": True})
    agent = PlacesEnrichmentAgent(
        places_client=fake,
        session=_make_session(),
        llm_client=FakeLLMClient(generate_result=""),
        now=_NOW,
        config=ScoreConfig(),
    )
    with patch("brave.domains.places.places_enrichment.write_audit"), \
         patch("brave.domains.places.places_enrichment.record_event"), \
         patch("brave.domains.places.places_enrichment.route_by_score"):
        await agent.run(rio, description=(None, CascadeResult(None, rate_limited=True), False))

    assert "descricao_attempts" not in rio.normalized
    assert "descricao_gate" not in rio.normalized


# ---------------------------------------------------------------------------
# AL sync 10/2026 — locate() fixes from the live Text Search evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locate_places_a_result_by_its_coords_when_no_municipio_name() -> None:
    """"Farol - Ponta Verde" came back with only administrative_area_level_1 (no city), but
    with coords: the nearest IBGE seat of the UF places it."""
    from brave.core.models import Municipio
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    farol = {
        **_search_result(name="Farol da Ponta Verde", lat=-9.6658, lng=-35.6950,
                         types=["premise"]),
        "municipio_nome": "",
        "municipio_ibge": "",
        "uf_short": "AL",
    }
    session = _make_session()
    session.scalars.return_value.all.return_value = [
        Municipio(ibge_code="2704302", nome="Maceió", uf="AL", lat=-9.6658, lng=-35.7350),
        Municipio(ibge_code="2706901", nome="Paripueira", uf="AL", lat=-9.4650, lng=-35.5520),
    ]
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Farol - Ponta Verde": [farol]}),
        session=session,
        config=ScoreConfig(),
    )
    match = await agent.locate("Farol - Ponta Verde", "AL")
    assert match is not None and match["municipio_ibge"] == "2704302"


@pytest.mark.asyncio
async def test_locate_coords_fallback_never_reaches_a_far_seat() -> None:
    """A point with no seat of the UF within reach stays unresolved (no wrong município)."""
    from brave.core.models import Municipio
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    far = {**_search_result(name="Farol da Ponta Verde", lat=-3.0, lng=-60.0),
           "municipio_ibge": "", "uf_short": "AL"}
    session = _make_session()
    session.scalars.return_value.all.return_value = [
        Municipio(ibge_code="2704302", nome="Maceió", uf="AL", lat=-9.6658, lng=-35.7350),
    ]
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Farol - Ponta Verde": [far]}),
        session=session,
        config=ScoreConfig(),
    )
    assert await agent.locate("Farol - Ponta Verde", "AL") is None


@pytest.mark.asyncio
async def test_locate_consensus_by_two_thirds_majority() -> None:
    """"Praia de Pratagi": no name match, the Pratagy results sit in Maceió and one outlier
    elsewhere — unanimity dropped the card; a >= 2/3 majority places it."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    def res(name, ibge):
        return {**_search_result(name=name), "municipio_ibge": ibge}

    results = [res("Pratagy Beach Resort", "2704302"), res("Pratagy Acqua Park", "2704302"),
               res("Piscinas Naturais do Pratagy", "2704302"), res("Praia da Sereia", "2704302"),
               res("Pousada Paripueira", "2706901")]
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Praia de Pratagi": results}),
        session=_make_session(),
        config=ScoreConfig(),
    )
    assert await agent.locate("Praia de Pratagi", "AL") == {
        "municipio_ibge": "2704302",
        "consensus": True,
    }

    # Below 2/3 (2 of 4) → still no verdict.
    split = [res("A", "2704302"), res("B", "2704302"), res("C", "2706901"), res("D", "2700300")]
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Praia de Pratagi": split}),
        session=_make_session(),
        config=ScoreConfig(),
    )
    assert await agent.locate("Praia de Pratagi", "AL") is None


def test_best_match_prefers_a_place_over_a_same_name_business() -> None:
    """"Piscinas Naturais de Pajuçara": the exact-name result is a tour agency; a
    tourist_attraction that also clears the name threshold wins over it."""
    from brave.domains.places.places_enrichment import _best_match

    results = [
        _search_result(name="Piscinas naturais de Maceió", place_id="ChIJmaceio",
                       types=["tourist_attraction", "point_of_interest", "establishment"]),
        _search_result(name="Piscinas Naturais da Ponta Verde", place_id="ChIJpontaverde",
                       types=["tourist_attraction", "point_of_interest", "establishment"]),
        _search_result(name="Piscinas naturais de Pajuçara", place_id="ChIJagency",
                       types=["tour_agency", "travel_agency", "point_of_interest"]),
    ]
    card = "Piscinas Naturais de Pajuçara"
    match = _best_match(results, card, _LAT, _LNG, 20.0, "Pontos de interesse & marcos")
    assert match is not None and match["place_id"] == "ChIJmaceio"

    # No place-typed alternative → the business is still the match (judged on Details).
    match = _best_match(results[2:], card, _LAT, _LNG, 20.0, "Pontos de interesse & marcos")
    assert match is not None and match["place_id"] == "ChIJagency"

    # A card TA files as a tour/shop/food (or no category) keeps its own business match.
    for category in ("Passeios de barco", None):
        match = _best_match(results, card, _LAT, _LNG, 20.0, category)
        assert match is not None and match["place_id"] == "ChIJagency"


@pytest.mark.asyncio
async def test_business_match_on_a_ta_attraction_category_goes_to_review() -> None:
    """"Maragogi Beach" (TA "Praias") matched only a restaurant: a human decides (DLQ),
    instead of a descarte, and none of the restaurant's data lands on the record."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    details = {**_details(), "primary_type": "restaurant", "types": ["restaurant", "food"]}
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": details},
    )
    # The business's id rode in from locate() as place_id_cache (the ingest payload).
    rio = _make_rio(extra_normalized={"category": "Praias", "place_id_cache": "ChIJmatriz001",
                                      "google_place_id": "ChIJmatriz001"})
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW,
                                  config=ScoreConfig())
    mock_route = await _run(agent, rio)

    assert (rio.routing, rio.dlq_reason) == ("dlq", "google_tipo_negocio")
    assert rio.normalized["google_enriched"] is True
    assert rio.normalized["google_primary_type"] == "restaurant"
    # Cleared: refresh_places would re-bill it, a Mar approval would publish the business id.
    assert rio.normalized["place_id_cache"] is None and rio.normalized["google_place_id"] is None
    assert "weekday_text" not in rio.normalized
    # A steward judges the business match first: no description pass may re-reason it.
    assert agent.wants_description(rio) is False
    assert not mock_route.called


@pytest.mark.parametrize(
    ("primary", "accepted_id", "routing", "reason"),
    [
        # Steward accepted this id → enriched; re-score (mocked here) owns the routing.
        ("restaurant", "ChIJmatriz001", "dlq", "score=55.50 below threshold_mar=80.0"),
        ("restaurant", "ChIJother", "dlq", "google_tipo_negocio"),  # another id: gate stands
        ("shopping_mall", "ChIJmatriz001", "descarte", "nao_atrativo"),  # malls never pass
    ],
)
@pytest.mark.asyncio
async def test_steward_accepted_place_id_passes_the_business_gate(
    primary: str, accepted_id: str, routing: str, reason: str
) -> None:
    """Cachoeira Paracupeba is a restaurant on Google: once a steward accepts THAT id
    (aceitar-place), its Details data is written like any place's."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    details = {**_details(weekday_text=["Seg: 8h–17h"]), "primary_type": primary,
               "types": [primary]}
    fake = FakePlacesClient(fixture_details={"ChIJmatriz001": details})
    rio = _make_rio(extra_normalized={
        "category": "Cachoeiras", "place_id_cache": "ChIJmatriz001",
        "google_tipo_aceito": accepted_id})
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW,
                                  config=ScoreConfig())
    await _run(agent, rio)

    assert (rio.routing, rio.dlq_reason) == (routing, reason)
    if reason.startswith("score="):
        assert rio.normalized["google_place_id"] == "ChIJmatriz001"
        assert rio.normalized["weekday_text"] == ["Seg: 8h–17h"]


@pytest.mark.parametrize(
    ("category", "primary", "routing"),
    [
        ("Lojas especializadas e de suvenir", "store", "descarte"),  # TA itself says a shop
        ("Pontos de interesse", "shopping_mall", "descarte"),  # malls are never atrativos
        (None, "restaurant", "descarte"),  # no TA category → Google's verdict stands
    ],
)
@pytest.mark.asyncio
async def test_business_match_without_an_attraction_category_is_still_discarded(
    category: str | None, primary: str, routing: str
) -> None:
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    details = {**_details(), "primary_type": primary, "types": [primary]}
    fake = FakePlacesClient(
        fixture_results={"Igreja Matriz": [_search_result()]},
        fixture_details={"ChIJmatriz001": details},
    )
    rio = _make_rio(extra_normalized={"category": category})
    agent = PlacesEnrichmentAgent(places_client=fake, session=_make_session(), now=_NOW,
                                  config=ScoreConfig())
    await _run(agent, rio)
    assert (rio.routing, rio.dlq_reason) == (routing, "nao_atrativo")


def test_best_match_keeps_an_exact_name_non_business_over_a_place_typed_homonym() -> None:
    """2b made premise neutral: "Usina Hidrelétrica de Xingó" (premise, exact) must keep
    beating "Mirante da Usina…" (tourist_attraction), which token_set_ratio also scores 100;
    same for "Farol da Ponta Verde" vs the beach "Ponta Verde". Only a BUSINESS gives way."""
    from brave.domains.places.places_enrichment import _best_match

    for card, other in (("Usina Hidrelétrica de Xingó", "Mirante da Usina Hidrelétrica de Xingó"),
                        ("Farol da Ponta Verde", "Ponta Verde")):
        results = [
            _search_result(name=card, place_id="ChIJexact", types=["premise", "establishment"]),
            _search_result(name=other, place_id="ChIJother", types=["beach", "tourist_attraction"]),
        ]
        match = _best_match(results, card, _LAT, _LNG, 20.0, "Pontos de interesse & marcos")
        assert match is not None and match["place_id"] == "ChIJexact"


def test_best_match_without_coords_gives_way_only_inside_the_same_municipio() -> None:
    """locate() has no distance guard: a business in the card's município must not lose to a
    place-typed homonym in ANOTHER município of the UF (it would place the card there)."""
    from brave.domains.places.places_enrichment import _best_match

    agency = {**_search_result(name="Piscinas Naturais de Pajuçara", place_id="ChIJagency",
                               types=["tour_agency"]), "municipio_ibge": "2704302"}
    elsewhere = {**_search_result(name="Piscinas Naturais de Pajuçara Norte",
                                  place_id="ChIJfar"), "municipio_ibge": "2704708"}
    same = {**_search_result(name="Piscinas naturais de Maceió", place_id="ChIJmaceio"),
            "municipio_ibge": "2704302"}
    card, category = "Piscinas Naturais de Pajuçara", "Piscinas naturais"
    assert _best_match([agency, elsewhere], card, None, None, 20.0, category)["place_id"] == (
        "ChIJagency"
    )
    assert _best_match([agency, elsewhere, same], card, None, None, 20.0, category)[
        "place_id"
    ] == "ChIJmaceio"


@pytest.mark.asyncio
async def test_locate_majority_needs_the_closest_name_in_the_winner() -> None:
    """A Google that does not know "Museu Casa do Penedo" answers with popular museums of
    Maceió: 4 of 6 is a 2/3 majority, but the closest name sits in Penedo → no verdict."""
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    def res(name, ibge):
        return {**_search_result(name=name), "municipio_ibge": ibge}

    results = [res("Museu Théo Brandão", "2704302"), res("Museu de Arte Sacra", "2704302"),
               res("Museu Palácio Floriano Peixoto", "2704302"), res("Museu Pierre Chalita", "2704302"),
               res("Casa da Cultura de Penedo", "2706703"),
               res("Igreja do Penedo", "2706703")]
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Museu Casa do Penedo": results}),
        session=_make_session(),
        config=ScoreConfig(),
    )
    assert await agent.locate("Museu Casa do Penedo", "AL") is None


def _seats_session(*seats):
    session = _make_session()
    session.scalars.return_value.all.return_value = list(seats)
    return session


@pytest.mark.asyncio
async def test_locate_never_snaps_a_result_google_did_not_put_in_the_uf() -> None:
    """The client keeps a result with no state component (fail open): near a border it may
    be in PE/SE, so its coords must not snap it to an AL seat."""
    from brave.core.models import Municipio
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    no_uf = {**_search_result(name="Farol da Ponta Verde", lat=-9.6658, lng=-35.6950,
                              types=["premise"]), "municipio_ibge": "", "uf_short": ""}
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Farol - Ponta Verde": [no_uf]}),
        session=_seats_session(
            Municipio(ibge_code="2704302", nome="Maceió", uf="AL", lat=-9.6658, lng=-35.7350)
        ),
        config=ScoreConfig(),
    )
    assert await agent.locate("Farol - Ponta Verde", "AL") is None


@pytest.mark.asyncio
async def test_locate_rechecks_a_locality_municipio_against_the_coords() -> None:
    """locality (no level_2) named a distrito that shares another município's name: the
    lookup gave that far município's code; the coords put it back at the nearest seat."""
    from brave.core.models import Municipio
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    homonym = {**_search_result(name="Praia da Sereia", lat=-9.6658, lng=-35.6950),
               "municipio_ibge": "2708006", "municipio_from_locality": True, "uf_short": "AL"}
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(fixture_results={"Praia da Sereia": [homonym]}),
        session=_seats_session(
            Municipio(ibge_code="2704302", nome="Maceió", uf="AL", lat=-9.6658, lng=-35.7350),
            Municipio(ibge_code="2708006", nome="Santana do Ipanema", uf="AL", lat=-9.3780,
                      lng=-37.2450),
        ),
        config=ScoreConfig(),
    )
    match = await agent.locate("Praia da Sereia", "AL")
    assert match is not None and match["municipio_ibge"] == "2704302"


def test_description_filter_skips_business_review_records() -> None:
    """The batch / describe_uf eligibility mirrors wants_description for google_tipo_negocio."""
    from sqlalchemy.dialects import postgresql

    from brave.domains.places.copy_batch import description_candidates_filter

    sql = " ".join(
        str(c.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for c in description_candidates_filter()
    )
    assert "coalesce(rio_records.dlq_reason, '') != 'google_tipo_negocio'" in sql
