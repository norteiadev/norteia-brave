"""HybridPlacesClient — Google picks the place, Apify brings its fields.

Behind ``AppConfig.places_provider == "hybrid"``. Per atrativo: the TA page gives the
município and a point; Google Text Search IDs Only (free) lists candidates for
"Nome, Município - UF" biased around that point; Place Details Essentials (US$ 0.005,
first-party: id, coords, ``types`` enum, address, addressComponents) on the top ones, in
Google's rank order, until one sits in the UF within the pick radius; Apify
(compass~crawler-google-places) then fetches THAT ``place_id:`` for hours, phone, site,
closed flags and the NEWEST review (Places (New) returns reviews by relevance only, so a
busy attraction looked stale). No network of its own: it composes a
``RealApifyPlacesClient`` and a ``RealPlacesClient`` and adapts their output to the exact
``RealPlacesClient`` dict shapes, so consumers do not change.

An Apify run costs ~45-60 s FIXED regardless of how many search strings it carries, so
batch callers ``prefetch()`` a whole page/lot first; ``search()``/``place_details()``
then read the cache and spend nothing on Apify.
"""

from __future__ import annotations

import math
import re
from typing import Any

import structlog
from celery.exceptions import SoftTimeLimitExceeded

from brave.clients.apify_places import apify_run_cost
from brave.clients.places import USD_DETAILS_ESSENTIALS, _normalize_name
from brave.shared.exceptions import CostGuardError, ProviderBalanceError
from brave.shared.opening_hours import _day_index

logger = structlog.get_logger(__name__)

# A wall stops the caller; it never marks a lot's queries as failed (see prefetch). The soft
# time limit too: swallowed per record, the lot would start its Apify run and be hard-killed.
_WALLS = (ProviderBalanceError, CostGuardError, SoftTimeLimitExceeded)

# Essentials paid in Google's rank order (RO: ~1.47 calls per atrativo).
PICK_CANDIDATES = 3
# Search bias: 5 km around a point (RO POC); Google caps a circle at 50 km.
_BIAS_MIN_KM, _BIAS_MAX_KM = 5.0, 50.0

# Queries per Apify run: ~6 s each on top of ~50 s fixed (smoke 2026-10-21: 13 in 128 s),
# so 50 finish well inside the run's 900 s timeout (a TIMED-OUT run loses its paid items).
APIFY_RUN_MAX_QUERIES = 50

# The crawler takes 27-char ChIJ…/GhIJ… place ids only. Any other id (Google's address
# or route ids, e.g. a 139-char 'EjZF…' for a road, RS 26/10) FAILS the whole run, so
# it is never sent and reads as a conclusive no-hit.
_APIFY_PLACE_ID = re.compile(r"(?:ChIJ|GhIJ)[\w-]{23}")

# Apify writes ranges as "07:00 to 17:00"; to_hours_map splits on dashes only.
_TO = re.compile(r"\s+to\s+", re.IGNORECASE)


class ApifyNoResultError(RuntimeError):
    """Apify answered and has no place for this id: conclusive (the agent marks the record
    consulted), unlike a failed run (transient, the TA floor stays unmarked)."""


class ApifyBatchFailedError(RuntimeError):
    """This lookup's batched run failed and nothing was asked for this record: no answer at
    all (the refresh must not stamp it), and never a run of its own (see ``_failed``)."""


def _fold(text: str) -> str:
    """Accent/case/space-insensitive key: the raw TA card name vs the Rio's normalize_name."""
    return " ".join(_normalize_name(text).split())


def _key(nome: str, municipio: str, uf: str) -> tuple[str, str, str]:
    """One lookup's identity: prefetch and run() read the same normalized, so they agree."""
    return _fold(nome), _fold(municipio), uf.upper()


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine. Inlined: clients never import brave.domains (test_domain_boundaries CHECK D)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


_SEARCH_KEYS = (
    "place_id", "formatted_address", "types", "location", "municipio_nome",
    "municipio_ibge", "uf_short", "municipio_from_locality", "distrito_hint",
)


def _business_status(apify: dict[str, Any]) -> str:
    if apify.get("permanentlyClosed"):
        return "CLOSED_PERMANENTLY"
    if apify.get("temporarilyClosed"):
        return "CLOSED_TEMPORARILY"
    return "OPERATIONAL"


def _day_order(h: dict[str, Any]) -> int:
    day = _day_index(h["day"])
    return 7 if day is None else day


def to_place_details(
    apify: dict[str, Any], essentials: dict[str, Any], *, include_reviews: bool
) -> dict[str, Any]:
    """The ``RealPlacesClient.place_details()`` dict (plus ``provider``) from both halves.

    ``openingHours`` starts TODAY: sorted Monday-first like Google's, else the stored lines
    (and the push) would change with the weekday of each refresh.
    """
    reviews = [
        {"publishTime": r.get("publishedAtDate"), "rating": r.get("stars"), "text": r.get("text") or ""}
        for r in apify.get("reviews") or []
    ] if include_reviews else []
    return {
        "place_id": essentials["place_id"],
        "name": apify.get("title") or "",
        "formatted_address": essentials.get("formatted_address") or apify.get("address") or "",
        "international_phone_number": apify.get("phone"),
        "website": apify.get("website"),
        "business_status": _business_status(apify),
        "weekday_text": [
            f"{h['day']}: {_TO.sub('–', h['hours'])}"
            for h in sorted(
                (h for h in apify.get("openingHours") or [] if h.get("day") and h.get("hours")),
                key=_day_order,
            )
        ],
        "reviews": reviews,
        # Same Google point either way; Essentials first (first-party), Apify if it had none.
        "location": essentials.get("location") or apify.get("location"),
        "distrito_hint": essentials.get("distrito_hint", ""),
        # Apify gives "R$ 40–60" text: no safe map to PRICE_LEVEL_* (Places gave 0% anyway).
        "price_level": None,
        "types": list(essentials.get("types") or []),
        # Pro-tier field: not fetched (Essentials only). _google_not_atrativo skips "".
        "primary_type": "",
        "provider": "hybrid",
    }


class HybridPlacesClient:
    """PlacesClientProtocol over Apify + Google Essentials (see module docstring).

    Deliberately has NO ``check_budget``: PlacesEnrichmentAgent.run() probes it with
    GOOGLE Text Search + Details prices, which this client never pays.

    Args:
        google: RealPlacesClient (Essentials + the text_search passthrough).
        apify: RealApifyPlacesClient.
        max_reviews: reviews per place when the caller wants them (newest first).
    """

    def __init__(self, google: Any, apify: Any, *, max_reviews: int = 1) -> None:
        self._google = google
        self._apify = apify
        self._max_reviews = max_reviews
        # ponytail: in-memory caches scoped to one Clients bag (one task run): entries live
        # as long as the bag, and a duplicate card / record is served by the same entry.
        # Move to Redis if prefetch and consumption ever split across processes.
        self._found: dict[tuple[str, bool], dict[str, Any] | None] = {}  # ("place_id:…", reviews)
        self._pairs: dict[tuple[str, bool], tuple[dict, dict]] = {}  # (place_id, reviews)
        # _key → the Essentials Google's pick chose (None: no candidate passed).
        self._picked: dict[tuple[str, str, str], dict[str, Any] | None] = {}
        # _keys whose pick failed in a prefetch: search() raises instead of a lookup of its own.
        self._pick_failed: set[tuple[str, str, str]] = set()
        # Queries whose batched run failed: looked up again by the NEXT lot, never one run per
        # record (~60 s each — a 200-record refresh would blow its time limit).
        self._failed: set[str] = set()
        # The latest prefetch had a failed run: an id it never carried (a sweep card's
        # locate()/consensus id) fails too instead of paying a single run on a degraded Apify.
        self._lot_failed = False

    async def text_search(self, query: str, uf: str) -> list[dict[str, Any]]:
        # locate() fallback D and DiscoveryAgent need many results: Google this phase.
        return await self._google.text_search(query, uf)

    def _reviews(self, include_reviews: bool) -> int:
        return self._max_reviews if include_reviews else 0

    async def _pick(
        self, nome: str, municipio: str, uf: str, lat: float, lng: float, radius_km: float
    ) -> dict[str, Any] | None:
        """Google's own pick: the first of the top PICK_CANDIDATES (free IDs-only search, biased
        to the reference) that is not a geographic entity, sits in ``uf`` (a TA point fell in
        Juína-MT) and lies within ``radius_km`` of (lat, lng). Essentials only for the ones
        looked at."""
        query = f"{nome}, {municipio} - {uf}" if municipio else f"{nome} - {uf}"
        bias_km = min(max(radius_km, _BIAS_MIN_KM), _BIAS_MAX_KM)
        ids = await self._google.search_ids(query, lat=lat, lng=lng, radius_m=bias_km * 1000)
        for place_id in ids[:PICK_CANDIDATES]:
            ess = await self._google.place_essentials(place_id, uf)
            loc = ess.get("location") or {}
            uf_short = (ess.get("uf_short") or "").upper()
            if (
                "political" in (ess.get("types") or ())
                or loc.get("lat") is None
                or (len(uf_short) == 2 and uf_short != uf.upper())
            ):
                continue
            if _km(lat, lng, loc["lat"], loc["lng"]) <= radius_km:
                return ess
        return None

    async def prefetch(self, items: list[dict[str, Any]]) -> None:
        """Google picks for the name items, then Apify runs of ≤ APIFY_RUN_MAX_QUERIES
        ``place_id:`` queries, one after another, for the whole lot.

        item = {"place_id"} or {"nome", "municipio", "uf", "lat", "lng", "radius_km"} (an
        "include_reviews" key is ignored). A pick that fails (not a wall) is remembered:
        search() then raises ApifyBatchFailedError instead of paying a lookup of its own.
        Every run asks max_reviews: one 4 GB run at a time (two at once fill the FREE plan's
        8 GB and every other run gets a busy 402), and a review costs US$ 0.0005 —
        to_place_details drops it for a caller that skips reviews. A query missing from a
        SUCCEEDED run is a real no-hit OR a request the crawler gave up on
        (indistinguishable), so misses get ONE more batched run; a miss there is cached as
        None (conclusive). A failed run marks its queries failed (see ``_failed``); the other
        runs still go, then the error raises.
        ponytail: +1 run (~60 s) per lot with misses; read the crawler's failed-request
        stats instead if Apify ever exposes them on the run.
        """
        queries: list[str] = []
        for item in items:
            place_id = item.get("place_id")
            if not place_id:
                key = _key(item["nome"], item["municipio"], item["uf"])
                if key not in self._picked:
                    try:
                        self._picked[key] = await self._pick(
                            item["nome"], item["municipio"], item["uf"],
                            item["lat"], item["lng"], item["radius_km"],
                        )
                        self._pick_failed.discard(key)
                    except _WALLS:
                        raise
                    except Exception as exc:  # noqa: BLE001 — that record raises in search()
                        logger.warning("hybrid_pick_failed", error=type(exc).__name__)
                        self._pick_failed.add(key)
                        continue
                place_id = (self._picked[key] or {}).get("place_id")
                if not place_id:
                    continue
            if not _APIFY_PLACE_ID.fullmatch(place_id):
                continue  # _take() answers it: a no-hit, no run
            query = f"place_id:{place_id}"
            if (query, True) not in self._found and query not in queries:
                queries.append(query)

        async def lookup(chunk: list[str]) -> dict[str, Any]:
            try:
                found = await self._apify.lookup(chunk, max_reviews=self._max_reviews)
                self._failed.difference_update(chunk)
                return found
            except _WALLS:
                raise
            except Exception:
                self._failed.update(chunk)
                raise

        self._lot_failed = False
        error: Exception | None = None
        for start in range(0, len(queries), APIFY_RUN_MAX_QUERIES):
            chunk = queries[start:start + APIFY_RUN_MAX_QUERIES]
            try:
                found = await lookup(chunk)
                missed = [q for q in chunk if found.get(q) is None]
                for q in chunk:
                    if q not in missed:
                        self._found[(q, True)] = found[q]
                if missed:
                    again = await lookup(missed)
                    for q in missed:
                        self._found[(q, True)] = again.get(q)
            except _WALLS:
                raise
            except Exception as exc:  # noqa: BLE001 — the lot's other runs still go
                self._lot_failed = True
                error = error or exc
        if error is not None:
            raise error

    def _check_budgets(self, include_reviews: bool, essentials: int) -> None:
        """Before an uncached lookup pays: no single run on a degraded Apify (``_lot_failed``),
        then both caps — a Places cap must not strand a paid Apify run."""
        if self._lot_failed:
            raise ApifyBatchFailedError("apify: this lot's batched run failed")
        self._apify.check_budget(apify_run_cost({
            "place-scraped": 1, "place-details-scraped": 1,
            "review-scraped": self._reviews(include_reviews), "apify-actor-start": 4,
        }))
        google_check = getattr(self._google, "check_budget", None)
        if google_check is not None and essentials:
            google_check(essentials * USD_DETAILS_ESSENTIALS)

    def _take(self, query: str, include_reviews: bool) -> tuple[bool, dict[str, Any] | None]:
        """(cached?, item) for ``query``. An entry fetched WITH reviews also serves a caller
        that skips them (to_place_details drops them); never the other way round. A query
        whose batched run failed raises instead of paying a run of its own; an id the
        crawler cannot take is a cached no-hit."""
        if not _APIFY_PLACE_ID.fullmatch(query.removeprefix("place_id:")):
            return True, None
        for key in ((query, include_reviews), (query, True)):
            if key in self._found:
                return True, self._found[key]
        if query in self._failed:
            raise ApifyBatchFailedError(f"apify: the batched run for {query!r} failed")
        return False, None

    async def _apify_single(self, query: str, include_reviews: bool) -> dict[str, Any] | None:
        """One run for ``query``; a miss gets ONE more (as in prefetch: a miss may be a
        request the crawler gave up on, and a miss here is conclusive)."""
        for _ in range(2):
            found = await self._apify.lookup([query], max_reviews=self._reviews(include_reviews))
            if found.get(query) is not None:
                return found[query]
        return None

    async def search(
        self, nome: str, municipio: str, uf: str, *, lat: float, lng: float, radius_km: float,
        include_reviews: bool = True,
    ) -> list[dict[str, Any]]:
        """0 or 1 result in ``RealPlacesClient.text_search()`` shape: Google's pick (see
        _pick) named by Apify's title, for the agent's _best_match name check. [] with no pick
        or no Apify item for the picked id. The caller always has a reference point (run()
        never searches without one)."""
        key = _key(nome, municipio, uf)
        if key in self._picked:
            essentials = self._picked[key]
        elif key in self._pick_failed:
            raise ApifyBatchFailedError("hybrid: this lot's Google pick failed")
        else:
            self._check_budgets(include_reviews, PICK_CANDIDATES)  # before any Essentials
            essentials = await self._pick(nome, municipio, uf, lat, lng, radius_km)
        if essentials is None:
            return []
        query = f"place_id:{essentials['place_id']}"
        cached, item = self._take(query, include_reviews)  # raises after a failed lot
        if not cached:
            self._check_budgets(include_reviews, 0)
            item = await self._apify_single(query, include_reviews)
        if item is None:
            return []
        # Kept so the place_details() that follows a match is free.
        self._pairs[(essentials["place_id"], include_reviews)] = (item, essentials)
        result = {k: essentials.get(k) for k in _SEARCH_KEYS}
        result["name"] = item.get("title") or ""
        result["location"] = essentials.get("location") or item.get("location")
        return [result]

    async def place_details(
        self, place_id: str, *, include_reviews: bool = True
    ) -> dict[str, Any]:
        """``RealPlacesClient.place_details()`` shape (+ ``provider``).

        From the search()/prefetch() cache when present, else Essentials + Apify
        ``place_id:`` run. Apify no hit → ApifyNoResultError (a known no-hit raises before
        Essentials is paid); a failed run → RuntimeError (the agent keeps the TA floor);
        Google NotFound from Essentials propagates so the agent clears a retired id.
        """
        pair = self._pairs.pop((place_id, include_reviews), None)
        if pair is not None:
            return to_place_details(*pair, include_reviews=include_reviews)
        query = f"place_id:{place_id}"
        cached, item = self._take(query, include_reviews)
        if cached and item is None:
            raise ApifyNoResultError(f"apify: no result for place_id {place_id}")
        if not cached:
            self._check_budgets(include_reviews, 1)
        # Essentials first: a retired id raises NotFound before an Apify run is paid.
        # No uf is known here: municipio_ibge is not part of the details dict.
        essentials = await self._google.place_essentials(place_id, "")
        if item is None:
            item = await self._apify_single(query, include_reviews)
        if item is None:
            raise ApifyNoResultError(f"apify: no result for place_id {place_id}")
        return to_place_details(item, essentials, include_reviews=include_reviews)
