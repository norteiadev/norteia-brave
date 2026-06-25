"""TripAdvisor GraphQL hybrid client.

Acquisition model (Phase 12):
  Session is injected by an operator via POST /api/v1/tripadvisor/session
  after capturing cookies from a real logged-in browser (DevTools Copy-as-cURL).
  _get_session() reads Redis only; SessionMissingError raised on miss.
  See scripts/ta_bootstrap.py for the injection helper.

Session dict shape (written by injection endpoint, read by _get_session):
  {
    "cookies": {"datadome": "abc", "TASession": "xyz", ...},
    "query_ids": {"destinations": "<16-hex>", "attractions": "<16-hex>"},
    "user_agent": "Mozilla/5.0 ...",
    "acquired_at": "2026-06-24T12:00:00Z",
    "session_id": "<TASID cookie value>",  # Phase 13: threaded into variables.sessionId
  }

Backwards compatibility: Phase 11 stored cookies as a list of
  {"name": ..., "value": ..., "domain": ...} dicts.
_get_session() normalises both shapes to a flat dict.

Security notes:
  - T-11-01-01: config.proxy_url never emitted in structlog calls.
  - T-11-01-02: Session cookie jar cached in Redis with TTL; never logged.
  - T-13-01-01: session_id (TASID) is a cookie value — NEVER logged; audit records
    only cookie_count + query_ids keys, session_id presence as boolean only.

Offline usage: inject NullTripAdvisorClient or FakeTripAdvisorClient instead.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from brave.config.settings import TripAdvisorConfig

logger = structlog.get_logger(__name__)

# Redis key for the cached session (cookie jar + queryId map)
# TTL is set by the injection endpoint (config.session_ttl, default 1800s / 30 min).
BRAVE_TA_SESSION_KEY: str = "brave:ta:session"

# GraphQL endpoint — all persisted-query POSTs target this URL
_TA_GRAPHQL_URL: str = "https://www.tripadvisor.com/data/graphql/ids"

# Safety guard: max pagination pages before stopping (prevents infinite loops, Risk A5)
_MAX_PAGES: int = 50


class SessionExpiredError(Exception):
    """Raised when a GraphQL request returns 403 or 429.

    Indicates the DataDome session cookies have expired or the queryId has
    rotated. Operator must re-inject a fresh session.
    """


class SessionMissingError(Exception):
    """Raised when BRAVE_TA_SESSION_KEY is absent from Redis.

    Operator must run scripts/ta_bootstrap.py --endpoint <URL> to inject a
    session before sweeping. The injection endpoint validates the session
    (canary check) before writing to Redis.
    """


class TripAdvisorClient:
    """TripAdvisor GraphQL hybrid client (real implementation).

    Accepts TripAdvisorClientProtocol structurally — see _check_protocol_compliance().

    Constructor does NOT import Playwright. Session acquisition is fully
    operator-gated: a human captures cookies from a real browser and POSTs
    them to /api/v1/tripadvisor/session. _get_session() reads from Redis only.

    Args:
        config: TripAdvisorConfig (proxy_url, session_ttl, query_id_override, ...).
        redis:  Sync Redis client (compatible with Celery worker thread context).
    """

    def __init__(self, config: "TripAdvisorConfig", redis: Any) -> None:
        self._config = config
        self._redis = redis

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_session(self) -> dict[str, Any]:
        """Return the operator-injected session from Redis.

        Returns:
            Session dict with 'cookies' (flat dict), 'query_ids', 'user_agent',
            and 'acquired_at' keys.

        Raises:
            SessionMissingError: When BRAVE_TA_SESSION_KEY is absent from Redis.
                Operator must inject a session via scripts/ta_bootstrap.py.
        """
        raw = self._redis.get(BRAVE_TA_SESSION_KEY)
        if raw is None:
            raise SessionMissingError(
                "No TripAdvisor session in Redis. "
                "Run: python scripts/ta_bootstrap.py --endpoint <URL>"
            )
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        session = json.loads(raw)
        # Backwards compat: Phase 11 stored cookies as a list of {name, value, domain}
        # The injection endpoint (plan 12-02) stores them as a flat dict.
        # Normalise to flat dict here so fetch_* can always do cookies["datadome"].
        if isinstance(session.get("cookies"), list):
            session["cookies"] = {c["name"]: c["value"] for c in session["cookies"]}
        return session

    # ------------------------------------------------------------------
    # Response parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_attractions_page(raw_sections: list) -> list[dict]:
        """Parse a raw sections list from the AttractionsFusion response.

        Keeps only WebPresentation_SingleFlexCardSection items and extracts
        the TripAdvisorReviewSignals fields from each FlexCard.

        Args:
            raw_sections: The sections list from data.Result[0].sections.

        Returns:
            List of normalized attraction dicts with keys:
              name, locationId, rating, review_count, category.
            Malformed cards are skipped with a debug log (never raises).
        """
        cards: list[dict] = []
        for section in raw_sections:
            if not isinstance(section, dict):
                continue
            if section.get("__typename") != "WebPresentation_SingleFlexCardSection":
                continue
            card = section.get("singleFlexCardContent")
            if not isinstance(card, dict):
                logger.debug("ta_parse_skip_missing_flex_content", section_type=section.get("__typename"))
                continue
            try:
                name: str = card.get("cardTitle", {}).get("text", "")
                location_id_raw = (
                    card.get("cardLink", {})
                    .get("webRoute", {})
                    .get("typedParams", {})
                    .get("detailId")
                )
                rating_raw = card.get("bubbleRating", {}).get("rating")
                review_count_raw = card.get("bubbleRating", {}).get("reviewCount")
                category: str = card.get("primaryInfo", {}).get("text", "")

                if location_id_raw is None:
                    logger.debug("ta_parse_skip_missing_detail_id", name=name)
                    continue

                cards.append(
                    {
                        "name": name,
                        "locationId": int(location_id_raw),
                        "rating": float(rating_raw) if rating_raw is not None else 0.0,
                        "review_count": int(review_count_raw) if review_count_raw is not None else 0,
                        "category": category,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("ta_parse_skip_malformed_card", error=type(exc).__name__)
                continue
        return cards

    # ------------------------------------------------------------------
    # Public protocol methods
    # ------------------------------------------------------------------

    async def fetch_destinations(
        self, uf: str, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Fetch TripAdvisor destinations (GEO entities) for a Brazilian UF.

        Uses the injected session cookies and queryId to POST a persisted GraphQL
        query. Raises SessionExpiredError on 403/429.

        Args:
            uf: Two-letter Brazilian state code.
            max_pages: Cap on pages to fetch. None (default) paginates up to
                _MAX_PAGES. The canary passes max_pages=1 — it only needs to prove
                the session returns any data, so it must not paginate a large UF
                past the 15 s timeout (WR-06).

        Returns:
            List of location dicts from the GraphQL response.

        Raises:
            SessionMissingError: When no session is in Redis (operator gate).
            SessionExpiredError: On 403 or 429 HTTP status (DataDome block / rate limit).
        """
        from brave.lanes.tripadvisor.geo import resolve_geo_id  # noqa: PLC0415

        geo_id = resolve_geo_id(uf, self._redis, self._config)
        session = self._get_session()
        query_id = session.get("query_ids", {}).get("destinations", "")
        cookies = session.get("cookies", {})
        user_agent = session.get("user_agent", "")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if user_agent:
            headers["User-Agent"] = user_agent

        # CR-02: route through the configured residential proxy (BRAVE_TA_PROXY_URL).
        # Without this every request egresses the server's datacenter IP — the IP
        # class the README/MITIGATIONS document as DataDome-walled.
        proxy = self._config.proxy_url or None

        page_limit = _MAX_PAGES if max_pages is None else min(max_pages, _MAX_PAGES)
        results: list[dict[str, Any]] = []
        for page_num in range(page_limit):
            offset = page_num * 20
            payload = [
                {
                    "variables": {"locationId": geo_id, "offset": offset, "limit": 20},
                    "extensions": {"preRegisteredQueryId": query_id},
                }
            ]
            async with httpx.AsyncClient(
                cookies=cookies, follow_redirects=True, proxy=proxy
            ) as hc:
                resp = await hc.post(
                    _TA_GRAPHQL_URL,
                    json=payload,
                    headers=headers,
                )

            if resp.status_code in (403, 429):
                raise SessionExpiredError(
                    f"TripAdvisor GraphQL returned {resp.status_code} — "
                    "DataDome session expired or queryId rotated. Re-inject required."
                )

            resp.raise_for_status()
            data = resp.json()
            # Handle both list-wrapped and dict response shapes
            if isinstance(data, list) and data:
                items = data[0].get("data", {}).get("locations", []) or []
            elif isinstance(data, dict):
                items = data.get("data", {}).get("locations", []) or []
            else:
                items = []

            if not items:
                break  # Empty page → pagination complete
            results.extend(items)
            if len(items) < 20:
                break  # Partial page → last page

        return results

    async def fetch_attractions(
        self, geo_id: int, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Fetch TripAdvisor attractions (ATTRACTION entities) for a geoId.

        Uses the live-validated AttractionsFusion listing query (qid a5cb7fa004b5e4b5)
        with the real request.routeParameters variables shape (Phase 13).

        Args:
            geo_id: TripAdvisor integer geoId.
            max_pages: Cap on pages to fetch. None (default) fetches a single page
                (see pagination gap comment below). The canary passes max_pages=1.

        Returns:
            List of attraction dicts with keys: name, locationId, rating,
            review_count, category.

        Raises:
            SessionMissingError: When no session is in Redis (operator gate).
            SessionExpiredError: On 403 or 429 HTTP status.
        """
        session = self._get_session()
        # T-13-01-02: qid is hardcoded — NOT read from session["query_ids"]["attractions"].
        # The real listing qid is fixed (a5cb7fa004b5e4b5); reading it from session
        # would allow injection of a stale/wrong qid (e.g. a telemetry or ad qid).
        _LISTING_QID = "a5cb7fa004b5e4b5"

        # T-13-01-01: session_id is a TASID cookie value — NEVER logged.
        session_id: str = session.get("session_id", "")
        cookies = session.get("cookies", {})
        user_agent = session.get("user_agent", "")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if user_agent:
            headers["User-Agent"] = user_agent

        # CR-02: route through the configured residential proxy (BRAVE_TA_PROXY_URL).
        proxy = self._config.proxy_url or None

        # PAGINATION GAP (Phase 13): AttractionsFusion variables carry no confirmed
        # page/offset param. The payload (routeParameters, no offset) is identical
        # across iterations, so looping would re-POST page 1 and duplicate its cards
        # into the result set. Until real pagination (oa30 via PaginationLinksList)
        # lands, fetch_attractions is a STRICT single-page contract.
        #
        # WR-02: enforce the contract instead of relying on the max_pages=None→1
        # default. A caller passing max_pages>1 would otherwise silently duplicate
        # page-1 cards — fail loud rather than corrupt ingest.
        if max_pages is not None and max_pages > 1:
            raise NotImplementedError(
                "fetch_attractions is single-page only — AttractionsFusion carries "
                "no page/offset param, so max_pages>1 would re-POST page 1 and "
                "duplicate cards. Multi-page pagination is a follow-up (PAGINATION GAP)."
            )

        pageview_uid = str(uuid.uuid4())
        payload = [
            {
                "variables": {
                    "request": {
                        "tracking": {
                            "screenName": "AttractionsFusion",
                            "pageviewUid": pageview_uid,
                        },
                        "routeParameters": {
                            "geoId": geo_id,
                            "contentType": "attraction",
                            "webVariant": "AttractionsFusion",
                            "filters": [{"id": "allAttractions", "value": ["true"]}],
                        },
                        "updateToken": None,
                    },
                    "commerce": {
                        "attractionCommerce": {
                            "pax": [{"ageBand": "ADULT", "count": 2}]
                        }
                    },
                    "tracking": {
                        "screenName": "AttractionsFusion",
                        "pageviewUid": pageview_uid,
                    },
                    "sessionId": session_id,
                    "unitLength": "MILES",
                    "currency": "USD",
                    "currentGeoPoint": None,
                    "mapSurface": False,
                    "debug": False,
                    "polling": False,
                },
                "extensions": {"preRegisteredQueryId": _LISTING_QID},
            }
        ]
        async with httpx.AsyncClient(
            cookies=cookies, follow_redirects=True, proxy=proxy
        ) as hc:
            resp = await hc.post(
                _TA_GRAPHQL_URL,
                json=payload,
                headers=headers,
            )

        if resp.status_code in (403, 429):
            raise SessionExpiredError(
                f"TripAdvisor GraphQL returned {resp.status_code} — "
                "DataDome session expired or queryId rotated. Re-inject required."
            )

        resp.raise_for_status()
        data = resp.json()

        # Safe extraction of sections list from the real response path
        sections: list = []
        try:
            sections = data[0]["data"]["Result"][0]["sections"]
        except (IndexError, KeyError, TypeError):
            sections = []

        if not sections:
            return []  # Empty sections → no attractions for this geo

        # Single page only — no partial-page break (the meaningless `< 30` guard is
        # removed: there is no second page to gate, so card count never terminates a loop).
        return self._parse_attractions_page(sections)

    async def resolve_geo_id(self, uf: str) -> int:
        """Resolve a Brazilian UF code to its TripAdvisor integer geoId.

        Delegates to geo.resolve_geo_id (Redis cache → seed JSON fallback).

        Args:
            uf: Two-letter Brazilian state code.

        Returns:
            TripAdvisor integer geoId.

        Raises:
            ValueError: When UF is unknown (not in cache or seed JSON).
        """
        from brave.lanes.tripadvisor.geo import resolve_geo_id  # noqa: PLC0415

        return resolve_geo_id(uf, self._redis, self._config)


# Structural type check: TripAdvisorClient must satisfy TripAdvisorClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    import fakeredis  # type: ignore[import]

    from brave.clients.base import TripAdvisorClientProtocol
    from brave.config.settings import AppConfig

    config = AppConfig().tripadvisor
    redis = fakeredis.FakeRedis()
    _client: TripAdvisorClientProtocol = TripAdvisorClient(config=config, redis=redis)  # noqa: F841
