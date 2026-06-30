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

import asyncio
import json
import re
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

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

# HTML SSR listing page (Phase 15 pagination transport). The GraphQL listing query
# cannot paginate, so each page is fetched as the server-rendered -oa{offset}- HTML
# variant. URL = fixed template + int geo_id + computed int offset (SSRF-safe,
# T-15-04-02). offset = (page-1)*30.
_TA_HTML_URL: str = (
    "https://www.tripadvisor.com/Attractions-g{geo_id}-Activities-"
    "a_allAttractions.true-oa{offset}-Brazil.html"
)

# TripAdvisor caps its paginated listing at 10000 results (offset 9990 == page 334).
# The page loop is clamped to this regardless of start_page + max_pages (LOW-3 fix).
_TA_MAX_PAGE: int = 334
_TA_MAX_OFFSET: int = 9990

# Marker identifying a FlexCard attraction section inside the embedded JSON island.
_TA_FLEXCARD_TYPENAME: str = "WebPresentation_SingleFlexCardSection"

# Safety guard: max pagination pages before stopping (prevents infinite loops, Risk A5)
_MAX_PAGES: int = 50

# Destinations (GEO entities) persisted query id.
# Discovered by inspecting browser DevTools: the POST to /data/graphql/ids
# that returns locations[] for a Brazilian state geo page.
# Set to None until captured from a real session; override via
# BRAVE_TA_QUERY_ID_OVERRIDE={"destinations":"<qid>"}.
_DESTINATIONS_QID: str | None = None


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
                # Use `(card.get(k) or {})` instead of `card.get(k, {})` so that
                # present-but-null fields (e.g. bubbleRating=null) are also guarded.
                # `.get(k, {})` only catches absent keys; `or {}` catches None too.
                name: str = (card.get("cardTitle") or {}).get("text", "")
                location_id_raw = (
                    card.get("cardLink", {})
                    .get("webRoute", {})
                    .get("typedParams", {})
                    .get("detailId")
                )
                rating_raw = (card.get("bubbleRating") or {}).get("rating")
                review_count_raw = (card.get("bubbleRating") or {}).get("reviewCount")
                category: str = (card.get("primaryInfo") or {}).get("text", "")

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

    @staticmethod
    def _find_flexcard_sections(node: Any, acc: list[dict], depth: int = 0) -> None:
        """Recursively collect FlexCard section dicts from a decoded JSON tree.

        The SSR page embeds the listing as a chunked, multiply-escaped flight
        payload: dict/list values whose string leaves are themselves JSON. This
        walker descends dicts and lists, and — when a *string* leaf still carries
        the FlexCard marker — re-parses it as JSON and recurses. Bounded depth
        guards against pathological nesting. Never raises.

        Args:
            node: A decoded JSON value (dict, list, str, or scalar).
            acc: Accumulator list; matching section dicts are appended in place.
            depth: Current recursion depth (internal; capped at 40).
        """
        if depth > 40:
            return
        if isinstance(node, dict):
            if node.get("__typename") == _TA_FLEXCARD_TYPENAME:
                acc.append(node)
            for value in node.values():
                TripAdvisorClient._find_flexcard_sections(value, acc, depth + 1)
        elif isinstance(node, list):
            for value in node:
                TripAdvisorClient._find_flexcard_sections(value, acc, depth + 1)
        elif isinstance(node, str):
            # A string leaf that still contains the marker is an inner JSON chunk.
            if _TA_FLEXCARD_TYPENAME in node:
                try:
                    parsed = json.loads(node)
                except (ValueError, TypeError):
                    return
                TripAdvisorClient._find_flexcard_sections(parsed, acc, depth + 1)

    @staticmethod
    def _extract_sections_from_html(html: str) -> list[dict]:
        """Recover the embedded FlexCard ``sections[]`` JSON island from an SSR page.

        The all-Brazil ``-oa{N}-`` HTML page renders the listing both as DOM and as
        a chunked JSON flight payload embedded in a ``<script src="data:text/...">``
        island (percent-encoded JS, with the card JSON nested several escape levels
        deep). This recovers that island using ONLY stdlib ``re`` + ``json`` (no
        lxml/beautifulsoup/selectolax/playwright — RESEARCH Don't-Hand-Roll): it
        locates the script blob carrying the FlexCard marker, URL-decodes the
        ``data:`` URI, ``json.loads`` the longest JS string literal that still
        carries the marker (peeling one escape level), then recursively walks the
        result (re-parsing inner JSON-string chunks) to collect the section dicts.

        The output is the SAME ``raw_sections`` shape ``_parse_attractions_page``
        consumes — a list of dicts carrying ``__typename ==
        'WebPresentation_SingleFlexCardSection'`` and ``singleFlexCardContent``.
        It is fed to that parser UNCHANGED (LGPD aggregate-only posture preserved).

        Mirrors ``_parse_attractions_page``'s never-raises posture: returns ``[]``
        on any miss (empty input, no island, decode/parse failure).

        Args:
            html: Raw HTML body of a captured ``-oa{N}-`` attractions page.

        Returns:
            List of FlexCard section dicts (possibly empty). Never raises.
        """
        if not html or _TA_FLEXCARD_TYPENAME not in html:
            return []

        # Locate the <script> blob that carries the FlexCard marker. The marker
        # letters are not percent-encoded, so they appear literally even inside the
        # data: URI src attribute.
        marker_idx = html.find(_TA_FLEXCARD_TYPENAME)
        script_start = html.rfind("<script", 0, marker_idx)
        script_end = html.find("</script>", marker_idx)
        if script_start == -1 or script_end == -1:
            return []
        blob = html[script_start:script_end]

        # The JSON lives in the data: URI src attribute (or, defensively, the body).
        src_pos = blob.find('src="')
        payload = blob[src_pos + 5:] if src_pos != -1 else blob

        try:
            decoded = unquote(payload)
        except Exception:  # noqa: BLE001 — never raise from a parser
            return []

        # Peel one escape level: json.loads the longest JS string literal that still
        # carries the marker. That yields a JSON string the recursive walker finishes.
        string_literal = re.compile(r'"((?:[^"\\]|\\.)*)"', re.DOTALL)
        candidates = [
            m.group(0)
            for m in string_literal.finditer(decoded)
            if _TA_FLEXCARD_TYPENAME in m.group(0)
        ]
        if not candidates:
            return []
        best = max(candidates, key=len)
        try:
            peeled = json.loads(best)
        except (ValueError, TypeError):
            return []

        sections: list[dict] = []
        TripAdvisorClient._find_flexcard_sections(peeled, sections)
        return sections

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
        from brave.lanes.tripadvisor.session import persist_rotated_cookies  # noqa: PLC0415

        geo_id = resolve_geo_id(uf, self._redis, self._config)
        session = self._get_session()
        # Three-step QID resolution (SPIKE 260629-rmz Finding 2):
        # 1. config.query_id_override["destinations"] — operator override wins
        # 2. session["query_ids"].get("destinations")  — legacy session key
        # 3. _DESTINATIONS_QID module constant          — pinned when discovered
        # If all three are falsy, raise ValueError (T-rmz-04: never silent empty QID).
        query_id = (
            self._config.query_id_override.get("destinations")
            or session.get("query_ids", {}).get("destinations")
            or _DESTINATIONS_QID
        )
        if not query_id:
            raise ValueError(
                "No destinations queryId configured. "
                'Set BRAVE_TA_QUERY_ID_OVERRIDE={"destinations":"<qid>"} or pin '
                "_DESTINATIONS_QID in client.py. "
                "Discover the QID by inspecting browser DevTools: POST /data/graphql/ids "
                "for a TA destinations/geo listing page and copy the preRegisteredQueryId."
            )
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
            # Write-back: merge rotated cookies into Redis session (260629-p2v).
            # Best-effort — errors must not abort the fetch (belt-and-suspenders
            # guard in addition to persist_rotated_cookies's own internal try/except).
            rotated = dict(resp.cookies)
            if rotated:
                cookies = {**cookies, **rotated}  # update local var for next page
                try:
                    persist_rotated_cookies(self._redis, rotated, self._config)
                except Exception:  # noqa: BLE001
                    pass  # persist_rotated_cookies already swallows, but guard defensively
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
        from brave.lanes.tripadvisor.session import persist_rotated_cookies  # noqa: PLC0415

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
        # Write-back: merge rotated cookies into Redis session (260629-p2v).
        # Single POST — no local cookies var to update for next iteration.
        rotated = dict(resp.cookies)
        if rotated:
            try:
                persist_rotated_cookies(self._redis, rotated, self._config)
            except Exception:  # noqa: BLE001
                pass  # best-effort guard
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

    async def fetch_attraction_detail(self, location_id: int) -> dict | None:
        """Fetch the TA detail record for a single attraction (qid 444040f131735091).

        NOTE (TA-ftx): No longer called by TripAdvisorAtrativosIngest._ingest_one —
        replaced by fetch_attraction_geo (qid d3d4987463b78a39) which returns
        cityName/stateName directly without a parents[] hop. Method kept; existing
        TestFetchAttractionDetail tests remain valid and must not be removed.

        Returns the first location dict from the response (contains parents[] geo
        hierarchy). Returns None on empty response or any parsing error.
        Never raises on data shape issues — returns None instead.

        Args:
            location_id: TripAdvisor integer locationId of the attraction.

        Raises:
            SessionMissingError: When no session is in Redis.
            SessionExpiredError: On 403 or 429 HTTP status.
        """
        from brave.lanes.tripadvisor.session import persist_rotated_cookies  # noqa: PLC0415

        session = self._get_session()
        cookies = session.get("cookies", {})
        user_agent = session.get("user_agent", "")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if user_agent:
            headers["User-Agent"] = user_agent
        proxy = self._config.proxy_url or None
        payload = [
            {
                "variables": {"locationId": location_id},
                "extensions": {"preRegisteredQueryId": "444040f131735091"},
            }
        ]
        async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, proxy=proxy) as hc:
            resp = await hc.post(_TA_GRAPHQL_URL, json=payload, headers=headers)
        if resp.status_code in (403, 429):
            raise SessionExpiredError(
                f"TripAdvisor detail returned {resp.status_code} — session expired."
            )
        resp.raise_for_status()
        rotated = dict(resp.cookies)
        if rotated:
            try:
                persist_rotated_cookies(self._redis, rotated, self._config)
            except Exception:  # noqa: BLE001
                pass
        try:
            data = resp.json()
            locations = data[0]["data"]["locations"]
            if not locations:
                return None
            return locations[0]
        except (IndexError, KeyError, TypeError, ValueError):
            return None

    async def fetch_attraction_geo(self, location_id: int) -> dict | None:
        """Fetch parent município geo data for one attraction (qid d3d4987463b78a39).

        Single GraphQL request — no HTML surface, no parents hop. Returns a
        normalized dict {location_id, city_name, state_name, city_geo_id,
        state_geo_id} from data.gtmData.locationData. Returns None on empty
        response or any parsing error.

        ToS/LGPD: aggregate geo only (cityName/stateName/geoIds); no PII.
        Validated: 5 attractions / 2 cities (SPIKE-2 2026-06-30).

        Args:
            location_id: TripAdvisor integer locationId of the attraction.

        Raises:
            SessionMissingError: When no session is in Redis.
            SessionExpiredError: On 403 or 429 HTTP status.
        """
        from brave.lanes.tripadvisor.session import persist_rotated_cookies  # noqa: PLC0415

        session = self._get_session()
        cookies = session.get("cookies", {})
        user_agent = session.get("user_agent", "")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if user_agent:
            headers["User-Agent"] = user_agent
        proxy = self._config.proxy_url or None
        payload = [
            {
                "variables": {
                    "locationId": location_id,
                    "eventType": "PAGEVIEW",
                    "isGeoPage": True,
                },
                "extensions": {"preRegisteredQueryId": "d3d4987463b78a39"},
            }
        ]
        async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, proxy=proxy) as hc:
            resp = await hc.post(_TA_GRAPHQL_URL, json=payload, headers=headers)
        if resp.status_code in (403, 429):
            raise SessionExpiredError(
                f"TripAdvisor geo returned {resp.status_code} — session expired."
            )
        resp.raise_for_status()
        rotated = dict(resp.cookies)
        if rotated:
            try:
                persist_rotated_cookies(self._redis, rotated, self._config)
            except Exception:  # noqa: BLE001
                pass
        try:
            data = resp.json()
            loc_data = data[0]["data"]["gtmData"]["locationData"]
            # Non-Brazil guard: filter out any non-Brazilian attraction
            if loc_data.get("countryId") != 294280:
                return None
            # city_geo_id: last non-empty element in locationHierarchy path
            hierarchy = loc_data.get("locationHierarchy", "")
            parts = [p for p in hierarchy.split(":") if p]
            try:
                city_geo_id = int(parts[-1]) if parts else 0
            except (ValueError, IndexError):
                city_geo_id = 0
            return {
                "location_id": location_id,
                "city_name": loc_data["cityName"],
                "state_name": loc_data["stateName"],
                "city_geo_id": city_geo_id,
                "state_geo_id": int(loc_data["stateId"]),
            }
        except (IndexError, KeyError, TypeError, ValueError):
            return None

    async def fetch_attractions_paginated(
        self, geo_id: int, start_page: int = 1, max_pages: int = _TA_MAX_PAGE
    ):
        """Stream attractions page-by-page over the HTML SSR transport (Phase 15).

        Separate transport from the single-page GraphQL ``fetch_attractions`` (which
        cannot paginate): each page is the server-rendered ``-oa{offset}-`` HTML
        variant. The embedded FlexCard JSON island is recovered by
        ``_extract_sections_from_html`` and fed to the UNCHANGED
        ``_parse_attractions_page`` (LGPD aggregate-only). Reuses the exact
        cookie/proxy/UA wiring and the 403/429 ``SessionExpiredError`` fail-fast
        from ``fetch_attractions``.

        The loop is CLAMPED to the 334-page / oa9990 hard cap (TA's 10000-result
        display ceiling) regardless of ``start_page + max_pages`` — a resumed full
        run never issues over-cap GETs (LOW-3 / T-15-04-04). Sleeps
        ``page_throttle_seconds`` between pages (never after the last).

        Args:
            geo_id: TripAdvisor integer geoId (294280 = all Brazil). MUST be an int
                — the URL builder is int-only (SSRF guard, T-15-04-02).
            start_page: 1-based page to start from (resume-from-offset support;
                page 1 = offset 0, page 2 = offset 30, ...).
            max_pages: Number of pages to attempt from start_page (clamped to cap).

        Yields:
            ``(offset, cards)`` tuples — one per page; ``offset = (page-1)*30``,
            ``cards`` is the normalized attraction-dict list.

        Raises:
            SessionMissingError: When no session is in Redis (operator gate).
            SessionExpiredError: On 403 or 429 HTTP status (no retry, stops).
            TypeError: When ``geo_id`` is not an int (raised before any GET).
        """
        # SSRF guard (T-15-04-02): the URL is built from a fixed template + int
        # geo_id + computed int offset. Reject a non-int geo_id BEFORE any GET so a
        # tampered value can never inject host/path. bool is an int subclass — reject it.
        if not isinstance(geo_id, int) or isinstance(geo_id, bool):
            raise TypeError(
                f"geo_id must be an int (SSRF guard); got {type(geo_id).__name__}"
            )

        from brave.lanes.tripadvisor.session import persist_rotated_cookies  # noqa: PLC0415

        session = self._get_session()
        cookies = session.get("cookies", {})
        user_agent = session.get("user_agent", "")
        # Browser-like Accept headers are REQUIRED for the HTML navigation surface:
        # DataDome 403s a User-Agent-only GET of the listing page (verified live
        # 2026-06-26). The XHR/GraphQL surface tolerates a bare UA; the SSR page does not.
        headers: dict[str, str] = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        }
        if user_agent:
            headers["User-Agent"] = user_agent
        # CR-02 / T-11-01-01: proxy_url routed but NEVER logged.
        proxy = self._config.proxy_url or None
        throttle = self._config.page_throttle_seconds

        # Clamp to the 334-page / oa9990 cap regardless of start_page + max_pages.
        start = max(1, start_page)
        last_page = min(start + max_pages, _TA_MAX_PAGE + 1)

        for page in range(start, last_page):
            offset = (page - 1) * 30
            if offset > _TA_MAX_OFFSET:
                break  # defensive — clamp already guarantees this
            url = _TA_HTML_URL.format(geo_id=geo_id, offset=offset)

            async with httpx.AsyncClient(
                cookies=cookies, follow_redirects=True, proxy=proxy
            ) as hc:
                resp = await hc.get(url, headers=headers)

            if resp.status_code in (403, 429):
                # T-15-04-01: log offset/status only — never cookies/UA/session/proxy.
                logger.warning(
                    "ta_paginated_session_expired",
                    offset=offset,
                    page=page,
                    status=resp.status_code,
                )
                raise SessionExpiredError(
                    f"TripAdvisor HTML returned {resp.status_code} — "
                    "DataDome session expired or blocked. Re-inject required."
                )

            resp.raise_for_status()
            # Write-back: merge rotated cookies into Redis session (260629-p2v).
            # Update local cookies var so next page uses the fresh jar.
            rotated = dict(resp.cookies)
            if rotated:
                cookies = {**cookies, **rotated}  # update local var for next page
                try:
                    persist_rotated_cookies(self._redis, rotated, self._config)
                except Exception:  # noqa: BLE001
                    pass  # best-effort guard
            sections = self._extract_sections_from_html(resp.text)
            cards = self._parse_attractions_page(sections)
            logger.info(
                "ta_paginated_page",
                offset=offset,
                page=page,
                card_count=len(cards),
            )
            yield offset, cards

            # Throttle between pages only — not after the final page.
            if throttle > 0 and page < last_page - 1:
                await asyncio.sleep(throttle)

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
