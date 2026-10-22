"""HybridPlacesClient + to_place_details — offline (FakeApifyClient + FakePlacesClient).

Fixtures are real responses for the same place (Morro do Moreno, Vila Velha/ES,
2026-10-21): the Apify item and the full Places (New) Details JSON, from which the
Essentials half is built.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from brave.clients.apify_places import normalize_apify_item
from brave.clients.hybrid_places import HybridPlacesClient, to_place_details
from brave.shared.opening_hours import to_hours_map
from tests.fakes.fake_apify import FakeApifyClient
from tests.fakes.fake_places import FakePlacesClient

_FIX = Path(__file__).resolve().parents[2] / "fixtures"
PID = "ChIJixdqrlIWuAARlfIafNUX-qk"
QUERY = "Morro do Moreno, Vila Velha - ES"  # Google's IDs-only search
Q = f"place_id:{PID}"  # Apify's
LAT, LNG = -20.3263, -40.2775  # the record's TA point, at the place
ITEM = {"nome": "Morro do Moreno", "municipio": "Vila Velha", "uf": "ES",
        "lat": LAT, "lng": LNG, "radius_km": 2.0}

# RealPlacesClient.place_details() keys (brave/clients/places.py).
DETAILS_KEYS = {
    "place_id", "name", "formatted_address", "international_phone_number", "website",
    "business_status", "weekday_text", "reviews", "location", "distrito_hint",
    "price_level", "types", "primary_type",
}
SEARCH_KEYS = {
    "place_id", "name", "formatted_address", "types", "location", "municipio_nome",
    "municipio_ibge", "uf_short", "municipio_from_locality", "distrito_hint",
}


def _apify() -> dict:
    return normalize_apify_item(json.loads((_FIX / "apify/apify_morro_do_moreno.json").read_text()))


def _essentials(uf_short: str = "ES") -> dict:
    """place_essentials() dict built from the real Places (New) Details JSON."""
    raw = json.loads((_FIX / "places/places_morro_do_moreno.json").read_text())
    return {
        "place_id": raw["id"],
        "formatted_address": raw["formattedAddress"],
        "types": raw["types"],
        "location": {"lat": raw["location"]["latitude"], "lng": raw["location"]["longitude"]},
        "municipio_nome": "Vila Velha",
        "municipio_ibge": "3205200",
        "uf_short": uf_short,
        "municipio_from_locality": False,
        "distrito_hint": "",
    }


def _hybrid(apify_fixture=None, essentials=None, ids=None, search_ids_error=None, **kw):
    apify = FakeApifyClient(apify_fixture if apify_fixture is not None else {Q: _apify()}, **kw)
    google = FakePlacesClient(
        fixture_essentials=essentials or {PID: _essentials()},
        fixture_ids={QUERY: [PID]} if ids is None else ids,
        search_ids_error=search_ids_error,
    )
    return HybridPlacesClient(google, apify, max_reviews=1), apify, google


def _search(hybrid, nome="Morro do Moreno", municipio="Vila Velha", uf="ES", **kw):
    kw = {"lat": LAT, "lng": LNG, "radius_km": 2.0, **kw}
    return asyncio.run(hybrid.search(nome, municipio, uf, **kw))


def _ess(place_id: str, *, km_north: float = 0.0, **over) -> dict:
    """Essentials for ``place_id`` ``km_north`` km north of the reference."""
    return {**_essentials(), "place_id": place_id,
            "location": {"lat": LAT + km_north / 111.2, "lng": LNG}, **over}


# -- adapter ---------------------------------------------------------------------------


def test_adapter_real_fixture_matches_place_details_shape():
    d = to_place_details(_apify(), _essentials(), include_reviews=True)

    assert set(d) == DETAILS_KEYS | {"provider"} and d["provider"] == "hybrid"
    assert d["place_id"] == PID and d["name"] == "Morro do Moreno"
    assert d["business_status"] == "OPERATIONAL"
    assert d["international_phone_number"] == "+55 27 99887-1444"
    assert d["types"] == ["tourist_attraction", "point_of_interest", "establishment"]
    assert d["primary_type"] == "" and d["price_level"] is None
    assert to_hours_map(d["weekday_text"]) == {"daily": "open_24h"}
    assert max(r["publishTime"] for r in d["reviews"]).startswith("2026-10-17")
    # LGPD: only date, rating, text survive.
    assert all(set(r) == {"publishTime", "rating", "text"} for r in d["reviews"])


def test_adapter_without_reviews_keeps_the_key_empty():
    assert to_place_details(_apify(), _essentials(), include_reviews=False)["reviews"] == []


@pytest.mark.parametrize(
    ("flags", "status"),
    [
        ({"permanentlyClosed": True, "temporarilyClosed": True}, "CLOSED_PERMANENTLY"),
        ({"permanentlyClosed": False, "temporarilyClosed": True}, "CLOSED_TEMPORARILY"),
        ({"permanentlyClosed": None, "temporarilyClosed": None}, "OPERATIONAL"),
    ],
)
def test_adapter_status_mapping(flags, status):
    item = {**_apify(), **flags}
    assert to_place_details(item, _essentials(), include_reviews=True)["business_status"] == status


_DAYS = ("quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo",
         "segunda-feira", "terça-feira")  # Apify starts TODAY


def _week(hours_by_day: dict[str, str], default: str) -> dict:
    return {**_apify(), "openingHours": [
        {"day": d, "hours": hours_by_day.get(d, default)} for d in _DAYS
    ]}


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        # Real Apify format (Parque do Ibitipoca, 2026-10-21): "to" ranges + "Fechado".
        (_week({"segunda-feira": "Fechado"}, "07:00 to 17:00"),
         {"mon": "closed", "tue_sun": "07:00-17:00"}),
        (_week({"domingo": "Fechado"}, "08:00–17:00"),
         {"mon_sat": "08:00-17:00", "sun": "closed"}),
        (_week({}, "08:00 to 12:00, 14:00 to 18:00"),
         {"daily": "08:00-12:00, 14:00-18:00"}),
    ],
)
def test_adapter_hours_formats_parse(item, expected):
    d = to_place_details(item, _essentials(), include_reviews=False)
    assert to_hours_map(d["weekday_text"]) == expected


def test_adapter_weekday_text_is_monday_first_whatever_the_scrape_day():
    """Apify starts on the scrape day: unsorted, a refresh on another weekday would change
    the stored lines (and supersede + re-push the Mar row) with identical hours."""
    d = to_place_details(_week({}, "08:00 to 17:00"), _essentials(), include_reviews=False)
    assert [line.split(":")[0] for line in d["weekday_text"]] == [
        "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira",
        "sábado", "domingo",
    ]


# -- prefetch --------------------------------------------------------------------------


def test_prefetch_is_one_run_at_max_reviews_and_caches():
    """One 4 GB run at a time (two at once fill the FREE plan's 8 GB → busy 402 for every
    other run); a no-reviews caller is served the same hit, reviews dropped."""
    hybrid, apify, google = _hybrid(
        {Q: _apify(), "place_id:P2": {**_apify(), "placeId": "P2"}},
        {PID: _essentials(), "P2": _ess("P2")},
    )
    items = [
        ITEM,
        dict(ITEM),  # dup
        {"place_id": "P2", "include_reviews": False},
        {"place_id": "P2", "include_reviews": True},  # twin record, other recency
    ]
    asyncio.run(hybrid.prefetch(items))

    assert apify.lookup_calls == [{"queries": [Q, "place_id:P2"], "max_reviews": 1}]
    assert len(google.search_ids_calls) == 1
    asyncio.run(hybrid.prefetch(items))  # already cached → no run, no pick
    assert len(apify.lookup_calls) == 1 and len(google.search_ids_calls) == 1
    assert asyncio.run(hybrid.place_details("P2", include_reviews=False))["reviews"] == []
    assert len(apify.lookup_calls) == 1


class _ChunkApify(FakeApifyClient):
    """Fails the run that carries ``bad``."""

    def __init__(self, fixture, bad):
        super().__init__(fixture)
        self._bad = bad

    async def lookup(self, queries, *, max_reviews):
        found = await super().lookup(queries, max_reviews=max_reviews)
        if self._bad in queries:
            raise RuntimeError("apify run TIMED-OUT")
        return found


def test_prefetch_splits_a_big_lot_and_a_failed_run_spares_the_others():
    """A 900 s Apify timeout fits ~50 places: a 200-place refresh in ONE run would time out
    every day. A failed run marks only its own queries."""
    from brave.clients.hybrid_places import APIFY_RUN_MAX_QUERIES

    n = APIFY_RUN_MAX_QUERIES

    pids = [f"P{i}" for i in range(2 * n + 5)]
    apify = _ChunkApify({f"place_id:{p}": {**_apify(), "placeId": p} for p in pids}, f"place_id:P{n}")
    essentials = {p: {**_essentials(), "place_id": p} for p in pids}
    hybrid = HybridPlacesClient(FakePlacesClient(fixture_essentials=essentials), apify)
    with pytest.raises(RuntimeError, match="TIMED-OUT"):
        asyncio.run(hybrid.prefetch([{"place_id": p} for p in pids]))

    assert [len(c["queries"]) for c in apify.lookup_calls] == [n, n, 5]
    assert asyncio.run(hybrid.place_details("P0"))["place_id"] == "P0"
    assert asyncio.run(hybrid.place_details(f"P{2 * n}"))["place_id"] == f"P{2 * n}"
    with pytest.raises(RuntimeError, match="batched run"):
        asyncio.run(hybrid.place_details(f"P{n}"))
    assert len(apify.lookup_calls) == 3


def test_prefetch_retries_misses_once_in_one_batch():
    """A miss in a SUCCEEDED run may be a dropped request: one more batched run for the
    misses; a second miss is a conclusive no-hit."""
    from brave.clients.hybrid_places import ApifyNoResultError

    hybrid, apify, google = _hybrid()
    asyncio.run(hybrid.prefetch([ITEM, {"place_id": "P2"}]))
    assert [c["queries"] for c in apify.lookup_calls] == [[Q, "place_id:P2"], ["place_id:P2"]]
    with pytest.raises(ApifyNoResultError):
        asyncio.run(hybrid.place_details("P2"))
    assert len(apify.lookup_calls) == 2
    assert [c["place_id"] for c in google.place_essentials_calls] == [PID]  # the pick only


def test_failed_prefetch_raises_per_record_instead_of_single_runs():
    hybrid, apify, google = _hybrid(raise_exc=RuntimeError("apify run FAILED"))
    with pytest.raises(RuntimeError):
        asyncio.run(hybrid.prefetch([{"place_id": "P2"}, ITEM]))
    for call in (lambda: asyncio.run(hybrid.place_details("P2")), lambda: _search(hybrid)):
        with pytest.raises(RuntimeError, match="batched run"):
            call()
    # One failed run, no single runs after; the only Essentials is the pick's.
    assert len(apify.lookup_calls) == 1 and len(google.place_essentials_calls) == 1


def test_a_wall_in_prefetch_does_not_mark_the_lot_failed():
    from brave.shared.exceptions import ProviderBalanceError

    hybrid, apify, _ = _hybrid(raise_exc=ProviderBalanceError("apify", "402"))
    with pytest.raises(ProviderBalanceError):
        asyncio.run(hybrid.prefetch([{"place_id": PID}]))
    with pytest.raises(ProviderBalanceError):  # the wall again, not "batched run failed"
        asyncio.run(hybrid.place_details(PID))


def test_failed_lot_blocks_single_runs_for_ids_it_never_carried():
    """Sweep: a card's id from locate()/consensus was never in the page's lot; on a degraded
    Apify it must fail like the lot, not pay a ~60 s+ run per card."""
    from brave.clients.hybrid_places import ApifyBatchFailedError

    hybrid, apify, google = _hybrid(
        essentials={PID: _essentials(), "P2": _ess("P2")}, raise_exc=RuntimeError("apify 500")
    )
    with pytest.raises(RuntimeError):
        asyncio.run(hybrid.prefetch([ITEM]))
    with pytest.raises(ApifyBatchFailedError):
        asyncio.run(hybrid.place_details("P2"))
    with pytest.raises(ApifyBatchFailedError):  # a live search, before any Google spend
        _search(hybrid, nome="Outro Lugar")
    assert len(apify.lookup_calls) == 1 and len(google.search_ids_calls) == 1

    hybrid._apify = FakeApifyClient({"place_id:P2": _apify()})  # Apify back: next lot clears it
    asyncio.run(hybrid.prefetch([]))
    assert asyncio.run(hybrid.place_details("P2"))["place_id"] == "P2"


def test_place_details_known_no_hit_raises_before_paying_essentials():
    from brave.clients.hybrid_places import ApifyNoResultError

    hybrid, apify, google = _hybrid({})
    asyncio.run(hybrid.prefetch([{"place_id": PID}]))
    with pytest.raises(ApifyNoResultError):
        asyncio.run(hybrid.place_details(PID))
    assert google.place_essentials_calls == [] and len(apify.lookup_calls) == 2


def test_prefetched_search_and_details_spend_nothing_more_on_apify():
    hybrid, apify, google = _hybrid()
    asyncio.run(hybrid.prefetch([ITEM]))

    [hit] = _search(hybrid)
    d = asyncio.run(hybrid.place_details(hit["place_id"]))

    assert len(apify.lookup_calls) == 1 and apify.check_budget_calls == []
    assert google.place_essentials_calls == [{"place_id": PID, "uf": "ES"}]  # the pick's one
    assert len(google.search_ids_calls) == 1
    assert d["provider"] == "hybrid" and d["reviews"]


def test_apify_is_asked_by_place_id_only():
    hybrid, apify, _ = _hybrid(essentials={PID: _essentials(), "P2": _ess("P2")},
                               ids={QUERY: [PID], "Outro Lugar, Vila Velha - ES": ["P2"]})
    asyncio.run(hybrid.prefetch([ITEM, {"place_id": "P3"}]))
    _search(hybrid, nome="Outro Lugar")  # live
    queries = [q for c in apify.lookup_calls for q in c["queries"]]
    assert queries and all(q.startswith("place_id:") for q in queries)


def test_duplicate_key_and_same_place_id_are_one_run_no_extra_lookup():
    """Two cards with the same key, and a third whose pick is the same place: all served
    from the one cached entry (read, never consumed)."""
    hybrid, apify, google = _hybrid(ids={QUERY: [PID], "Mirante do Moreno, Vila Velha - ES": [PID]})
    other = {**ITEM, "nome": "Mirante do Moreno"}
    asyncio.run(hybrid.prefetch([ITEM, {**ITEM, "nome": "MORRO DO  MORENO"}, other]))

    hits = [_search(hybrid), _search(hybrid, nome="Morro do Moreno"),
            _search(hybrid, nome="Mirante do Moreno")]
    assert [h[0]["place_id"] for h in hits] == [PID] * 3
    assert apify.lookup_calls == [{"queries": [Q], "max_reviews": 1}]
    assert len(google.search_ids_calls) == 2  # one pick per distinct key


def test_failed_pick_in_prefetch_raises_batch_failed_in_search():
    from brave.clients.hybrid_places import ApifyBatchFailedError

    hybrid, apify, google = _hybrid(search_ids_error=RuntimeError("places 503"))
    asyncio.run(hybrid.prefetch([ITEM]))  # swallowed: that record raises in search()
    with pytest.raises(ApifyBatchFailedError):
        _search(hybrid)
    assert len(google.search_ids_calls) == 1 and apify.lookup_calls == []


def test_wall_in_pick_propagates_from_prefetch():
    from brave.shared.exceptions import ProviderBalanceError

    hybrid, apify, _ = _hybrid(search_ids_error=ProviderBalanceError("places", "403"))
    with pytest.raises(ProviderBalanceError):
        asyncio.run(hybrid.prefetch([ITEM]))
    assert apify.lookup_calls == []


# -- pick ------------------------------------------------------------------------------


def test_pick_takes_the_first_candidate_in_the_uf_within_radius():
    essentials = {
        "POL": _ess("POL", types=["locality", "political"]),
        "MT": _ess("MT", uf_short="MT"),  # a TA point fell in Juína-MT
        PID: _ess(PID, km_north=1.0),
    }
    hybrid, _, google = _hybrid(essentials=essentials, ids={QUERY: ["POL", "MT", PID]})
    [hit] = _search(hybrid)
    assert hit["place_id"] == PID
    assert [c["place_id"] for c in google.place_essentials_calls] == ["POL", "MT", PID]


def test_pick_stops_at_the_first_passer():
    hybrid, _, google = _hybrid(essentials={PID: _essentials(), "P2": _ess("P2")},
                                ids={QUERY: [PID, "P2"]})
    [hit] = _search(hybrid)
    assert hit["place_id"] == PID and len(google.place_essentials_calls) == 1


def test_pick_looks_at_the_top_three_only():
    far = {f"F{i}": _ess(f"F{i}", km_north=10.0) for i in range(3)}
    hybrid, apify, google = _hybrid(essentials={**far, PID: _essentials()},
                                    ids={QUERY: ["F0", "F1", "F2", PID]})
    assert _search(hybrid) == []
    assert len(google.place_essentials_calls) == 3 and apify.lookup_calls == []


def test_pick_bias_is_5_km_around_a_point_and_50_km_max():
    hybrid, _, google = _hybrid(ids={})
    _search(hybrid, radius_km=2.0)
    _search(hybrid, nome="Outro", radius_km=80.0)
    assert [(c["lat"], c["lng"], c["radius_m"]) for c in google.search_ids_calls] == [
        (LAT, LNG, 5000.0), (LAT, LNG, 50000.0),
    ]
    assert google.search_ids_calls[0]["query"] == QUERY


# -- search ----------------------------------------------------------------------------


def test_search_hit_has_text_search_shape():
    hybrid, apify, _ = _hybrid()
    [hit] = _search(hybrid)

    assert set(hit) == SEARCH_KEYS
    assert hit["place_id"] == PID and hit["name"] == "Morro do Moreno"
    assert hit["municipio_ibge"] == "3205200"
    assert apify.lookup_calls == [{"queries": [Q], "max_reviews": 1}]


def test_search_no_hit_returns_empty_without_essentials():
    hybrid, apify, google = _hybrid(ids={})
    assert _search(hybrid) == []
    assert google.place_essentials_calls == [] and apify.lookup_calls == []


def test_search_out_of_uf_returns_empty():
    hybrid, apify, _ = _hybrid(essentials={PID: _essentials(uf_short="RJ")})
    assert _search(hybrid) == [] and apify.lookup_calls == []


def test_search_without_reviews_asks_zero():
    hybrid, apify, _ = _hybrid()
    _search(hybrid, include_reviews=False)
    assert apify.lookup_calls[0]["max_reviews"] == 0


def test_uncached_search_checks_both_budgets_before_paying():
    from brave.shared.exceptions import CostGuardError

    hybrid, apify, google = _hybrid()
    asked: list[float] = []

    def capped(usd):
        asked.append(usd)
        raise CostGuardError("places daily cap")

    google.check_budget = capped
    with pytest.raises(CostGuardError):
        _search(hybrid)
    assert asked == [pytest.approx(3 * 0.005)]  # every Essentials the pick may pay
    assert apify.check_budget_calls and apify.lookup_calls == []  # Apify never paid
    assert google.search_ids_calls == [] and google.place_essentials_calls == []


# -- place_details ---------------------------------------------------------------------


def test_uncached_place_details_runs_place_id_query():
    hybrid, apify, google = _hybrid({f"place_id:{PID}": _apify()})
    d = asyncio.run(hybrid.place_details(PID, include_reviews=False))

    assert apify.lookup_calls == [{"queries": [f"place_id:{PID}"], "max_reviews": 0}]
    assert google.place_essentials_calls == [{"place_id": PID, "uf": ""}]
    assert d["reviews"] == [] and set(d) == DETAILS_KEYS | {"provider"}


def test_place_details_apify_no_hit_raises_after_one_more_run():
    """A single-run miss is conclusive for the agent, and may be a request the crawler gave
    up on: one more run first, as prefetch does."""
    hybrid, apify, _ = _hybrid({})
    with pytest.raises(RuntimeError, match="no result"):
        asyncio.run(hybrid.place_details(PID))
    assert len(apify.lookup_calls) == 2


def test_place_details_cache_is_consumed_once():
    hybrid, apify, _ = _hybrid()
    _search(hybrid)
    asyncio.run(hybrid.place_details(PID))
    asyncio.run(hybrid.place_details(PID))  # pair popped → pays a place_id run
    assert [c["queries"] for c in apify.lookup_calls] == [[Q], [Q]]


def test_text_search_delegates_to_google_and_no_check_budget():
    google = FakePlacesClient(fixture_results={"praias": [{"place_id": "x"}]})
    hybrid = HybridPlacesClient(google, FakeApifyClient())
    assert asyncio.run(hybrid.text_search("praias", "BA")) == [{"place_id": "x"}]
    assert google.text_search_calls == [{"query": "praias", "uf": "BA"}]
    # run() probes check_budget with GOOGLE prices — the hybrid must not expose one.
    assert not hasattr(hybrid, "check_budget")
