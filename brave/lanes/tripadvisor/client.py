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
  }

Backwards compatibility: Phase 11 stored cookies as a list of
  {"name": ..., "value": ..., "domain": ...} dicts.
_get_session() normalises both shapes to a flat dict.

Security notes:
  - T-11-01-01: config.proxy_url never emitted in structlog calls.
  - T-11-01-02: Session cookie jar cached in Redis with TTL; never logged.

Offline usage: inject NullTripAdvisorClient or FakeTripAdvisorClient instead.
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
        self, geo_id: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Fetch TripAdvisor attractions (ATTRACTION entities) for a geoId.

        Args:
            geo_id: TripAdvisor integer geoId.
            offset: Pagination offset (0, 20, 40, ...).

        Returns:
            List of attraction dicts from the GraphQL response.

        Raises:
            SessionMissingError: When no session is in Redis (operator gate).
            SessionExpiredError: On 403 or 429 HTTP status.
        """
        session = self._get_session()
        query_id = session.get("query_ids", {}).get("attractions", "")
        cookies = session.get("cookies", {})
        user_agent = session.get("user_agent", "")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if user_agent:
            headers["User-Agent"] = user_agent

        # CR-02: route through the configured residential proxy (BRAVE_TA_PROXY_URL).
        proxy = self._config.proxy_url or None

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
