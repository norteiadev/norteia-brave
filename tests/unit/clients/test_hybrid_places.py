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
QUERY = "Morro do Moreno, Vila Velha - ES"

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


def _hybrid(apify_fixture=None, essentials=None, **kw):
    apify = FakeApifyClient(apify_fixture if apify_fixture is not None else {QUERY: _apify()}, **kw)
    google = FakePlacesClient(fixture_essentials={PID: essentials or _essentials()})
    return HybridPlacesClient(google, apify, max_reviews=1), apify, google


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
    hybrid, apify, _ = _hybrid({QUERY: _apify(), f"place_id:{PID}": _apify()})
    items = [
        {"nome": "Morro do Moreno", "municipio": "Vila Velha", "uf": "ES"},
        {"nome": "Morro do Moreno", "municipio": "Vila Velha", "uf": "ES"},  # dup
        {"place_id": PID, "include_reviews": False},
        {"place_id": PID, "include_reviews": True},  # twin record, other recency
    ]
    asyncio.run(hybrid.prefetch(items))

    assert apify.lookup_calls == [{"queries": [QUERY, f"place_id:{PID}"], "max_reviews": 1}]
    asyncio.run(hybrid.prefetch(items))  # already cached → no run
    assert len(apify.lookup_calls) == 1
    assert asyncio.run(hybrid.place_details(PID, include_reviews=False))["reviews"] == []
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


def test_prefetch_retries_exact_misses_once_in_one_batch():
    """A miss in a SUCCEEDED run may be a dropped request: the exact queries (place_id /
    with município) get one more batched run; município-less sweep names do not."""
    hybrid, apify, _ = _hybrid({f"place_id:{PID}": _apify()})
    asyncio.run(hybrid.prefetch([
        {"place_id": PID},
        {"nome": "Lugar X", "municipio": "Vila Velha", "uf": "ES"},
        {"nome": "Card Y", "municipio": "", "uf": "ES"},
    ]))
    assert [c["queries"] for c in apify.lookup_calls] == [
        [f"place_id:{PID}", "Lugar X, Vila Velha - ES", "Card Y - ES"],
        ["Lugar X, Vila Velha - ES"],
    ]
    # Missed twice: a conclusive no-hit, no third run.
    assert asyncio.run(hybrid.search("Lugar X", "Vila Velha", "ES")) == []
    assert len(apify.lookup_calls) == 2


def test_failed_prefetch_raises_per_record_instead_of_single_runs():
    hybrid, apify, google = _hybrid(raise_exc=RuntimeError("apify run FAILED"))
    with pytest.raises(RuntimeError):
        asyncio.run(hybrid.prefetch([
            {"place_id": PID}, {"nome": "Morro do Moreno", "municipio": "Vila Velha", "uf": "ES"},
            {"nome": "Card Y", "municipio": "", "uf": "ES"},
        ]))
    for call in (
        hybrid.place_details(PID),
        hybrid.search("Morro do Moreno", "Vila Velha", "ES"),
        hybrid.search("Card Y", "Serra", "ES"),  # via the sweep alias
    ):
        with pytest.raises(RuntimeError, match="batched run"):
            asyncio.run(call)
    assert len(apify.lookup_calls) == 1 and google.place_essentials_calls == []


def test_a_wall_in_prefetch_does_not_mark_the_lot_failed():
    from brave.shared.exceptions import ProviderBalanceError

    hybrid, apify, _ = _hybrid(raise_exc=ProviderBalanceError("apify", "402"))
    with pytest.raises(ProviderBalanceError):
        asyncio.run(hybrid.prefetch([{"place_id": PID}]))
    with pytest.raises(ProviderBalanceError):  # the wall again, not "batched run failed"
        asyncio.run(hybrid.place_details(PID))


def test_record_exact_hit_wins_over_a_homonym_alias():
    """Two 'Igreja Matriz' of one lot: each record takes its OWN cached hit, even when the
    other's Apify address names its município (Rua Cachoeira, Lençóis)."""
    cach = {**_apify(), "placeId": "P_CACH", "city": "Cachoeira", "address": "Centro, Cachoeira - BA"}
    lenc = {**_apify(), "placeId": "P_LENC", "city": "Lençóis",
            "address": "Rua Cachoeira, 10, Lençóis - BA"}
    q_cach, q_lenc = "Igreja Matriz, Cachoeira - BA", "Igreja Matriz, Lençóis - BA"
    apify = FakeApifyClient({q_cach: cach, q_lenc: lenc})
    essentials = {pid: {**_essentials(uf_short="BA"), "place_id": pid} for pid in ("P_CACH", "P_LENC")}
    hybrid = HybridPlacesClient(FakePlacesClient(fixture_essentials=essentials), apify)
    asyncio.run(hybrid.prefetch([
        {"nome": "Igreja Matriz", "municipio": "Cachoeira", "uf": "BA"},
        {"nome": "Igreja Matriz", "municipio": "Lençóis", "uf": "BA"},
    ]))

    [hit_c] = asyncio.run(hybrid.search("Igreja Matriz", "Cachoeira", "BA"))
    [hit_l] = asyncio.run(hybrid.search("Igreja Matriz", "Lençóis", "BA"))
    assert (hit_c["place_id"], hit_l["place_id"]) == ("P_CACH", "P_LENC")
    assert len(apify.lookup_calls) == 1


def test_sweep_alias_matches_apify_city_not_an_address_substring():
    lenc = {**_apify(), "city": "Lençóis", "address": "Rua Cachoeira, 10, Lençóis - BA"}
    apify = FakeApifyClient({"Igreja Matriz - BA": lenc})
    hybrid = HybridPlacesClient(FakePlacesClient(), apify)
    asyncio.run(hybrid.prefetch([{"nome": "Igreja Matriz", "municipio": "", "uf": "BA"}]))
    asyncio.run(hybrid.search("Igreja Matriz", "Cachoeira", "BA"))
    assert [c["queries"] for c in apify.lookup_calls] == [
        ["Igreja Matriz - BA"],
        ["Igreja Matriz, Cachoeira - BA"], ["Igreja Matriz, Cachoeira - BA"],  # miss: once more
    ], "the Lençóis hit is not Cachoeira's"


def test_record_moved_by_the_ta_is_served_by_its_prefetched_name():
    """Backfill: prefetched as 'Nome, A - UF' (the geocoder's município), then the TA moves
    the record to B: Apify's hit sits in B → served, no single run of its own."""
    hybrid, apify, _ = _hybrid({"Morro do Moreno, Serra - ES": _apify()})  # city Vila Velha
    asyncio.run(hybrid.prefetch([{"nome": "Morro do Moreno", "municipio": "Serra", "uf": "ES"}]))
    [hit] = asyncio.run(hybrid.search("Morro do Moreno", "Vila Velha", "ES"))
    assert hit["place_id"] == PID and len(apify.lookup_calls) == 1


def test_failed_lot_blocks_single_runs_for_ids_it_never_carried():
    """Sweep: a card's id from locate()/consensus was never in the page's lot; on a degraded
    Apify it must fail like the lot, not pay a ~60 s+ run per card."""
    from brave.clients.hybrid_places import ApifyBatchFailedError

    hybrid, apify, _ = _hybrid(raise_exc=RuntimeError("apify 500"))
    with pytest.raises(RuntimeError):
        asyncio.run(hybrid.prefetch([{"nome": "Card 0", "municipio": "", "uf": "ES"}]))
    with pytest.raises(ApifyBatchFailedError):
        asyncio.run(hybrid.place_details(PID))
    assert len(apify.lookup_calls) == 1

    hybrid._apify = FakeApifyClient({f"place_id:{PID}": _apify()})  # Apify back: next lot clears it
    asyncio.run(hybrid.prefetch([]))
    assert asyncio.run(hybrid.place_details(PID))["place_id"] == PID


def test_place_details_known_no_hit_raises_before_paying_essentials():
    from brave.clients.hybrid_places import ApifyNoResultError

    hybrid, apify, google = _hybrid({})
    asyncio.run(hybrid.prefetch([{"place_id": PID}]))
    with pytest.raises(ApifyNoResultError):
        asyncio.run(hybrid.place_details(PID))
    assert google.place_essentials_calls == [] and len(apify.lookup_calls) == 2


def test_prefetched_search_and_details_spend_nothing_more_on_apify():
    hybrid, apify, google = _hybrid()
    asyncio.run(hybrid.prefetch([{"nome": "Morro do Moreno", "municipio": "Vila Velha", "uf": "ES"}]))

    [hit] = asyncio.run(hybrid.search("Morro do Moreno", "Vila Velha", "ES"))
    d = asyncio.run(hybrid.place_details(hit["place_id"]))

    assert len(apify.lookup_calls) == 1 and apify.check_budget_calls == []
    assert google.place_essentials_calls == [{"place_id": PID, "uf": "ES"}]  # one Essentials
    assert d["provider"] == "hybrid" and d["reviews"]


# -- search ----------------------------------------------------------------------------


def test_search_hit_has_text_search_shape():
    hybrid, apify, _ = _hybrid()
    [hit] = asyncio.run(hybrid.search("Morro do Moreno", "Vila Velha", "ES"))

    assert set(hit) == SEARCH_KEYS
    assert hit["place_id"] == PID and hit["name"] == "Morro do Moreno"
    assert hit["municipio_ibge"] == "3205200"
    assert apify.lookup_calls == [{"queries": [QUERY], "max_reviews": 1}]


def test_search_no_hit_returns_empty_without_essentials():
    hybrid, _, google = _hybrid({})
    assert asyncio.run(hybrid.search("Morro do Moreno", "Vila Velha", "ES")) == []
    assert google.place_essentials_calls == []


def test_search_out_of_uf_returns_empty():
    hybrid, _, _ = _hybrid(essentials=_essentials(uf_short="RJ"))
    assert asyncio.run(hybrid.search("Morro do Moreno", "Vila Velha", "ES")) == []


def test_search_without_reviews_asks_zero():
    hybrid, apify, _ = _hybrid()
    asyncio.run(hybrid.search("Morro do Moreno", "Vila Velha", "ES", include_reviews=False))
    assert apify.lookup_calls[0]["max_reviews"] == 0


def test_uncached_search_checks_both_budgets_before_paying():
    from brave.shared.exceptions import CostGuardError

    hybrid, apify, google = _hybrid()

    def capped(usd):
        raise CostGuardError("places daily cap")

    google.check_budget = capped
    with pytest.raises(CostGuardError):
        asyncio.run(hybrid.search("Morro do Moreno", "Vila Velha", "ES"))
    assert apify.check_budget_calls and apify.lookup_calls == []  # Apify never paid


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
    asyncio.run(hybrid.search("Card Y", "", "ES"))  # município-less: not exact, one run
    assert len(apify.lookup_calls) == 3


def test_place_details_cache_is_consumed_once():
    hybrid, apify, _ = _hybrid({QUERY: _apify(), f"place_id:{PID}": _apify()})
    asyncio.run(hybrid.search("Morro do Moreno", "Vila Velha", "ES"))
    asyncio.run(hybrid.place_details(PID))
    asyncio.run(hybrid.place_details(PID))  # cache popped → pays a place_id run
    assert [c["queries"] for c in apify.lookup_calls] == [[QUERY], [f"place_id:{PID}"]]


def test_text_search_delegates_to_google_and_no_check_budget():
    google = FakePlacesClient(fixture_results={"praias": [{"place_id": "x"}]})
    hybrid = HybridPlacesClient(google, FakeApifyClient())
    assert asyncio.run(hybrid.text_search("praias", "BA")) == [{"place_id": "x"}]
    assert google.text_search_calls == [{"query": "praias", "uf": "BA"}]
    # run() probes check_budget with GOOGLE prices — the hybrid must not expose one.
    assert not hasattr(hybrid, "check_budget")
