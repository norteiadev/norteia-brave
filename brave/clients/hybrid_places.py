"""HybridPlacesClient — Apify search/details + Google Place Details Essentials.

Behind ``AppConfig.places_provider == "hybrid"``. Apify (compass~crawler-google-places)
finds the place and brings hours, phone, site, closed flags and the NEWEST review (Places
(New) returns reviews by relevance only, so a busy attraction looked stale); Google
Essentials (US$ 0.005, first-party) brings the id, coords, ``types`` enum, address and
addressComponents (município → IBGE, distrito). No network of its own: it composes a
``RealApifyPlacesClient`` and a ``RealPlacesClient`` and adapts their output to the exact
``RealPlacesClient`` dict shapes, so consumers do not change.

An Apify run costs ~45-60 s FIXED regardless of how many search strings it carries, so
batch callers ``prefetch()`` a whole page/lot first; ``search()``/``place_details()``
then read the cache and spend nothing on Apify.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from brave.clients.apify_places import apify_query, apify_run_cost
from brave.clients.nominatim import UF_NAME
from brave.clients.places import USD_DETAILS_ESSENTIALS, _normalize_name
from brave.shared.exceptions import CostGuardError, ProviderBalanceError
from brave.shared.opening_hours import _day_index

logger = structlog.get_logger(__name__)

# A wall stops the caller; it never marks a lot's queries as failed (see prefetch).
_WALLS = (ProviderBalanceError, CostGuardError)

# Queries per Apify run: ~6 s each on top of ~50 s fixed (smoke 2026-10-21: 13 in 128 s),
# so 50 finish well inside the run's 900 s timeout (a TIMED-OUT run loses its paid items).
APIFY_RUN_MAX_QUERIES = 50

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
        # ponytail: in-memory caches scoped to one Clients bag (one task run); entries are
        # popped when consumed, so only prefetched-but-unused queries linger until the bag
        # dies. Move to Redis if prefetch and consumption ever split across processes.
        self._found: dict[tuple[str, bool], dict[str, Any] | None] = {}  # (query, reviews)
        self._pairs: dict[tuple[str, bool], tuple[dict, dict]] = {}  # (place_id, reviews)
        # (folded name, UF) → a prefetched município-less name query. A TA sweep prefetches a
        # page before the ingest resolves each card's município ("Nome - UF"); run() then
        # searches with the Rio's município ("Nome, Município - UF") and lands here.
        self._by_name: dict[tuple[str, str], tuple[str, bool]] = {}
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

    async def prefetch(self, items: list[dict[str, Any]]) -> None:
        """Apify runs of ≤ APIFY_RUN_MAX_QUERIES, one after another, for a whole lot.

        item = {"place_id": ...} or {"nome", "municipio", "uf"} (an "include_reviews" key is
        ignored). Every run asks max_reviews: one 4 GB run at a time (two at once fill the
        FREE plan's 8 GB and every other run gets a busy 402), and a review costs US$ 0.0005
        — to_place_details drops it for a caller that skips reviews. A query missing from a
        SUCCEEDED run is a real no-hit OR a request the crawler gave up on
        (indistinguishable), so the exact ones (place_id / with município) get ONE more
        batched run; a miss there is cached as None (conclusive). A failed run marks its
        queries failed (see ``_failed``); the other runs still go, then the error raises.
        ponytail: +1 run (~60 s) per lot with misses; read the crawler's failed-request
        stats instead if Apify ever exposes them on the run.
        """
        # locationQuery is one per run: a município-less sweep name runs bounded to its UF
        # (None = no bound: a place_id, or a query that already names the município).
        by_location: dict[str | None, list[str]] = {}
        exact: set[str] = set()
        for item in items:
            location = None
            if item.get("place_id"):
                query = f"place_id:{item['place_id']}"
                exact.add(query)
            else:
                query = apify_query(item["nome"], item.get("municipio") or "", item["uf"])
                if item.get("municipio"):
                    exact.add(query)
                elif item["uf"].upper() in UF_NAME:
                    location = f"{UF_NAME[item['uf'].upper()]}, Brasil"
                # Also for an exact one: a backfill's TA can move the record to another
                # município before run() searches; the alias still serves it when Apify's
                # city is the new one (_take_by_name checks).
                self._by_name.setdefault((_fold(item["nome"]), item["uf"].upper()), (query, True))
            queries = by_location.setdefault(location, [])
            if (query, True) not in self._found and query not in queries:
                queries.append(query)

        async def lookup(chunk: list[str], location: str | None) -> dict[str, Any]:
            try:
                found = await self._apify.lookup(
                    chunk, max_reviews=self._max_reviews,
                    **({"location": location} if location else {}),
                )
                self._failed.difference_update(chunk)
                return found
            except _WALLS:
                raise
            except Exception:
                self._failed.update(chunk)
                raise

        self._lot_failed = False
        error: Exception | None = None
        chunks = [
            (location, queries[start:start + APIFY_RUN_MAX_QUERIES])
            for location, queries in by_location.items()
            for start in range(0, len(queries), APIFY_RUN_MAX_QUERIES)
        ]
        for location, chunk in chunks:
            try:
                found = await lookup(chunk, location)
                missed = [q for q in chunk if found.get(q) is None and q in exact]
                for q in chunk:
                    if q not in missed:
                        self._found[(q, True)] = found.get(q)
                if missed:
                    again = await lookup(missed, location)
                    for q in missed:
                        self._found[(q, True)] = again.get(q)
            except _WALLS:
                raise
            except Exception as exc:  # noqa: BLE001 — the lot's other runs still go
                self._lot_failed = True
                error = error or exc
        if error is not None:
            raise error

    def _check_budgets(self, include_reviews: bool) -> None:
        """Before an uncached lookup pays: no single run on a degraded Apify (``_lot_failed``),
        then both caps — a Places cap must not strand a paid Apify run."""
        if self._lot_failed:
            raise ApifyBatchFailedError("apify: this lot's batched run failed")
        self._apify.check_budget(apify_run_cost({
            "place-scraped": 1, "place-details-scraped": 1,
            "review-scraped": self._reviews(include_reviews), "apify-actor-start": 4,
        }))
        google_check = getattr(self._google, "check_budget", None)
        if google_check is not None:
            google_check(USD_DETAILS_ESSENTIALS)

    def _take(self, query: str, include_reviews: bool) -> tuple[bool, dict[str, Any] | None]:
        """(cached?, item) for ``query``, consumed. An entry fetched WITH reviews also serves
        a caller that skips them (to_place_details drops them); never the other way round.
        A query whose batched run failed raises instead of paying a run of its own."""
        for key in ((query, include_reviews), (query, True)):
            if key in self._found:
                return True, self._found.pop(key)
        if query in self._failed:
            raise ApifyBatchFailedError(f"apify: the batched run for {query!r} failed")
        return False, None

    def _take_by_name(
        self, nome: str, municipio: str, uf: str, include_reviews: bool
    ) -> dict[str, Any] | None:
        """A sweep's município-less hit for this name, when it sits in ``municipio``.

        Apify's own city (else its address) must name the município: a "Nome - UF" search
        can land on a homonym elsewhere in the UF, and the per-record search (with the
        município) is the better bet then. A cached no-hit is not trusted either (the
        narrower query may hit). ponytail: address substring when Apify has no city;
        Essentials' municipio_ibge would be exact but costs the Essentials call first.
        """
        alias = (_fold(nome), uf.upper())
        entry = self._by_name.get(alias)
        if entry is None:
            return None
        if entry[0] in self._failed:
            raise ApifyBatchFailedError(f"apify: the batched run for {entry[0]!r} failed")
        item = self._found.get(entry)
        if item is None:
            return None
        if municipio:
            city, wanted = _fold(item.get("city") or ""), _fold(municipio)
            sits = city == wanted if city else wanted in _fold(item.get("address") or "")
            if not sits:
                # Measures the sweep's alias miss rate (each miss pays a run of its own).
                logger.info("apify_alias_rejected", uf=uf, apify_city=item.get("city") or "")
                return None
        del self._by_name[alias]
        return self._found.pop(entry)

    def _take_by_place_id(self, place_id: str, include_reviews: bool) -> dict[str, Any] | None:
        """A prefetched name hit that turned out to be ``place_id`` (a locate() match or
        consensus in the sweep reaches place_details() with an id, never via search())."""
        for key, item in self._found.items():
            if item and item.get("placeId") == place_id and (key[1] or not include_reviews):
                return self._found.pop(key)
        return None

    async def _apify_single(
        self, query: str, include_reviews: bool, *, exact: bool
    ) -> dict[str, Any] | None:
        """One run for ``query``; an exact one that misses gets ONE more (as in prefetch: a
        miss may be a request the crawler gave up on, and a miss here is conclusive)."""
        for _ in range(2 if exact else 1):
            found = await self._apify.lookup([query], max_reviews=self._reviews(include_reviews))
            if found.get(query) is not None:
                return found[query]
        return None

    async def search(
        self, nome: str, municipio: str, uf: str, *, include_reviews: bool = True
    ) -> list[dict[str, Any]]:
        """0 or 1 result in ``RealPlacesClient.text_search()`` shape.

        [] when Apify had no hit or Essentials puts the place in another UF. The record's
        own exact query wins over a sweep's name alias.
        """
        query = apify_query(nome, municipio, uf)
        cached, item = self._take(query, include_reviews)
        if not cached:
            item = self._take_by_name(nome, municipio, uf, include_reviews)
            if item is None:
                self._check_budgets(include_reviews)
                item = await self._apify_single(query, include_reviews, exact=bool(municipio))
        if item is None:
            return []
        essentials = await self._google.place_essentials(item["placeId"], uf)
        uf_short = (essentials.get("uf_short") or "").upper()
        if len(uf_short) == 2 and uf_short != uf.upper():
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
        item = self._take_by_place_id(place_id, include_reviews)
        if item is None:
            cached, item = self._take(query, include_reviews)
            if cached and item is None:
                raise ApifyNoResultError(f"apify: no result for place_id {place_id}")
            if not cached:
                self._check_budgets(include_reviews)
        # Essentials first: a retired id raises NotFound before an Apify run is paid.
        # No uf is known here: municipio_ibge is not part of the details dict.
        essentials = await self._google.place_essentials(place_id, "")
        if item is None:
            item = await self._apify_single(query, include_reviews, exact=True)
        if item is None:
            raise ApifyNoResultError(f"apify: no result for place_id {place_id}")
        return to_place_details(item, essentials, include_reviews=include_reviews)
