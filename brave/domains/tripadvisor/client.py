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
import contextlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

import httpx
import structlog

from brave.shared.exceptions import (
    SourceRateLimitError,
    SourceRecordError,
    SourceSchemaError,
    SourceSessionError,
    TransientError,
)

if TYPE_CHECKING:
    from brave.config.settings import TripAdvisorConfig

logger = structlog.get_logger(__name__)

# Redis key for the cached session (cookie jar + queryId map)
# TTL is set by the injection endpoint (config.session_ttl, default 1800s / 30 min).
BRAVE_TA_SESSION_KEY: str = "brave:ta:session"

# GraphQL endpoint — all persisted-query POSTs target this URL. The .com.br TLD is
# what makes TripAdvisor return pt-BR attraction NAMES (verified live: ".com" →
# "Camburi Beach"/"...Convent"; ".com.br" → "Praia De Camburi"/"Convento Nossa
# Senhora da Penha"). The Accept-Language header alone does NOT localize the listing;
# the TLD does. Session cookies (domain=.tripadvisor.com) are passed explicitly and
# work on .com.br too — and .com.br matches the operator's Copy-as-cURL capture domain.
_TA_GRAPHQL_URL: str = "https://www.tripadvisor.com.br/data/graphql/ids"

# HTML SSR listing page (Phase 15 pagination transport). The GraphQL listing query
# cannot paginate, so each page is fetched as the server-rendered -oa{offset}- HTML
# variant. URL = fixed template + int geo_id + computed int offset (SSRF-safe,
# T-15-04-02). offset = (page-1)*30.
_TA_HTML_URL: str = (
    "https://www.tripadvisor.com/Attractions-g{geo_id}-Activities-"
    "a_allAttractions.true-oa{offset}-Brazil.html"
)

# Attraction detail page: g1 works for any attraction (TA redirects to the canonical URL).
_TA_ATTRACTION_URL: str = (
    "https://www.tripadvisor.com.br/Attraction_Review-g1-d{location_id}-Reviews.html"
)
_JSON_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
# Pages in a row without the LocalBusiness block → systemic (TA dropped it), not per-card.
_PAGE_ERRORS_SYSTEMIC = 5

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

# AttractionsFusion GraphQL paginated-listing persisted query id (POC-confirmed
# live against real TripAdvisor — see docs/poc/ta-graphql-pagination.md). The
# GraphQL listing DOES paginate via request.routeParameters.pagee, so this
# REPLACES the single-page fetch_attractions AND the fragile HTML-SSR path.
# Captured live 2026-10-19 — the old 79aaeeb847e55e58 started answering
# PersistedQueryNotFound. TA rotates these: BRAVE_TA_QUERY_ID_OVERRIDE={"listing":"<qid>"}
# swaps it without a deploy (capture: listing page › next page › the /data/graphql/ids
# item whose variables carry request.routeParameters.pagee).
_LISTING_QID_GQL: str = "0e26761de5110a17"

# Review-list persisted query id: powers reliability review recency (atualidade + precise
# corroboração) via fetch_recent_review. Captured live from the attraction detail page
# 2026-10-17 — the old ef1a9f94012220d3 started answering PersistedQueryNotFound.
_REVIEWS_QID: str = "1eeac11193438f8a"
# Reviews GraphQL errors on this many cards in a row → systemic (abort), not per-card.
_REVIEW_ERRORS_SYSTEMIC = 5

# Locale headers so TripAdvisor returns pt-BR attraction names (else it leaks English
# names like "Camburi Beach"/"...Convent"). The HTML-SSR path already sends this; the
# GraphQL surfaces must too. Paired with currency=BRL / unitLength=KILOMETERS in the
# listing variables (pt-BR context signals).
_TA_ACCEPT_LANGUAGE: str = "pt-BR,pt;q=0.9,en;q=0.8"

# HTTP 429 is throttling, not a dead session: retry the SAME request this many times with
# exponential backoff (honoring Retry-After, capped), then raise SourceRateLimitError so the
# task backs off and resumes — no needs_bootstrap, no engine OFF. A 429 that carries a
# DataDome captcha challenge is a blocked session and stays SessionExpiredError, like 403.
_RATE_LIMIT_RETRIES: int = 2
_RATE_LIMIT_MAX_SLEEP_SECONDS: float = 60.0
_RATE_LIMIT_DEFAULT_RETRY_AFTER: float = 60.0


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """The 429's Retry-After hint in seconds, or None (absent / HTTP-date form)."""
    try:
        return max(float(resp.headers.get("Retry-After", "")), 0.0)
    except ValueError:
        return None


def _is_datadome_challenge(resp: httpx.Response) -> bool:
    """A captcha wall (blocked session), not plain throttling."""
    return "x-datadome" in resp.headers or "captcha-delivery.com" in resp.text


def _graphql_errors(data: Any) -> list[str] | None:
    """Messages of a GraphQL ``errors`` envelope (``[{"errors": [...]}]``), else None."""
    first = data[0] if isinstance(data, list) and data else data
    errors = first.get("errors") if isinstance(first, dict) else None
    if not errors:
        return None
    return [str(e.get("message") if isinstance(e, dict) else e) for e in errors]


def _json_ld_blocks(html: str) -> list[Any]:
    """Every parseable JSON-LD block of a page; a malformed one is skipped."""
    blocks = []
    for raw in _JSON_LD.findall(html):
        with contextlib.suppress(ValueError):
            blocks.append(json.loads(raw))
    return blocks


def _num(value: Any) -> float | None:
    """A JSON-LD coordinate (number or numeric string) as float, else None."""
    if isinstance(value, bool):
        return None
    try:
        return float(value) if isinstance(value, int | float | str) else None
    except ValueError:
        return None


class SessionExpiredError(SourceSessionError):
    """Raised when a request returns 403 (or a 429 carrying a DataDome captcha).

    A plain 429 is throttling → SourceRateLimitError (brave/shared/exceptions.py), never this.

    Indicates the DataDome session cookies have expired or the queryId has
    rotated. Operator must re-inject a fresh session.

    Subclasses SourceSessionError (brave/shared/exceptions.py) so it joins the
    central BraveError hierarchy. The class object is unchanged and still raised
    here, so every existing ``except SessionExpiredError`` / isinstance check —
    including the multi-exception tuples in pipeline.py and tripadvisor_session.py
    — keeps working unchanged.
    """


class SessionMissingError(SourceSessionError):
    """Raised when BRAVE_TA_SESSION_KEY is absent from Redis.

    Operator must run scripts/ta_bootstrap.py --endpoint <URL> to inject a
    session before sweeping. The injection endpoint validates the session
    (canary check) before writing to Redis.

    Subclasses SourceSessionError (brave/shared/exceptions.py) — see
    SessionExpiredError for the compatibility rationale.
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

    def __init__(self, config: TripAdvisorConfig, redis: Any) -> None:
        self._config = config
        self._redis = redis
        self._hc: httpx.AsyncClient | None = None
        self._review_errors_in_a_row = 0  # per-card reviews errors; a run of them is systemic
        self._page_errors_in_a_row = 0  # attraction pages without the block; same rule

    # ------------------------------------------------------------------
    # Persistent HTTP client (opt-in: ``async with ta_client:`` around a sweep)
    # ------------------------------------------------------------------

    async def __aenter__(self) -> TripAdvisorClient:
        self._hc = httpx.AsyncClient(
            follow_redirects=True, proxy=self._config.proxy_url or None
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        hc, self._hc = self._hc, None
        if hc is not None:
            await hc.aclose()

    @asynccontextmanager
    async def _http(
        self, cookies: dict[str, str], proxy: str | None
    ) -> AsyncIterator[httpx.AsyncClient]:
        """Yield the HTTP client for one request.

        Inside ``async with self`` the shared client is reused (one TLS/proxy
        connection for the whole sweep). Its jar is reset to exactly ``cookies``
        before each request, so what goes on the wire matches a fresh
        ``AsyncClient(cookies=cookies)``. Outside it, a one-shot client as before.
        """
        if self._hc is None:
            async with httpx.AsyncClient(
                cookies=cookies, follow_redirects=True, proxy=proxy
            ) as hc:
                yield hc
            return
        self._hc.cookies.clear()
        self._hc.cookies.update(cookies)
        yield self._hc

    async def _send(
        self,
        method: str,
        url: str,
        cookies: dict[str, str],
        proxy: str | None,
        what: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """One TA request with the shared status handling of every transport.

        403 (or a DataDome-captcha 429) → SessionExpiredError. A plain 429 is retried with
        exponential backoff, then → SourceRateLimitError. Any other non-2xx →
        httpx.HTTPStatusError. On 2xx the response cookies are written back AND the session
        TTL slides even when nothing rotated, so a long sweep cannot outlive its Redis key
        while the session is in active use.
        """
        from brave.domains.tripadvisor.session import persist_rotated_cookies  # noqa: PLC0415

        base = max(self._config.page_throttle_seconds, 1.0)
        for attempt in range(_RATE_LIMIT_RETRIES + 1):
            async with self._http(cookies, proxy) as hc:
                resp = await getattr(hc, method)(url, **kwargs)
            if resp.status_code == 429 and not _is_datadome_challenge(resp):
                hint = _retry_after_seconds(resp)
                if attempt < _RATE_LIMIT_RETRIES:
                    await asyncio.sleep(
                        min(max(hint or 0.0, base * 2**attempt), _RATE_LIMIT_MAX_SLEEP_SECONDS)
                    )
                    continue
                # T-15-04-01: status/attempts only — never cookies/UA/session/proxy.
                logger.warning("ta_rate_limited", what=what, attempts=attempt + 1)
                raise SourceRateLimitError(
                    f"TripAdvisor {what} returned 429 after {attempt + 1} attempts",
                    retry_after=hint or _RATE_LIMIT_DEFAULT_RETRY_AFTER,
                )
            if resp.status_code in (403, 429):
                raise SessionExpiredError(
                    f"TripAdvisor {what} returned {resp.status_code} — "
                    "DataDome session expired or queryId rotated. Re-inject required."
                )
            resp.raise_for_status()
            # Best-effort (it already swallows internally): a write-back error never aborts.
            with contextlib.suppress(Exception):
                persist_rotated_cookies(self._redis, dict(resp.cookies), self._config)
            return resp
        raise AssertionError("unreachable")  # pragma: no cover

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
        from brave.domains.tripadvisor.geo import resolve_geo_id  # noqa: PLC0415

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
            resp = await self._send(
                "post", _TA_GRAPHQL_URL, cookies, proxy, "GraphQL", json=payload, headers=headers
            )
            # Write-back to Redis happened in _send (260629-p2v); keep the local jar fresh.
            cookies = {**cookies, **dict(resp.cookies)}
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

        DEPRECATED (kept for back-compat; NO LONGER used by the atrativos lane —
        superseded by fetch_attractions_paginated_gql, which paginates via the same
        AttractionsFusion query). Existing tests keep this method green; do not call
        it from new code.

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

        def _build_payload() -> list:
            pageview_uid = str(uuid.uuid4())
            return [
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

        # TRANSIENT-RETRY (260701-has): AttractionsFusion intermittently returns
        # HTTP 200 with Result[0].status.success == false, totalResults == 0 and
        # sections == [] for a VALID geoId; retrying the identical request succeeds.
        # A single wrong drop previously silenced an entire UF. Retries are bounded
        # by attractions_transient_max_retries (T-has-01) — a persistently-failing
        # geo returns [] after max_retries+1 calls, never an unbounded loop.
        # CRITICAL: status ABSENT (or success true) is NOT transient — it must fall
        # straight through to real-empty so a genuinely-empty geo returns [] in ONE
        # call (existing empty-sections contract).
        max_retries = self._config.attractions_transient_max_retries
        for attempt in range(max_retries + 1):
            payload = _build_payload()  # regenerate pageview_uid per attempt
            resp = await self._send(
                "post", _TA_GRAPHQL_URL, cookies, proxy, "GraphQL", json=payload, headers=headers
            )
            data = resp.json()

            # Safe extraction of Result[0] from the real response path.
            result0: dict = {}
            try:
                result0 = data[0]["data"]["Result"][0]
            except (IndexError, KeyError, TypeError):
                result0 = {}

            # Transient ONLY when status is a dict with success is False. Status
            # absent / None / success true → real result (fall through below).
            status = result0.get("status") if isinstance(result0, dict) else None
            if (
                isinstance(status, dict)
                and status.get("success") is False
                and attempt < max_retries
            ):
                await asyncio.sleep(
                    self._config.attractions_transient_retry_sleep_seconds
                )
                continue

            # Real result (or retries exhausted): extract sections.
            sections = (
                result0.get("sections") if isinstance(result0, dict) else None
            ) or []
            if not sections:
                return []  # Real-empty geo or exhausted transient → no attractions.

            # Single page only — no partial-page break (there is no second page to
            # gate, so card count never terminates a loop).
            return self._parse_attractions_page(sections)

        return []  # Unreachable safety net (loop always returns).

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
        resp = await self._send(
            "post", _TA_GRAPHQL_URL, cookies, proxy, "detail", json=payload, headers=headers
        )
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
        resp = await self._send(
            "post", _TA_GRAPHQL_URL, cookies, proxy, "geo", json=payload, headers=headers
        )
        try:
            data = resp.json()
        except ValueError:
            data = None  # a 200 that is not JSON (challenge page, proxy error) — see below
        errors = _graphql_errors(data)
        if errors:
            # A retired qid fails for EVERY attraction — systemic, not "this card has no geo".
            logger.error("ta.geo.graphql_error", errors=errors)
            raise SourceSchemaError(f"TripAdvisor geo query failed: {errors}")
        try:
            loc_data = data[0]["data"]["gtmData"]["locationData"]  # type: ignore[index]
        except (IndexError, KeyError, TypeError):
            loc_data = None
        if not isinstance(loc_data, dict):
            # Non-JSON or a renamed gtmData/locationData is a shape change that fails for
            # EVERY card: returning None let the backfill stamp ta_backfilled_at on all of
            # them with the bulk's provisional UF, for good. Abort instead (C1).
            logger.error("ta.geo.schema_error", location_id=location_id)
            raise SourceSchemaError("TripAdvisor geo query answered in an unknown shape")
        # Non-Brazil guard: None only for a real, different integer countryId. A missing or
        # retyped field (rename, "294280" as a string) is a shape change for EVERY card —
        # read as "abroad" it stamped ta_backfilled_at on all of them with the bulk's
        # provisional UF (C1). A lone odd card is skipped by the backfill, not fatal.
        country_id = loc_data.get("countryId")
        if not isinstance(country_id, int) or isinstance(country_id, bool):
            logger.error("ta.geo.schema_error", location_id=location_id, field="countryId")
            raise SourceSchemaError("TripAdvisor geo answered without an integer countryId")
        if country_id != 294280:
            return None
        # city_geo_id: last non-empty element in locationHierarchy path
        hierarchy = loc_data.get("locationHierarchy") or ""
        parts = [p for p in hierarchy.split(":") if p]
        try:
            city_geo_id = int(parts[-1]) if parts else 0
        except (ValueError, IndexError):
            city_geo_id = 0
        try:
            state_geo_id = int(loc_data.get("stateId") or 0)
        except (TypeError, ValueError):
            state_geo_id = 0
        # cityName/stateName may legitimately be null (TA often names only a region).
        return {
            "location_id": location_id,
            "city_name": loc_data.get("cityName"),
            "state_name": loc_data.get("stateName"),
            "city_geo_id": city_geo_id,
            "state_geo_id": state_geo_id,
        }

    async def fetch_attraction_address(self, location_id: int) -> dict[str, Any]:
        """TA's own address block for one attraction: the LocalBusiness JSON-LD of its page.

        Returns {city_name, state_name, lat, lng, address, phone, website} (any may be None;
        city_name/state_name = raw addressLocality/addressRegion, state_name_to_uf maps
        them), or {} when TA has no such attraction page (404/410, redirect off
        Attraction_Review — a removed listing lands on its city page) or the address is not
        in Brazil: conclusive, no streak.
        LGPD: only these keys are read; aggregateRating/reviews/reviewer data never touched
        or logged.
        Raises: TypeError (non-int id, before any GET), SessionMissingError,
        SessionExpiredError (403/DataDome 429 via _send, or a 200 captcha page),
        SourceRateLimitError, SourceRecordError (an attraction page without the block —
        this card), SourceSchemaError (_PAGE_ERRORS_SYSTEMIC such pages in a row).
        """
        # SSRF guard: the URL is a fixed template + an int id. bool is an int subclass.
        if not isinstance(location_id, int) or isinstance(location_id, bool):
            raise TypeError(
                f"location_id must be an int (SSRF guard); got {type(location_id).__name__}"
            )
        session = self._get_session()
        cookies = session.get("cookies", {})
        user_agent = session.get("user_agent", "")
        # Browser-like Accept headers: DataDome 403s a UA-only GET of an HTML page.
        headers: dict[str, str] = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": _TA_ACCEPT_LANGUAGE,
        }
        if user_agent:
            headers["User-Agent"] = user_agent
        proxy = self._config.proxy_url or None
        url = _TA_ATTRACTION_URL.format(location_id=location_id)
        try:
            resp = await self._send("get", url, cookies, proxy, "attraction page", headers=headers)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                return {}
            raise
        ld = next(
            (b for b in _json_ld_blocks(resp.text)
             if isinstance(b, dict) and b.get("@type") == "LocalBusiness"),
            None,
        )
        if ld is None:
            if "captcha-delivery.com" in resp.text:
                raise SessionExpiredError("TripAdvisor attraction page answered a captcha")
            if "Attraction_Review" not in str(resp.url):
                return {}
            self._page_errors_in_a_row += 1
            if self._page_errors_in_a_row >= _PAGE_ERRORS_SYSTEMIC:
                logger.error("ta.page.schema_error")
                raise SourceSchemaError("TripAdvisor attraction pages lost their address block")
            logger.warning("ta.page.record_error", location_id=location_id)
            raise SourceRecordError(f"TripAdvisor page of location {location_id} has no address")
        self._page_errors_in_a_row = 0
        addr = ld["address"] if isinstance(ld.get("address"), dict) else {}
        geo = ld["geo"] if isinstance(ld.get("geo"), dict) else {}
        if addr.get("addressCountry") and addr["addressCountry"] != "BR":
            return {}
        street = (addr.get("streetAddress") or "").strip()
        address = (
            ", ".join(p for p in (street, addr.get("addressLocality"), addr.get("postalCode")) if p)
            if street
            else None
        )
        phone = ld.get("telephone")
        same_as = ld.get("sameAs")
        website = None
        if isinstance(same_as, str):
            if same_as.startswith("http"):
                website = same_as
            elif same_as.startswith("www."):
                website = f"https://{same_as}"
        return {
            "city_name": addr.get("addressLocality"),
            "state_name": addr.get("addressRegion"),
            "lat": _num(geo.get("latitude")),
            "lng": _num(geo.get("longitude")),
            "address": address,
            "phone": phone if isinstance(phone, str) and phone else None,
            "website": website,
        }

    async def fetch_attractions_paginated(
        self, geo_id: int, start_page: int = 1, max_pages: int = _TA_MAX_PAGE
    ):
        """Stream attractions page-by-page over the HTML SSR transport (Phase 15).

        DEPRECATED (kept for back-compat; NO LONGER used by the atrativos lane —
        superseded by fetch_attractions_paginated_gql, which paginates the same
        AttractionsFusion query over the GraphQL transport, removing the fragile
        HTML-SSR island extraction). Existing tests keep this method green.

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

            resp = await self._send("get", url, cookies, proxy, "HTML", headers=headers)
            # Write-back happened in _send (260629-p2v); next page uses the fresh jar.
            cookies = {**cookies, **dict(resp.cookies)}
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

    async def fetch_attractions_paginated_gql(
        self, geo_id: int, start_page: int = 1, max_pages: int = _TA_MAX_PAGE
    ) -> AsyncIterator[tuple[int, list[dict]]]:
        """Stream AttractionsFusion attractions page-by-page over GraphQL (POC-confirmed).

        REPLACES both the single-page ``fetch_attractions`` and the fragile HTML-SSR
        ``fetch_attractions_paginated``: the AttractionsFusion persisted query
        (qid ``_LISTING_QID_GQL``) DOES paginate via ``request.routeParameters.pagee``.
        Each page POSTs the same batch-array shape as ``fetch_attractions`` with an
        explicit ``pagee=str(offset)`` (offset = (page-1)*30). Page 1 MUST send
        ``pagee="0"`` — an absent ``pagee`` returns 0 cards (POC finding). Parses each
        page with the UNCHANGED ``_parse_attractions_page`` (same FlexCard shape,
        LGPD aggregate-only) and yields ``(offset, cards)``.

        Reuses the exact cookie/UA/proxy wiring, ``persist_rotated_cookies``
        write-back and 403/429 → ``SessionExpiredError`` fail-fast of the other
        transports. Throttles ``page_throttle_seconds`` between pages (never after
        the last). The loop is CLAMPED to the 334-page / oa9990 cap regardless of
        ``start_page + max_pages``.

        Args:
            geo_id: TripAdvisor integer geoId (294280 = all Brazil). MUST be an int
                (SSRF guard, mirrors ``fetch_attractions_paginated``).
            start_page: 1-based page to start from (resume support; page 1 = offset 0).
            max_pages: Number of pages to attempt from start_page (clamped to cap).

        Yields:
            ``(offset, cards)`` per page — ``offset = (page-1)*30``, ``cards`` the
            normalized attraction-dict list. STOPS when a status-ok page yields 0 cards
            or the offset reaches the reported ``totalResults``.

        Raises:
            SessionMissingError: When no session is in Redis (operator gate).
            SessionExpiredError: On 403 / DataDome-captcha 429 (no retry, stops).
            SourceRateLimitError: On a 429 that outlived the in-client backoff.
            SourceSchemaError: On a GraphQL ``errors`` envelope / unreadable Result.
            TransientError: When a page keeps soft-failing (status.success == false).
            TypeError: When ``geo_id`` is not an int (raised before any POST).
        """
        # SSRF guard (mirror fetch_attractions_paginated): reject a non-int geo_id
        # BEFORE any request. bool is an int subclass — reject it too.
        if not isinstance(geo_id, int) or isinstance(geo_id, bool):
            raise TypeError(
                f"geo_id must be an int (SSRF guard); got {type(geo_id).__name__}"
            )

        session = self._get_session()
        # session_id is a TASID cookie value — NEVER logged (T-13-01-01).
        session_id: str = session.get("session_id", "")
        cookies = session.get("cookies", {})
        user_agent = session.get("user_agent", "")
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept-Language": _TA_ACCEPT_LANGUAGE,  # pt-BR attraction names
        }
        if user_agent:
            headers["User-Agent"] = user_agent
        # CR-02 / T-11-01-01: proxy routed but NEVER logged.
        proxy = self._config.proxy_url or None
        throttle = self._config.page_throttle_seconds

        def _build_payload(offset: int) -> list:
            pageview_uid = str(uuid.uuid4())
            return [
                {
                    "variables": {
                        "request": {
                            "tracking": {
                                "screenName": "AttractionsFusion",
                                "pageviewUid": pageview_uid,
                            },
                            "routeParameters": {
                                "geoId": geo_id,
                                "filters": [],
                                "contentType": "attraction",
                                "webVariant": "AttractionsFusion",
                                # Page 1 sends pagee="0" — MUST be explicit (absent → 0 cards).
                                "pagee": str(offset),
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
                        "unitLength": "KILOMETERS",
                        "currency": "BRL",
                        "currentGeoPoint": None,
                        "mapSurface": False,
                        "debug": False,
                        "polling": False,
                    },
                    "extensions": {
                        "preRegisteredQueryId": self._config.query_id_override.get("listing")
                        or _LISTING_QID_GQL
                    },
                }
            ]

        # Clamp to the 334-page / oa9990 cap regardless of start_page + max_pages.
        start = max(1, start_page)
        last_page = min(start + max_pages, _TA_MAX_PAGE + 1)

        max_retries = self._config.attractions_transient_max_retries
        total_results: int | None = None  # TA's listing size, once a page reported it
        for page in range(start, last_page):
            offset = (page - 1) * 30
            if offset > _TA_MAX_OFFSET:
                break  # defensive — clamp already guarantees this
            if total_results is not None and offset >= total_results:
                break  # past the listing's end — no request needed

            # Only a status-ok page with no cards ends the listing. A GraphQL `errors`
            # envelope (retired qid) or an unreadable Result is systemic → SourceSchemaError;
            # the intermittent soft-fail (200 + status.success == false, 260701-has) is retried
            # like fetch_attractions, then → TransientError. Breaking on either used to end a
            # 334-page bulk at page N as "done", silently dropping every page after it.
            for attempt in range(max_retries + 1):
                payload = _build_payload(offset)  # fresh pageviewUid per attempt
                resp = await self._send(
                    "post", _TA_GRAPHQL_URL, cookies, proxy, "GraphQL",
                    json=payload, headers=headers,
                )
                # Write-back happened in _send (260629-p2v); keep the local jar fresh.
                cookies = {**cookies, **dict(resp.cookies)}
                data = resp.json()
                errors = _graphql_errors(data)
                if errors:
                    logger.error("ta.listing.graphql_error", offset=offset, errors=errors)
                    raise SourceSchemaError(f"TripAdvisor listing query failed: {errors}")
                try:
                    result0 = data[0]["data"]["Result"][0]
                except (IndexError, KeyError, TypeError) as exc:
                    raise SourceSchemaError(
                        f"TripAdvisor listing response has no Result (offset={offset})"
                    ) from exc
                status = result0.get("status") if isinstance(result0, dict) else None
                if not (isinstance(status, dict) and status.get("success") is False):
                    break
                if attempt == max_retries:
                    raise TransientError(
                        f"TripAdvisor listing soft-failed {attempt + 1}x at offset={offset}"
                    )
                await asyncio.sleep(self._config.attractions_transient_retry_sleep_seconds)
            sections = (result0.get("sections") if isinstance(result0, dict) else None) or []
            reported_total = result0.get("totalResults") if isinstance(result0, dict) else None
            if isinstance(reported_total, int) and reported_total > 0:
                total_results = reported_total
            cards = self._parse_attractions_page(sections)
            logger.info(
                "ta_paginated_gql_page",
                offset=offset,
                page=page,
                card_count=len(cards),
            )

            if not cards:
                break  # STOP when a page yields 0 cards (end of listing).
            yield offset, cards

            # Throttle between pages only — never after the final page.
            if throttle > 0 and page < last_page - 1:
                await asyncio.sleep(throttle)

    async def fetch_recent_review(self, location_id: int) -> dict | None:
        """Fetch review recency for one attraction (qid 1eeac11193438f8a).

        Powers ``atualidade_from_recency`` (review recency lifts the score) plus a
        precise corroboração review_count. ``sortBy=MOST_RECENT`` with no language
        filter, so ``reviews[0]`` is the newest review in ANY language and
        ``totalCount`` matches the listing card's count.

        LGPD (CRITICAL): reads ONLY ``totalCount`` + ``reviews[0].submittedDateTime``.
        NEVER reads/logs/returns review text, title, username, userProfile, photos,
        or reviewTip.

        ``rating`` is always None: the only rating here is the newest review's own
        stars, not the attraction aggregate — callers keep the card's aggregate.

        Args:
            location_id: TripAdvisor integer locationId of the attraction.

        Returns:
            ``{"review_count": int, "rating": None, "most_recent_review_at":
            datetime}`` on success, or ``None`` on empty reviews / a per-card parse error.

        Raises:
            SessionMissingError: When no session is in Redis (operator gate).
            SessionExpiredError: On 403 / DataDome-captcha 429.
            SourceRateLimitError: On a 429 that outlived the in-client backoff.
            SourceSchemaError: On a GraphQL ``errors`` envelope (e.g. a retired qid →
                PersistedQueryNotFound) or a response without ``data`` — systemic.
        """

        session = self._get_session()
        cookies = session.get("cookies", {})
        user_agent = session.get("user_agent", "")
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept-Language": _TA_ACCEPT_LANGUAGE,
        }
        if user_agent:
            headers["User-Agent"] = user_agent
        proxy = self._config.proxy_url or None
        payload = [
            {
                "variables": {
                    "clientId": "ATTRACTION_DETAIL_LIST",
                    "locationId": location_id,
                    "sortBy": "MOST_RECENT",
                    "limit": 1,
                    "offset": 0,
                    "locale": "pt-BR",
                },
                "extensions": {"preRegisteredQueryId": _REVIEWS_QID},
            }
        ]
        resp = await self._send(
            "post", _TA_GRAPHQL_URL, cookies, proxy, "reviews", json=payload, headers=headers
        )

        try:
            data = resp.json()
        except ValueError:
            data = None  # a non-JSON 200 is no answer either: abort, never "no reviews"
        errors = _graphql_errors(data)
        if errors and not any("PersistedQueryNotFound" in e for e in errors):
            # One location TA's resolver chokes on ("Unexpected error.", "Error #939088493:
            # Trace ID: n/a ...") while the cards around it answer: that card only. A retired
            # qid says PersistedQueryNotFound and aborts below; so does a streak of errors.
            self._review_errors_in_a_row += 1
            if self._review_errors_in_a_row < _REVIEW_ERRORS_SYSTEMIC:
                logger.warning(
                    "ta.reviews.record_error", qid=_REVIEWS_QID, location_id=location_id,
                    errors=errors,
                )
                raise SourceRecordError(f"TripAdvisor reviews failed for location {location_id}")
        if errors or not (isinstance(data, list) and data and isinstance(data[0], dict)
                          and "data" in data[0]):
            # A retired qid / schema change fails for EVERY card. Returning None stored each
            # record with no recency for good (the record looked synced) — abort instead.
            logger.error("ta.reviews.graphql_error", qid=_REVIEWS_QID, errors=errors)
            raise SourceSchemaError(f"TripAdvisor reviews query failed (qid {_REVIEWS_QID})")
        try:
            result = data[0]["data"]["ReviewsProxy_getPublishedReviewsForLocation"][0]["result"]
            reviews = result["reviews"]
        except (IndexError, KeyError, TypeError):
            reviews = None
        self._review_errors_in_a_row = 0
        if not isinstance(reviews, list):
            # A renamed operation/result/reviews answers like "no reviews" for EVERY card:
            # each record was stamped TA-complete with no recency (C1). Only an explicit
            # empty list is "conclusively none".
            logger.error("ta.reviews.schema_error", qid=_REVIEWS_QID, location_id=location_id)
            raise SourceSchemaError(
                f"TripAdvisor reviews answered in an unknown shape (qid {_REVIEWS_QID})"
            )
        if not reviews:
            return None
        try:
            # LGPD: touch ONLY submittedDateTime (+ totalCount) — no PII fields.
            most_recent_at = datetime.fromisoformat(
                reviews[0]["submittedDateTime"].replace("Z", "+00:00")
            ).astimezone(UTC)
            return {
                "review_count": int(result["totalCount"]),
                "rating": None,
                "most_recent_review_at": most_recent_at,
            }
        except (IndexError, KeyError, TypeError, ValueError, AttributeError):
            return None

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
        from brave.domains.tripadvisor.geo import resolve_geo_id  # noqa: PLC0415

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
