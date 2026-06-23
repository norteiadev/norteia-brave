"""TripAdvisor GraphQL hybrid client (TA-01).

Acquisition model:
  1. _bootstrap_session(): Playwright (lazy-imported, sync_playwright) launches
     headless Chromium, intercepts outbound GraphQL requests to capture live
     queryIds and DataDome session cookies.
  2. _get_session(): Returns cached session from Redis (brave:ta:session) or
     calls _bootstrap_session() on cache miss.
  3. fetch_destinations() / fetch_attractions(): Use httpx with session cookies
     to POST persisted queries. On 403/429 → raise SessionExpiredError.

Security notes:
  - T-11-01-01: config.proxy_url never emitted in structlog calls.
  - T-11-01-02: Session cookie jar cached in Redis with TTL; never logged.
  - T-11-01-03: Playwright lazy-imported — never reachable from API path.

Offline usage: inject NullTripAdvisorClient or FakeTripAdvisorClient instead.

Important:
  - Playwright is NOT imported at module top-level.
  - Import only happens inside _bootstrap_session().
  - Consumers in CI will never trigger the import path.
  - The scraper optional dep group must be installed separately:
    pip install 'norteia-brave[scraper]' && playwright install chromium
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from brave.config.settings import TripAdvisorConfig

logger = structlog.get_logger(__name__)

# Redis key for the cached session (cookie jar + queryId map)
# TTL is set to config.session_ttl (default 1800s / 30 min).
BRAVE_TA_SESSION_KEY: str = "brave:ta:session"

# GraphQL endpoint — all persisted-query POSTs target this URL
_TA_GRAPHQL_URL: str = "https://www.tripadvisor.com/data/graphql/ids"

# Safety guard: max pagination pages before stopping (prevents infinite loops, Risk A5)
_MAX_PAGES: int = 50


class SessionExpiredError(Exception):
    """Raised when a GraphQL request returns 403 or 429.

    Indicates the DataDome session cookies have expired or the queryId has
    rotated. Caller should trigger a re-bootstrap via _bootstrap_session().
    """


class TripAdvisorClient:
    """TripAdvisor GraphQL hybrid client (real implementation).

    Accepts TripAdvisorClientProtocol structurally — see _check_protocol_compliance().

    Constructor does NOT import Playwright; only _bootstrap_session() does,
    on first session cache miss.

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

    def _proxy_args(self) -> dict[str, str] | None:
        """Build Playwright proxy dict from config.proxy_url.

        Returns None (no proxy) when proxy_url is empty — safe default for dev.
        Never logs the proxy URL (T-11-01-01 security requirement).
        """
        if not self._config.proxy_url:
            return None
        # Log at debug level without including the URL value
        logger.debug("ta_client_proxy_configured", proxy="[redacted]")
        return {"server": self._config.proxy_url}

    def _bootstrap_session(self) -> dict[str, Any]:
        """Bootstrap a DataDome session via Playwright headless Chromium.

        Lazy-imports sync_playwright — only reachable from Celery sweep tasks,
        never from the API path or CI (T-11-01-03). Requires the scraper
        optional dep group: pip install 'norteia-brave[scraper]' && playwright install chromium

        Returns:
            Session dict:
                {
                    "cookies": [{"name": ..., "value": ..., "domain": ...}, ...],
                    "query_ids": {"destinations": "<queryId>", "attractions": "<queryId>"},
                }

        Raises:
            ImportError: When Playwright is not installed (scraper dep group missing).
            SessionExpiredError: When bootstrap fails to capture any queryId.
        """
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "Playwright is not installed. Install the scraper dep group: "
                "pip install 'norteia-brave[scraper]' then run playwright install chromium"
            ) from exc

        captured: list[dict[str, Any]] = []
        query_ids: dict[str, str] = {}

        def _on_request(request: Any) -> None:
            if "graphql/ids" in request.url and request.method == "POST":
                try:
                    body = json.loads(request.post_data or "{}")
                    captured.append(body)
                except Exception:
                    pass

        proxy_args = self._proxy_args()
        launch_kwargs: dict[str, Any] = {"headless": True}
        if proxy_args:
            launch_kwargs["proxy"] = proxy_args

        def _run_sync() -> list[dict[str, Any]]:
            """Run the sync_playwright bootstrap and return the captured cookies.

            Executed in a dedicated thread so the Playwright Sync API never runs
            inside a running asyncio loop. The async producers call this client
            via asyncio.run(produce(...)), which would otherwise trip Playwright's
            "Sync API inside the asyncio loop" guard. The Celery (sync) path is
            unaffected — it also gets a clean, loop-free thread.
            """
            with sync_playwright() as pw:
                browser = pw.chromium.launch(**launch_kwargs)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                )
                page = context.new_page()
                page.on("request", _on_request)

                # Navigate to a TA page to trigger GraphQL requests + DataDome cookies
                try:
                    page.goto(
                        "https://www.tripadvisor.com/Tourism-g303506-Rio_de_Janeiro_State_of_Rio_de_Janeiro-Vacations.html",
                        wait_until="networkidle",
                        timeout=30_000,
                    )
                except Exception:
                    # Page may timeout or redirect — captured requests may still be valid
                    pass

                # Extract cookies from the browser context (before close)
                browser_cookies = context.cookies()
                browser.close()
            return browser_cookies

        import concurrent.futures  # noqa: PLC0415

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
            cookies = _ex.submit(_run_sync).result()

        # Extract queryIds from intercepted requests (captured during page.goto)
        for body in captured:
            if isinstance(body, list):
                # Shape A: [{"query": queryId, "variables": {...}}]
                for item in body:
                    if isinstance(item, dict) and "query" in item:
                        qid = item["query"]
                        vars_ = item.get("variables", {})
                        if "locationId" in vars_ or "geoId" in vars_:
                            # Heuristic: distinguish destinations vs attractions
                            if "ATTRACTION" in str(vars_).upper():
                                query_ids.setdefault("attractions", qid)
                            else:
                                query_ids.setdefault("destinations", qid)
            elif isinstance(body, dict) and "extensions" in body:
                # Shape B: {"extensions": {"persistedQuery": {"sha256Hash": ...}}}
                sha = (
                    body.get("extensions", {})
                    .get("persistedQuery", {})
                    .get("sha256Hash", "")
                )
                if sha:
                    query_ids.setdefault("destinations", sha)

        # Apply config overrides (escape hatch for queryId rotation)
        query_ids.update(self._config.query_id_override)

        session: dict[str, Any] = {
            "cookies": cookies,
            "query_ids": query_ids,
        }

        # Cache in Redis — never log the cookie values (T-11-01-02)
        self._redis.setex(
            BRAVE_TA_SESSION_KEY,
            self._config.session_ttl,
            json.dumps(session),
        )
        logger.info(
            "ta_session_bootstrapped",
            query_ids_captured=list(query_ids.keys()),
            cookie_count=len(cookies),
        )
        return session

    def _get_session(self) -> dict[str, Any]:
        """Return the cached session or bootstrap a new one.

        Returns:
            Session dict with 'cookies' and 'query_ids' keys.
        """
        raw = self._redis.get(BRAVE_TA_SESSION_KEY)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        return self._bootstrap_session()

    # ------------------------------------------------------------------
    # Public protocol methods
    # ------------------------------------------------------------------

    async def fetch_destinations(self, uf: str) -> list[dict[str, Any]]:
        """Fetch TripAdvisor destinations (GEO entities) for a Brazilian UF.

        Uses the cached session cookies and queryId to POST a persisted GraphQL
        query. Raises SessionExpiredError on 403/429.

        Args:
            uf: Two-letter Brazilian state code.

        Returns:
            List of location dicts from the GraphQL response.

        Raises:
            SessionExpiredError: On 403 or 429 HTTP status (DataDome block / rate limit).
        """
        from brave.lanes.tripadvisor.geo import resolve_geo_id  # noqa: PLC0415

        geo_id = resolve_geo_id(uf, self._redis, self._config)
        session = self._get_session()
        query_id = session.get("query_ids", {}).get("destinations", "")
        cookies = {c["name"]: c["value"] for c in session.get("cookies", [])}

        results: list[dict[str, Any]] = []
        for page_num in range(_MAX_PAGES):
            offset = page_num * 20
            payload = [
                {
                    "query": query_id,
                    "variables": {"locationId": geo_id, "offset": offset, "limit": 20},
                }
            ]
            async with httpx.AsyncClient(cookies=cookies, follow_redirects=True) as hc:
                resp = await hc.post(
                    _TA_GRAPHQL_URL,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

            if resp.status_code in (403, 429):
                raise SessionExpiredError(
                    f"TripAdvisor GraphQL returned {resp.status_code} — "
                    "DataDome session expired or queryId rotated. Re-bootstrap required."
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
        self, geo_id: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Fetch TripAdvisor attractions (ATTRACTION entities) for a geoId.

        Args:
            geo_id: TripAdvisor integer geoId.
            offset: Pagination offset (0, 20, 40, ...).

        Returns:
            List of attraction dicts from the GraphQL response.

        Raises:
            SessionExpiredError: On 403 or 429 HTTP status.
        """
        session = self._get_session()
        query_id = session.get("query_ids", {}).get("attractions", "")
        cookies = {c["name"]: c["value"] for c in session.get("cookies", [])}

        payload = [
            {
                "query": query_id,
                "variables": {"locationId": geo_id, "offset": offset, "limit": 20},
            }
        ]
        async with httpx.AsyncClient(cookies=cookies, follow_redirects=True) as hc:
            resp = await hc.post(
                _TA_GRAPHQL_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        if resp.status_code in (403, 429):
            raise SessionExpiredError(
                f"TripAdvisor GraphQL returned {resp.status_code} — "
                "DataDome session expired or queryId rotated. Re-bootstrap required."
            )

        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0].get("data", {}).get("attractions", []) or []
        elif isinstance(data, dict):
            return data.get("data", {}).get("attractions", []) or []
        return []

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
