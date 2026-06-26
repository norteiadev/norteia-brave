"""NominatimGeocoderClient — real async httpx geocoder (TA-14).

Uses OpenStreetMap Nominatim to forward-geocode attraction names to
lat/lon + município name for IBGE matching (TA-15 geo-enrichment path).

LGPD (decision #8, 14-CONTEXT.md):
  Only 4 keys are persisted/returned: lat, lon, osm_id, municipio_name.
  display_name, street, postcode, and the raw address object are NEVER
  stored or logged.

Rate limit: ≥1 req/s enforced via asyncio.sleep (Nominatim usage policy).
Cache: Redis key "brave:geo:{location_id}" with 30-day TTL.
Negative cache: {"__no_match": true} sentinel prevents repeated queries.

Guard: raises RuntimeError when AppConfig().run_real_externals is False.
Use NullGeocoderClient (brave/clients/null_nominatim.py) in CI/offline.
Use FakeGeocoderClient (tests/fakes/fake_nominatim.py) in unit tests.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from brave.config.settings import NominatimConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Redis key prefix — full key: f"brave:geo:{location_id}"
# Analog: geo.py lines 32-36 (REDIS_GEO_KEY_PREFIX / REDIS_GEO_TTL pattern)
NOMINATIM_CACHE_KEY_PREFIX: str = "brave:geo:"
NOMINATIM_CACHE_TTL: int = 86_400 * 30  # geocodes are stable — 30 days (module default)

# All 27 Brazilian state codes mapped to full names (Nominatim query parameter)
UF_NAME: dict[str, str] = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AM": "Amazonas",
    "AP": "Amapá",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MG": "Minas Gerais",
    "MS": "Mato Grosso do Sul",
    "MT": "Mato Grosso",
    "PA": "Pará",
    "PB": "Paraíba",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "PR": "Paraná",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RO": "Rondônia",
    "RR": "Roraima",
    "RS": "Rio Grande do Sul",
    "SC": "Santa Catarina",
    "SE": "Sergipe",
    "SP": "São Paulo",
    "TO": "Tocantins",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode(value: Any) -> str:
    """Decode Redis response bytes to str (mirrors geo.py pattern)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _is_retryable(exc: BaseException) -> bool:
    """429 / 5xx / connection errors are retryable; other 4xx are not."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    return False


# ---------------------------------------------------------------------------
# Real client
# ---------------------------------------------------------------------------


class NominatimGeocoderClient:
    """Real Nominatim geocoder — async httpx, tenacity retry, Redis cache, rate limit.

    Structurally satisfies GeocoderClientProtocol (D-09).
    Guard: raises RuntimeError when AppConfig().run_real_externals is False.

    Args:
        config: NominatimConfig instance (base_url, user_agent, intervals, TTL).
        redis:  Redis client (sync — compatible with Celery worker + asyncio contexts).
    """

    def __init__(self, config: "NominatimConfig", redis: Any) -> None:
        # Analog: brave/clients/places.py lines 181-199
        from brave.config.settings import AppConfig

        if not AppConfig().run_real_externals:
            raise RuntimeError(
                "NominatimGeocoderClient: run_real_externals=False — "
                "use NullGeocoderClient / FakeGeocoderClient in the default test suite. "
                "Set RUN_REAL_EXTERNALS=true to enable real Nominatim calls."
            )
        self._config = config
        self._redis = redis
        self._min_interval: float = config.min_request_interval
        self._last_request_ts: float = 0.0
        self._cache_ttl: int = config.cache_ttl

    @retry(
        # Analog: brave/clients/places.py lines 223-228
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def geocode(
        self, location_id: str, name: str, uf: str
    ) -> dict[str, Any] | None:
        """Forward-geocode `name + UF + Brazil` → 4-key geo dict or None.

        Steps:
          1. Redis cache hit → return cached result (or None on __no_match sentinel)
          2. Rate limit: asyncio.sleep if elapsed < min_request_interval
          3. Nominatim GET with q="{name}, {UF_NAME[uf]}, Brazil"
          4. Empty data → cache sentinel + return None
          5. Parse address precedence: municipality → city → town → village → county
          6. Cache {lat, lon, osm_id, municipio_name} with 30-day TTL
          7. Return 4-key result dict (LGPD: no display_name, no street)

        Args:
            location_id: TripAdvisor attraction locationId (used as Redis cache key).
            name:        Attraction name for the search query.
            uf:          Two-letter Brazilian state code.

        Returns:
            {"lat": float, "lon": float, "osm_id": int | None, "municipio_name": str | None}
            or None when Nominatim returns no results.
        """
        # 1. Redis cache hit (analog: geo.py lines 92-96)
        key = f"{NOMINATIM_CACHE_KEY_PREFIX}{location_id}"
        raw = _decode(self._redis.get(key))
        if raw:
            logger.debug("nominatim_cache_hit", location_id=location_id)
            cached = json.loads(raw)
            return None if cached.get("__no_match") else cached

        # 2. Rate limit — ≥1 req/s per Nominatim policy (Pattern 3)
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_ts = time.monotonic()

        # 3. Nominatim HTTP call (spike-validated q/params shape)
        uf_name = UF_NAME.get(uf, uf)
        params = {
            "q": f"{name}, {uf_name}, Brazil",
            "format": "json",
            "limit": 1,
            "countrycodes": "br",
            "addressdetails": 1,
        }
        headers = {"User-Agent": self._config.user_agent}

        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as hc:
            resp = await hc.get(
                self._config.base_url, params=params, headers=headers
            )
        resp.raise_for_status()
        data = resp.json()

        # 4. Empty data → cache negative sentinel + return None
        if not data:
            self._redis.setex(
                key, self._cache_ttl, json.dumps({"__no_match": True})
            )
            logger.debug(
                "nominatim_no_result",
                location_id=location_id,
                # LGPD: log only location_id + uf — never name or raw address
                uf=uf,
            )
            return None

        # 5. Parse address precedence chain (LGPD decision #8)
        hit = data[0]
        addr = hit.get("address", {})
        municipio_name = (
            addr.get("municipality")
            or addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("county")
        )

        # 6. LGPD: persist only lat/lon/osm_id/municipio_name — never display_name or street
        result: dict[str, Any] = {
            "lat": float(hit["lat"]),
            "lon": float(hit["lon"]),
            "osm_id": hit.get("osm_id"),
            "municipio_name": municipio_name,
        }
        self._redis.setex(key, self._cache_ttl, json.dumps(result))
        logger.info(
            "nominatim_geocoded",
            location_id=location_id,
            uf=uf,
            municipio_name=municipio_name,
            # LGPD: log location_id + resolved municipio only; never name/address payload
        )

        # 7. Return 4-key LGPD-safe dict
        return result

    @retry(
        # Same retryable-error policy as geocode (analog: places.py 223-228)
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def geocode_national(
        self, location_id: str, name: str
    ) -> dict[str, Any] | None:
        """Forward-geocode `name + Brazil` (no UF) → 4-key geo dict or None (Phase 15).

        The all-Brazil bulk attractions lane (geoId 294280) has no per-UF context —
        UF is derived downstream from the geocoded município/IBGE code, not supplied
        as input. This national variant queries ``"{name}, Brazil"`` instead of
        ``"{name}, {uf}, Brazil"`` and otherwise honours the same Redis cache,
        rate-limit, retry, and LGPD-safe return contract as ``geocode``.

        The cache key is namespaced (``brave:geo:natl:{location_id}``) so a national
        result never collides with a per-UF ``geocode`` result for the same id.

        Args:
            location_id: TripAdvisor attraction locationId (Redis cache key).
            name:        Attraction name for the national search query.

        Returns:
            {"lat": float, "lon": float, "osm_id": int | None, "municipio_name": str | None}
            or None when Nominatim returns no results.
        """
        # 1. Redis cache hit (national namespace — never collides with per-UF key)
        key = f"{NOMINATIM_CACHE_KEY_PREFIX}natl:{location_id}"
        raw = _decode(self._redis.get(key))
        if raw:
            logger.debug("nominatim_natl_cache_hit", location_id=location_id)
            cached = json.loads(raw)
            return None if cached.get("__no_match") else cached

        # 2. Rate limit — ≥1 req/s per Nominatim policy (Pattern 3)
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_ts = time.monotonic()

        # 3. Nominatim HTTP call — national query (no UF segment)
        params = {
            "q": f"{name}, Brazil",
            "format": "json",
            "limit": 1,
            "countrycodes": "br",
            "addressdetails": 1,
        }
        headers = {"User-Agent": self._config.user_agent}

        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as hc:
            resp = await hc.get(
                self._config.base_url, params=params, headers=headers
            )
        resp.raise_for_status()
        data = resp.json()

        # 4. Empty data → cache negative sentinel + return None
        if not data:
            self._redis.setex(
                key, self._cache_ttl, json.dumps({"__no_match": True})
            )
            logger.debug(
                "nominatim_natl_no_result",
                location_id=location_id,
                # LGPD: log only location_id — never name or raw address
            )
            return None

        # 5. Parse address precedence chain (LGPD decision #8)
        hit = data[0]
        addr = hit.get("address", {})
        municipio_name = (
            addr.get("municipality")
            or addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("county")
        )

        # 6. LGPD: persist only lat/lon/osm_id/municipio_name — never display_name or street
        result: dict[str, Any] = {
            "lat": float(hit["lat"]),
            "lon": float(hit["lon"]),
            "osm_id": hit.get("osm_id"),
            "municipio_name": municipio_name,
        }
        self._redis.setex(key, self._cache_ttl, json.dumps(result))
        logger.info(
            "nominatim_natl_geocoded",
            location_id=location_id,
            municipio_name=municipio_name,
            # LGPD: log location_id + resolved municipio only; never name/address payload
        )

        # 7. Return 4-key LGPD-safe dict
        return result


# ---------------------------------------------------------------------------
# Structural type check
# ---------------------------------------------------------------------------

# Analog: null_tripadvisor.py lines 65-70
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import GeocoderClientProtocol

    _c: GeocoderClientProtocol = NominatimGeocoderClient  # type: ignore[assignment]  # noqa: F841
