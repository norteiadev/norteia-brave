"""PlacesEnrichmentAgent over the REAL HybridPlacesClient (places_provider="hybrid").

Offline: FakeApifyClient + FakePlacesClient(fixture_essentials), the real Morro do Moreno
fixtures (Apify item + Places (New) Details, Vila Velha/ES, 2026-10-21). Google's pick is the
free IDs-only search (fixture_ids) + Essentials; the paid Text Search is never called on this
path, and the agent's _best_match checks Apify's title against the record's name.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from brave.clients.apify_places import normalize_apify_item
from brave.clients.hybrid_places import HybridPlacesClient
from brave.config.settings import ScoreConfig
from brave.shared.exceptions import ProviderBalanceError
from tests.fakes.fake_apify import FakeApifyClient
from tests.fakes.fake_places import FakePlacesClient
from tests.unit.domains.places.test_places_enrichment import _make_rio, _make_session, _run

_FIX = Path(__file__).resolve().parents[3] / "fixtures"
_NOW = datetime(2026, 10, 21, tzinfo=UTC)
PID = "ChIJixdqrlIWuAARlfIafNUX-qk"
QUERY = "Morro do Moreno, Vila Velha - ES"  # Google's IDs-only search
Q = f"place_id:{PID}"  # Apify's
_TA_LAT, _TA_LNG = -20.327, -40.278  # TA's own point, ~150 m from Google's


def _apify(**over) -> dict:
    item = normalize_apify_item(json.loads((_FIX / "apify/apify_morro_do_moreno.json").read_text()))
    return {**item, **over}


def _essentials(**over) -> dict:
    raw = json.loads((_FIX / "places/places_morro_do_moreno.json").read_text())
    return {
        "place_id": raw["id"],
        "formatted_address": raw["formattedAddress"],
        "types": raw["types"],
        "location": {"lat": raw["location"]["latitude"], "lng": raw["location"]["longitude"]},
        "municipio_nome": "Vila Velha",
        "municipio_ibge": "3205200",
        "uf_short": "ES",
        "municipio_from_locality": False,
        "distrito_hint": "",
        **over,
    }


def _rio(**normalized):
    rio = _make_rio(extra_normalized={
        "name": "Morro do Moreno", "category": "Pontos de interesse e marcos",
        "municipio": "Vila Velha", "municipio_id": "3205200",
        "lat": _TA_LAT, "lon": _TA_LNG, **normalized,
    })
    rio.uf = "ES"
    return rio


def _agent(apify_fixture=None, essentials=None, ids=None, more_essentials=None, **apify_kw):
    apify = FakeApifyClient(apify_fixture if apify_fixture is not None else {Q: _apify()},
                            **apify_kw)
    google = FakePlacesClient(
        fixture_essentials={PID: essentials or _essentials(), **(more_essentials or {})},
        fixture_ids=ids or {QUERY: [PID]},
    )
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    agent = PlacesEnrichmentAgent(
        places_client=HybridPlacesClient(google, apify, max_reviews=1),
        session=_make_session(), now=_NOW, config=ScoreConfig(),
    )
    return agent, apify, google


# -- gate ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("types", "expected"),
    [
        (["restaurant", "food", "point_of_interest", "establishment"], True),  # Faraó na Praia
        (["pet_care", "park", "point_of_interest", "establishment"], False),  # Parque dos Falcões
        (["shopping_mall", "park", "point_of_interest"], True),  # malls always out
        (["tourist_attraction", "point_of_interest", "establishment"], False),
    ],
)
def test_hybrid_gate_judges_on_essentials_types(types, expected) -> None:
    from brave.domains.places.places_enrichment import _google_not_atrativo

    assert _google_not_atrativo({"provider": "hybrid", "primary_type": "", "types": types}) is expected


def test_google_details_without_primary_type_are_still_not_judged() -> None:
    from brave.domains.places.places_enrichment import _google_not_atrativo

    assert _google_not_atrativo({"primary_type": "", "types": ["shopping_mall", "restaurant"]}) is False


@pytest.mark.parametrize(
    ("name", "category", "types", "routing", "reason"),
    [
        ("Faraó na Praia", "Praias", ["restaurant", "food", "point_of_interest"],
         "dlq", "google_tipo_negocio"),
        ("Shopping Vila Velha", "Pontos de interesse e marcos",
         ["shopping_mall", "point_of_interest", "establishment"], "descarte", "nao_atrativo"),
    ],
)
@pytest.mark.asyncio
async def test_hybrid_business_and_mall_matches(name, category, types, routing, reason) -> None:
    query = f"{name}, Vila Velha - ES"
    agent, _, _ = _agent({Q: _apify(title=name)}, _essentials(types=types), ids={query: [PID]})
    rio = _rio(name=name, category=category)
    await _run(agent, rio)
    assert (rio.routing, rio.dlq_reason) == (routing, reason)
    assert rio.normalized.get("google_place_id") is None


@pytest.mark.asyncio
async def test_hybrid_pet_care_park_is_kept() -> None:
    name = "Parque dos Falcões"
    agent, _, _ = _agent({Q: _apify(title=name)},
                         _essentials(types=["pet_care", "park", "point_of_interest"]),
                         ids={f"{name}, Vila Velha - ES": [PID]})
    rio = _rio(name=name, category="Parques")
    await _run(agent, rio)
    assert rio.normalized["google_place_id"] == PID and rio.routing == "dlq"


# -- run() flow ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_search_match_enriches_from_apify_and_essentials() -> None:
    agent, apify, google = _agent()
    rio = _rio()
    await _run(agent, rio)

    n = rio.normalized
    assert n["google_place_id"] == n["place_id_cache"] == PID
    assert n["google_enriched"] is True
    assert n["most_recent_review_at"].startswith("2026-10-17")  # Apify's newest review
    assert n["atualidade_value"] == 100.0
    assert n["phone"] == "+55 27 99887-1444"
    assert n["google_types"] == ["tourist_attraction", "point_of_interest", "establishment"]
    assert n["weekday_text"] and n["signal"]["business_status"] == "OPERATIONAL"
    assert apify.lookup_calls == [{"queries": [Q], "max_reviews": 1}]
    assert google.text_search_calls == []  # no Text Search Pro on the hybrid path
    assert [c["query"] for c in google.search_ids_calls] == [QUERY]  # the free one
    assert google.place_essentials_calls == [{"place_id": PID, "uf": "ES"}]


@pytest.mark.asyncio
async def test_hybrid_cached_place_id_runs_a_place_id_query() -> None:
    agent, apify, google = _agent()
    rio = _rio(place_id_cache=PID)
    await _run(agent, rio)

    assert rio.normalized["weekday_text"]
    assert apify.lookup_calls == [{"queries": [f"place_id:{PID}"], "max_reviews": 1}]
    assert google.place_essentials_calls == [{"place_id": PID, "uf": ""}]
    assert google.text_search_calls == []


@pytest.mark.parametrize(
    "essentials",
    [
        _essentials(location={"lat": -23.55, "lng": -46.63}),  # same name, São Paulo
        _essentials(uf_short="RJ"),  # Essentials puts it in another UF
    ],
    ids=["too-far", "other-uf"],
)
@pytest.mark.asyncio
async def test_hybrid_no_pick_is_a_conclusive_no_match(essentials) -> None:
    agent, apify, google = _agent(essentials=essentials)
    rio = _rio()
    await _run(agent, rio)

    assert rio.normalized["google_enriched"] is True
    assert "place_id_cache" not in rio.normalized and "weekday_text" not in rio.normalized
    assert apify.lookup_calls == [] and google.text_search_calls == []  # Apify never paid


@pytest.mark.asyncio
async def test_hybrid_apify_no_hit_is_a_conclusive_no_match() -> None:
    agent, _, google = _agent({})
    rio = _rio()
    await _run(agent, rio)
    assert rio.normalized["google_enriched"] is True
    assert len(google.place_essentials_calls) == 1  # the pick's; no Details after
    assert google.text_search_calls == []


@pytest.mark.asyncio
async def test_hybrid_apify_failure_keeps_the_ta_floor_unmarked() -> None:
    agent, _, _ = _agent(raise_exc=RuntimeError("apify run FAILED"))
    rio = _rio()
    await _run(agent, rio)
    assert "google_enriched" not in rio.normalized and "weekday_text" not in rio.normalized


@pytest.mark.asyncio
async def test_hybrid_apify_balance_wall_propagates() -> None:
    agent, _, _ = _agent(raise_exc=ProviderBalanceError("apify", "Apify HTTP 402"))
    with pytest.raises(ProviderBalanceError):
        await _run(agent, _rio())


@pytest.mark.parametrize(
    ("category", "routing", "reason"),
    [
        ("Pontos de interesse e marcos", "descarte", "closed_place"),
        ("Montanhas", "dlq", "google_fechado_revisao"),  # a natural place never closes
    ],
)
@pytest.mark.asyncio
async def test_hybrid_permanently_closed(category, routing, reason) -> None:
    agent, _, _ = _agent({Q: _apify(permanentlyClosed=True)})
    rio = _rio(category=category)
    await _run(agent, rio)
    assert (rio.routing, rio.dlq_reason) == (routing, reason)


@pytest.mark.asyncio
async def test_hybrid_temporarily_closed_goes_to_dlq() -> None:
    agent, _, _ = _agent({Q: _apify(temporarilyClosed=True)})
    rio = _rio()
    await _run(agent, rio)
    assert (rio.routing, rio.dlq_reason) == ("dlq", "closed_temporarily")


@pytest.mark.asyncio
async def test_hybrid_recent_ta_review_asks_apify_for_no_reviews() -> None:
    ta_date = "2026-09-30T00:00:00+00:00"
    agent, apify, _ = _agent()
    rio = _rio(atualidade_value=100.0, most_recent_review_at=ta_date)
    await _run(agent, rio)
    assert apify.lookup_calls[0]["max_reviews"] == 0
    assert rio.normalized["most_recent_review_at"] == ta_date  # no Google date taken


# -- prefetch --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefetch_one_run_for_the_lot_then_runs_spend_nothing_on_apify() -> None:
    other = "Convento da Penha, Vila Velha - ES"
    agent, apify, _ = _agent(
        {Q: _apify(), "place_id:P_CONV": None},
        more_essentials={"P_CONV": _essentials(place_id="P_CONV")},
        ids={QUERY: [PID], other: ["P_CONV"]},
    )
    by_name, by_id, missing = _rio(), _rio(place_id_cache=PID), _rio(name="Convento da Penha")
    # Own names: skipped records must stay out of the run.
    done = _rio(name="Já Feito", google_enriched=True)
    unplaced = _rio(name="Fora da UF", ta_uf="RJ")

    await agent.prefetch(agent.prefetch_items([by_name, by_id, missing, done, unplaced]))
    lot, retry = apify.lookup_calls
    assert lot["queries"] == [Q, "place_id:P_CONV"]  # by_name's pick is by_id's place
    # A miss may be a request the crawler dropped: ONE more batched run, misses only.
    assert retry["queries"] == ["place_id:P_CONV"]

    for rio in (by_name, by_id, missing):
        await _run(agent, rio)
    assert len(apify.lookup_calls) == 2 and apify.check_budget_calls == []
    assert by_name.normalized["google_place_id"] == by_id.normalized["google_place_id"] == PID
    assert missing.normalized["google_enriched"] is True  # missed twice: conclusive


@pytest.mark.asyncio
async def test_prefetch_items_then_run_spend_nothing_more() -> None:
    """The sweep's page lot: items built from the placed rows, one run, then run() per row
    reads the cache — no second pick, no Apify run, no budget probe."""
    agent, apify, google = _agent()
    rio = _rio()
    items = agent.prefetch_items([rio])
    assert items == [{
        "nome": "Morro do Moreno", "municipio": "Vila Velha", "uf": "ES",
        "lat": _TA_LAT, "lng": _TA_LNG, "radius_km": 2.0, "include_reviews": True,
    }]
    await agent.prefetch(items)
    await _run(agent, rio)

    assert apify.lookup_calls == [{"queries": [Q], "max_reviews": 1}]
    assert apify.check_budget_calls == [] and len(google.search_ids_calls) == 1
    assert rio.normalized["google_place_id"] == PID


@pytest.mark.asyncio
async def test_failed_lot_run_keeps_each_record_unmarked_without_a_run_of_its_own() -> None:
    """refresh_places: a failed batched run must not turn into ~60 s single runs per record
    (200 of them blow the time limit) — each record keeps the TA floor, unmarked."""
    agent, apify, google = _agent(raise_exc=RuntimeError("apify run FAILED"))
    rios = [_rio(place_id_cache=PID), _rio()]
    await agent.prefetch(agent.prefetch_items(rios))
    for rio in rios:
        await _run(agent, rio)

    assert len(apify.lookup_calls) == 1, "no single-query fallback runs"
    assert len(google.place_essentials_calls) == 1, "the pick's; no Details paid after"
    assert all("google_enriched" not in r.normalized for r in rios)


@pytest.mark.asyncio
async def test_refresh_after_a_failed_lot_raises_so_the_record_stays_due() -> None:
    """refresh_places: nothing was asked for the record, so it must not be stamped
    places_refreshed_at (skipped 60-90 days with no data); the raise rolls it back."""
    from unittest.mock import patch

    from brave.clients.hybrid_places import ApifyBatchFailedError

    agent, apify, google = _agent(raise_exc=RuntimeError("apify run FAILED"))
    rio = _rio(place_id_cache=PID, places_refreshed_at="2026-01-01T00:00:00+00:00")
    await agent.prefetch(agent.prefetch_items([rio], refresh=True))
    with patch("brave.domains.places.places_enrichment.write_audit"), \
         patch("brave.domains.places.places_enrichment.record_event"), \
         patch("brave.domains.places.places_enrichment.route_by_score"), \
         pytest.raises(ApifyBatchFailedError):
        await agent.run(rio, refresh=True)
    assert rio.normalized["places_refreshed_at"] == "2026-01-01T00:00:00+00:00"
    assert len(apify.lookup_calls) == 1 and google.place_essentials_calls == []


@pytest.mark.asyncio
async def test_apify_no_hit_on_a_known_id_is_conclusive() -> None:
    """Apify cannot open the listing (after the lot's retry): marked consulted, so the
    backfill stops re-selecting (and re-paying) it; no Essentials paid for it."""
    agent, apify, google = _agent({})
    rio = _rio(place_id_cache=PID)
    await agent.prefetch(agent.prefetch_items([rio]))
    await _run(agent, rio)

    assert len(apify.lookup_calls) == 2  # the lot + the retry of its miss
    assert google.place_essentials_calls == []
    assert rio.normalized["google_enriched"] is True and "weekday_text" not in rio.normalized


@pytest.mark.asyncio
async def test_google_retired_id_is_cleared_before_any_apify_run() -> None:
    agent, apify, _ = _agent()
    rio = _rio(place_id_cache="ChIJ-retired")
    await _run(agent, rio)

    assert rio.normalized["place_id_cache"] is None and rio.normalized["google_place_id"] is None
    assert apify.lookup_calls == [], "Essentials' NotFound comes first"


@pytest.mark.asyncio
async def test_hybrid_keeps_the_last_recent_review_count() -> None:
    """Apify brings apify_max_reviews (1) per place: counting that sample would cap
    reviews_recent_count at 1 — the last known count stands instead."""
    agent, _, _ = _agent()
    rio = _rio(signal={"reviews_recent_count": 7})
    await _run(agent, rio)
    assert rio.normalized["signal"]["reviews_recent_count"] == 7


@pytest.mark.asyncio
async def test_prefetch_swallows_a_failure_but_not_a_balance_wall() -> None:
    agent, _, _ = _agent(raise_exc=RuntimeError("apify 500"))
    await agent.prefetch(agent.prefetch_items([_rio()]))  # logged; run() raises per record

    agent, _, _ = _agent(raise_exc=ProviderBalanceError("apify"))
    with pytest.raises(ProviderBalanceError):
        await agent.prefetch(agent.prefetch_items([_rio()]))


@pytest.mark.asyncio
async def test_prefetch_is_a_noop_in_google_mode() -> None:
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    google = FakePlacesClient()
    agent = PlacesEnrichmentAgent(places_client=google, session=_make_session(), config=ScoreConfig())
    assert agent.prefetches is False
    assert agent.prefetch_items([_rio()]) == []
    await agent.prefetch([])
    assert google.text_search_calls == google.place_essentials_calls == []


@pytest.mark.asyncio
async def test_apify_title_failing_the_name_check_is_a_conclusive_no_match() -> None:
    """Google's pick is near and in the UF, but Apify names it "SESC Academia": not the
    "Teatro do SESC" — no match, and no second search."""
    agent, apify, google = _agent(
        {Q: _apify(title="SESC Academia")}, ids={"Teatro do SESC, Vila Velha - ES": [PID]}
    )
    rio = _rio(name="Teatro do SESC")
    await _run(agent, rio)

    assert rio.normalized["google_enriched"] is True
    assert "place_id_cache" not in rio.normalized
    assert [c["queries"] for c in apify.lookup_calls] == [[Q]]
    assert len(google.search_ids_calls) == 1


def _spy_search(agent) -> list[dict]:
    seen: list[dict] = []
    real = agent._places_client.search

    async def spy(*args, **kw):
        seen.append(kw)
        return await real(*args, **kw)

    agent._places_client.search = spy
    return seen


@pytest.mark.asyncio
async def test_run_searches_around_the_record_point_with_the_pick_radius() -> None:
    agent, _, _ = _agent()
    seen = _spy_search(agent)
    rio = _rio()
    await _run(agent, rio)

    assert (seen[0]["lat"], seen[0]["lng"], seen[0]["radius_km"]) == (_TA_LAT, _TA_LNG, 2.0)
    assert rio.normalized["google_place_id"] == PID


@pytest.mark.asyncio
async def test_run_without_a_point_searches_around_the_seat_at_the_seat_radius() -> None:
    from brave.core.models import Municipio

    agent, _, google = _agent()
    agent._session.get.return_value = Municipio(
        ibge_code="3205200", nome="Vila Velha", uf="ES", lat=-20.3297, lng=-40.2925
    )
    seen = _spy_search(agent)
    rio = _rio(lat=None, lon=None)
    await _run(agent, rio)

    assert (seen[0]["lat"], seen[0]["lng"], seen[0]["radius_km"]) == (-20.3297, -40.2925, 80.0)
    assert google.search_ids_calls[0]["radius_m"] == 50_000.0  # Google's circle cap
    assert rio.normalized["google_place_id"] == PID


@pytest.mark.asyncio
async def test_run_without_any_reference_does_not_search_or_mark() -> None:
    """No point, no seat: nothing to pick around — no answer, so no marker (retried later)."""
    agent, apify, google = _agent()
    rio = _rio(lat=None, lon=None, municipio_id="")
    await _run(agent, rio)

    assert google.search_ids_calls == [] and apify.lookup_calls == []
    assert rio.normalized.get("google_enriched") is not True


def test_prefetch_item_without_any_reference_is_skipped() -> None:
    agent, _, _ = _agent()
    assert agent.prefetch_items([_rio(lat=None, lon=None, municipio_id="")]) == []


# -- steward path (aceitar-place → enrich_places_task → _enrich_one) -------------------


def test_steward_reenrich_uses_the_hybrid_from_the_clients_bag(monkeypatch) -> None:
    """aceitar-place re-runs enrich_places_task, which builds clients.places: in hybrid mode
    that is the HybridPlacesClient, and the accepted business id passes the gate."""
    from brave.config.settings import AppConfig
    from brave.tasks import pipeline

    apify = FakeApifyClient({f"place_id:{PID}": _apify()})
    google = FakePlacesClient(fixture_essentials={PID: _essentials(types=["restaurant"])})

    class Bag:
        places = HybridPlacesClient(google, apify)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(pipeline, "clients_for", lambda *a, **k: Bag())
    for name in ("write_audit", "record_event", "route_by_score"):
        monkeypatch.setattr(f"brave.domains.places.places_enrichment.{name}", MagicMock())
    ctx = SimpleNamespace(effective=AppConfig(places_enrichment_enabled=True),
                          ibge_lookup=None, distritos=[])
    rio = _rio(category="Cachoeiras", place_id_cache=PID, google_tipo_aceito=PID,
               google_enriched=False)
    rio.id = uuid.uuid4()

    pipeline._enrich_one(_make_session(), rio, ctx)

    assert apify.lookup_calls == [{"queries": [f"place_id:{PID}"], "max_reviews": 1}]
    assert rio.normalized["google_place_id"] == PID and rio.normalized["weekday_text"]
