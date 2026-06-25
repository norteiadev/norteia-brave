# Phase 14: Coordless attraction geo-resolution via Nominatim - Pattern Map

**Mapped:** 2026-06-25
**Files analyzed:** 8 new/modified files
**Analogs found:** 8 / 8

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `brave/clients/base.py` (ADD protocol) | middleware/boundary | request-response | `brave/clients/base.py` (existing Protocol definitions) | exact |
| `brave/clients/nominatim.py` | service/client | request-response | `brave/lanes/tripadvisor/client.py` + `brave/clients/places.py` | exact |
| `brave/clients/null_nominatim.py` | utility/stub | request-response | `brave/clients/null_tripadvisor.py` | exact |
| `brave/config/settings.py` (ADD NominatimConfig) | config | — | `brave/config/settings.py` (`TripAdvisorConfig`) | exact |
| `tests/fakes/fake_nominatim.py` | test/fake | request-response | `tests/fakes/fake_tripadvisor.py` | exact |
| `tests/unit/clients/test_nominatim.py` | test | request-response | `tests/unit/clients/test_null_tripadvisor.py` + `tests/unit/lanes/tripadvisor/test_client.py` | exact |
| `brave/lanes/tripadvisor/ibge.py` (EDIT: max_distance_km default note) | utility | transform | `brave/lanes/tripadvisor/ibge.py` | exact (self) |
| `brave/lanes/tripadvisor/atrativos.py` (EDIT: geo-enrichment step) | service | CRUD | `brave/lanes/tripadvisor/atrativos.py` | exact (self) |
| `tests/unit/lanes/tripadvisor/test_atrativos.py` (EDIT: add regression tests) | test | CRUD | `tests/unit/lanes/tripadvisor/test_atrativos.py` | exact (self) |

---

## Pattern Assignments

### `brave/clients/base.py` — ADD GeocoderClientProtocol

**Analog:** `brave/clients/base.py` (lines 238–293, `TripAdvisorClientProtocol`)

**Docstring placement** — the module docstring (lines 1–20) lists all protocols. Append to the nine-item list:
```
  10. GeocoderClientProtocol — OpenStreetMap Nominatim forward-geocoder (Phase 14, TA-14)
```

**Protocol definition** — mirror `TripAdvisorClientProtocol` exactly (lines 238–244, Protocol class + docstring):
```python
# brave/clients/base.py — append after TripAdvisorClientProtocol

class GeocoderClientProtocol(Protocol):
    """OpenStreetMap Nominatim forward-geocoder (Phase 14, TA-14).

    LGPD: returns ONLY lat/lon + OSM place id + the município-level
    address sub-field needed for IBGE matching. No address PII is returned
    or stored (decision #8, 14-CONTEXT.md).
    """

    async def geocode(
        self, location_id: str, name: str, uf: str
    ) -> dict[str, Any] | None:
        """Forward-geocode `name + UF + Brazil` → geo dict or None.

        Returns on hit: {"lat": float, "lon": float, "osm_id": int | None,
            "municipio_name": str | None}  # first of address.municipality
            |city|town|village|county precedence chain.
        Returns None when Nominatim returns no results.
        Caches by location_id in Redis (one Nominatim call per attraction per 30d).
        """
        ...
```

---

### `brave/clients/nominatim.py` — NominatimGeocoderClient (real, async httpx)

**Analog:** `brave/lanes/tripadvisor/client.py` (httpx, structlog, Redis, json) + `brave/clients/places.py` (run_real_externals guard lines 181–199, tenacity @retry lines 29, 223–228)

**Imports pattern** (mirror `client.py` lines 32–43 + `places.py` lines 24–31):
```python
# brave/clients/nominatim.py
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
```

**Cache constants** (mirror `brave/lanes/tripadvisor/geo.py` lines 32–36):
```python
# brave/clients/nominatim.py — module-level constants
# Analog: geo.py lines 32-36 (REDIS_GEO_KEY_PREFIX / REDIS_GEO_TTL pattern)
NOMINATIM_CACHE_KEY_PREFIX: str = "brave:geo:"   # full key: f"brave:geo:{location_id}"
NOMINATIM_CACHE_TTL: int = 86_400 * 30           # geocodes are stable — 30d

_NOMINATIM_SEARCH_URL: str = "https://nominatim.openstreetmap.org/search"
```

**_decode helper** (copy verbatim from `geo.py` lines 39–45):
```python
def _decode(value: Any) -> str:
    """Decode Redis response bytes to str (mirrors geo.py pattern)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
```

**_is_retryable helper** (mirror `places.py` lines 130–158 — adapt for httpx status codes):
```python
def _is_retryable(exc: BaseException) -> bool:
    """429 / 5xx / connection errors are retryable; 4xx are not."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    return False
```

**run_real_externals guard** — `__init__` pattern (mirror `places.py` lines 181–203):
```python
class NominatimGeocoderClient:
    """Real Nominatim geocoder — httpx, tenacity, Redis cache, rate limit.

    Structurally satisfies GeocoderClientProtocol (D-09).
    Guard: raises RuntimeError when run_real_externals=False.
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
        ...
```

**Rate-limit + Redis cache + httpx call** (core `geocode` body; Pattern 3 from RESEARCH.md):
```python
    async def geocode(
        self, location_id: str, name: str, uf: str
    ) -> dict[str, Any] | None:
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

        # 3. Nominatim HTTP call (analog: spike validated q/params shape)
        uf_name = self._config.uf_name_map.get(uf, uf)
        params = {
            "q": f"{name}, {uf_name}, Brazil",
            "format": "json",
            "limit": 1,
            "countrycodes": "br",
            "addressdetails": 1,
        }
        headers = {"User-Agent": self._config.user_agent}

        async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as hc:
            resp = await hc.get(_NOMINATIM_SEARCH_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            # Cache negative result (policy: don't repeat same query)
            self._redis.setex(key, NOMINATIM_CACHE_TTL, json.dumps({"__no_match": True}))
            logger.debug("nominatim_no_result", location_id=location_id, name=name, uf=uf)
            return None

        hit = data[0]
        addr = hit.get("address", {})
        # LGPD: locked precedence chain, address.municipality → city → town → village → county
        municipio_name = (
            addr.get("municipality")
            or addr.get("city")
            or addr.get("town")
            or addr.get("village")
            or addr.get("county")
        )
        # LGPD: persist only lat/lon/osm_id/municipio_name — never store display_name or street
        result: dict[str, Any] = {
            "lat": float(hit["lat"]),
            "lon": float(hit["lon"]),
            "osm_id": hit.get("osm_id"),
            "municipio_name": municipio_name,
        }
        self._redis.setex(key, NOMINATIM_CACHE_TTL, json.dumps(result))
        logger.info(
            "nominatim_geocoded",
            location_id=location_id,
            uf=uf,
            municipio_name=municipio_name,
            # LGPD: log location_id + resolved municipio only; never name/address payload
        )
        return result


# Structural type check (analog: null_tripadvisor.py line 66-70)
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import GeocoderClientProtocol
    _c: GeocoderClientProtocol = NominatimGeocoderClient  # noqa: F841
```

---

### `brave/clients/null_nominatim.py` — NullGeocoderClient

**Analog:** `brave/clients/null_tripadvisor.py` (entire file — exact structural mirror)

**Full file pattern** (mirror `null_tripadvisor.py` lines 1–71 exactly, substitute names):
```python
# brave/clients/null_nominatim.py
"""In-package offline Geocoder stub (production-safe, TA-14).

Used when AppConfig.run_real_externals is False (CI default).
Returns None (no match) so callers fall through to quarantine without network I/O.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree. Tests use tests/fakes/FakeGeocoderClient for call-recording assertions.
"""
from __future__ import annotations
from typing import Any


class NullGeocoderClient:
    """No-network geocoder stub (structural protocol match).

    Returns None for geocode() — no httpx call, no Redis write, no rate limit.
    Safe to use when RUN_REAL_EXTERNALS is unset/false.
    """

    async def geocode(
        self, location_id: str, name: str, uf: str
    ) -> dict[str, Any] | None:
        """Return None — offline stub performs no geocoding.

        Args:
            location_id: TripAdvisor location id (ignored).
            name:        Attraction name (ignored).
            uf:          Two-letter state code (ignored).

        Returns:
            None (no match sentinel — caller quarantines if no other match).
        """
        return None


# Structural type check (analog: null_tripadvisor.py lines 65-70)
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import GeocoderClientProtocol
    _c: GeocoderClientProtocol = NullGeocoderClient()  # noqa: F841
```

---

### `brave/config/settings.py` — ADD NominatimConfig + nest into AppConfig

**Analog:** `brave/config/settings.py` lines 232–301 (`TripAdvisorConfig` + `AppConfig` nesting)

**NominatimConfig class** (mirror `TripAdvisorConfig` lines 232–287 — same Field/SettingsConfigDict shape, CR-02 no aliases):
```python
# brave/config/settings.py — append after TripAdvisorConfig, before AppConfig

class NominatimConfig(BaseSettings):
    """OpenStreetMap Nominatim geocoder configuration (TA-14).

    No env-var aliases (CR-02): each field resolves from its exact BRAVE_NOMINATIM_
    prefixed name only.

    Env prefix: BRAVE_NOMINATIM_
      BRAVE_NOMINATIM_BASE_URL          — Nominatim search endpoint (default public OSM)
      BRAVE_NOMINATIM_USER_AGENT        — identifiable UA string (required by policy)
      BRAVE_NOMINATIM_MIN_REQUEST_INTERVAL — seconds between requests (default 1.1)
      BRAVE_NOMINATIM_CACHE_TTL         — geocode Redis TTL seconds (default 2592000 = 30d)
      BRAVE_NOMINATIM_TIMEOUT_SECONDS   — httpx timeout (default 15)
    """

    base_url: str = Field(
        default="https://nominatim.openstreetmap.org/search",
        description="Nominatim search endpoint (BRAVE_NOMINATIM_BASE_URL). "
                    "Override for a self-hosted instance.",
    )
    user_agent: str = Field(
        default="norteia-brave/1.0 (leandro.freire08@gmail.com)",
        description="HTTP User-Agent sent to Nominatim (BRAVE_NOMINATIM_USER_AGENT). "
                    "Required by Nominatim usage policy — must be identifiable.",
    )
    min_request_interval: float = Field(
        default=1.1,
        description="Minimum seconds between consecutive Nominatim requests "
                    "(BRAVE_NOMINATIM_MIN_REQUEST_INTERVAL). Policy: ≤1 req/s; 1.1 adds margin.",
    )
    cache_ttl: int = Field(
        default=86_400 * 30,
        description="Redis TTL for cached geocode results in seconds "
                    "(BRAVE_NOMINATIM_CACHE_TTL). Default 30 days — geocodes are stable.",
    )
    timeout_seconds: float = Field(
        default=15.0,
        description="httpx request timeout in seconds (BRAVE_NOMINATIM_TIMEOUT_SECONDS).",
    )

    model_config = SettingsConfigDict(env_prefix="BRAVE_NOMINATIM_")
    # CR-02: NO Field(alias=...) anywhere in this class.
```

**AppConfig nesting** (mirror `AppConfig` lines 290–301 — add one line):
```python
# brave/config/settings.py — AppConfig.nominatim field
# Analog: tripadvisor field at line 301
nominatim: NominatimConfig = Field(default_factory=NominatimConfig)
```

---

### `tests/fakes/fake_nominatim.py` — FakeGeocoderClient

**Analog:** `tests/fakes/fake_tripadvisor.py` (entire file — exact structural mirror)

**Full file pattern** (mirror `fake_tripadvisor.py` lines 1–101, substitute for single `geocode` method):
```python
# tests/fakes/fake_nominatim.py
"""Fake geocoder client for offline testing (TA-14).

FakeGeocoderClient implements GeocoderClientProtocol (structural typing, D-09).
Records all calls for assertion and returns pre-configured fixture data.

Usage:
    from tests.fakes.fake_nominatim import FakeGeocoderClient

    fake = FakeGeocoderClient(
        fixture_results={"312332": {"lat": -19.047, "lon": -43.426,
                                     "osm_id": 123, "municipio_name": "Conceição do Mato Dentro"}},
    )
    result = await fake.geocode("312332", "Cachoeira do Tabuleiro", "MG")
    assert fake.geocode_calls == [{"location_id": "312332", "name": "Cachoeira do Tabuleiro", "uf": "MG"}]
"""
from typing import Any

from brave.clients.base import GeocoderClientProtocol


class FakeGeocoderClient:
    """Fake geocoder that returns pre-configured fixture results.

    Structurally satisfies GeocoderClientProtocol (D-09).
    Records all calls for assertion in tests.
    Never makes network calls or writes to Redis.
    """

    def __init__(
        self,
        fixture_results: dict[str, dict[str, Any] | None] | None = None,
    ) -> None:
        """Initialize with optional fixture data.

        Args:
            fixture_results: Dict mapping location_id → geo dict or None.
                             Returned by geocode().
        """
        self._fixture_results = fixture_results or {}
        # Call recording list for test assertions (analog: fake_tripadvisor.py line 53-55)
        self.geocode_calls: list[dict[str, Any]] = []

    async def geocode(
        self, location_id: str, name: str, uf: str
    ) -> dict[str, Any] | None:
        """Return fixture result for the given location_id.

        Args:
            location_id: TripAdvisor attraction location id.
            name:        Attraction name (recorded, not used for lookup).
            uf:          Two-letter state code (recorded, not used for lookup).

        Returns:
            Fixture geo dict if location_id present, None otherwise.
        """
        self.geocode_calls.append({"location_id": location_id, "name": name, "uf": uf})
        return self._fixture_results.get(location_id)


# Structural type check (analog: fake_tripadvisor.py lines 97-100)
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: GeocoderClientProtocol = FakeGeocoderClient()  # noqa: F841
```

---

### `tests/unit/clients/test_nominatim.py` — respx-mocked client tests

**Analog:** `tests/unit/clients/test_null_tripadvisor.py` (class structure) + `tests/unit/lanes/tripadvisor/test_client.py` lines 349–391 (respx.mock + side_effect + fakeredis pattern)

**Imports + setup** (mirror `test_client.py` lines 1–22):
```python
# tests/unit/clients/test_nominatim.py
import json

import fakeredis
import httpx
import pytest
import respx

from brave.config.settings import AppConfig, NominatimConfig
```

**Guard test** (mirror `test_null_tripadvisor.py` structure, analog `test_client.py` SessionMissingError pattern):
```python
class TestNominatimGeocoderClientGuard:
    def test_guard_raises_when_real_externals_false(self) -> None:
        """NominatimGeocoderClient raises RuntimeError when run_real_externals=False."""
        from brave.clients.nominatim import NominatimGeocoderClient

        redis = fakeredis.FakeRedis()
        config = NominatimConfig()
        # run_real_externals defaults to False in test env
        with pytest.raises(RuntimeError, match="run_real_externals=False"):
            NominatimGeocoderClient(config=config, redis=redis)
```

**respx mock pattern** (mirror `test_client.py` lines 375–391 — the canonical respx side_effect shape in this repo):
```python
class TestNominatimGeocoderClientReal:
    """Tests for the real client via respx mock (run_real_externals override)."""

    @pytest.mark.asyncio
    async def test_request_params(self, monkeypatch) -> None:
        """geocode sends User-Agent + addressdetails=1 + countrycodes=br."""
        monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

        from brave.clients.nominatim import NominatimGeocoderClient

        redis = fakeredis.FakeRedis()
        config = NominatimConfig()

        captured_request = None

        with respx.mock:
            def capture(request):
                nonlocal captured_request
                captured_request = request
                return httpx.Response(200, json=[{
                    "lat": "-19.0469", "lon": "-43.4256", "osm_id": 123,
                    "address": {"municipality": "Conceição do Mato Dentro",
                                "state": "Minas Gerais", "country_code": "br"},
                }])

            # Analog: test_client.py lines 381-384 (respx.post/get + side_effect)
            respx.get("https://nominatim.openstreetmap.org/search").mock(
                side_effect=capture
            )
            client = NominatimGeocoderClient(config=config, redis=redis)
            result = await client.geocode("312332", "Cachoeira do Tabuleiro", "MG")

        assert result is not None
        assert result["municipio_name"] == "Conceição do Mato Dentro"
        assert "User-Agent" in captured_request.headers
        assert captured_request.url.params["addressdetails"] == "1"
        assert captured_request.url.params["countrycodes"] == "br"
```

**Cache-hit test pattern** (Redis cache round-trip, no second httpx call):
```python
    @pytest.mark.asyncio
    async def test_cache_by_location_id(self, monkeypatch) -> None:
        """Second geocode call hits Redis cache — respx call count == 1."""
        monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
        from brave.clients.nominatim import NominatimGeocoderClient

        redis = fakeredis.FakeRedis()
        config = NominatimConfig()

        with respx.mock:
            route = respx.get("https://nominatim.openstreetmap.org/search").mock(
                return_value=httpx.Response(200, json=[{
                    "lat": "-19.0469", "lon": "-43.4256", "osm_id": 123,
                    "address": {"municipality": "Conceição do Mato Dentro"},
                }])
            )
            client = NominatimGeocoderClient(config=config, redis=redis)
            await client.geocode("312332", "Cachoeira do Tabuleiro", "MG")
            await client.geocode("312332", "Cachoeira do Tabuleiro", "MG")  # cache hit

        assert route.call_count == 1, "Second call must hit cache, not Nominatim"
```

**Null client test pattern** (mirror `test_null_tripadvisor.py` lines 11–42):
```python
class TestNullGeocoderClient:
    @pytest.mark.asyncio
    async def test_geocode_returns_none(self) -> None:
        from brave.clients.null_nominatim import NullGeocoderClient
        client = NullGeocoderClient()
        result = await client.geocode("312332", "Cachoeira do Tabuleiro", "MG")
        assert result is None

    def test_protocol_compliance(self) -> None:
        from brave.clients.null_nominatim import _check_protocol_compliance
        _check_protocol_compliance()
```

---

### `brave/lanes/tripadvisor/atrativos.py` — EDIT: geo-enrichment + async _ingest_one

**Analog:** `brave/lanes/tripadvisor/atrativos.py` (self — modification of existing lines 73–278)

**__init__ signature extension** (after `destino_rio_map` param, lines 92–104):
```python
# brave/lanes/tripadvisor/atrativos.py — __init__ addition
# Add as last parameter, defaulting to None (no Phase-11/13 regression)
# TYPE_CHECKING import: add GeocoderClientProtocol to the existing block (line 56-57)

def __init__(
    self,
    ta_client: "TripAdvisorClientProtocol",
    session: Session,
    config: ScoreConfig,
    ibge_records: list[IbgeMunicipio],
    destino_rio_map: dict[str, tuple[uuid.UUID, str]] | None = None,
    geocoder: "GeocoderClientProtocol | None" = None,   # NEW — None = current behavior
) -> None:
    self._client = ta_client
    self._session = session
    self._config = config
    self._ibge_records = ibge_records
    self._destino_rio_map: dict[str, tuple[uuid.UUID, str]] = destino_rio_map or {}
    self._geocoder = geocoder  # NEW
```

**produce loop** — promote `_ingest_one` call to `await` (line 124):
```python
# brave/lanes/tripadvisor/atrativos.py — produce() loop body (line 122-133)
# Change: self._ingest_one(...) → await self._ingest_one(...)
# Reason: _ingest_one becomes async to support await geocoder.geocode(...)
        for entity in attractions:
            try:
                await self._ingest_one(uf, entity, run_rio=run_rio)   # CHANGED: add await
            except Exception as exc:  # noqa: BLE001
                ...
```

**_ingest_one signature** — promote to async:
```python
# brave/lanes/tripadvisor/atrativos.py line 135
async def _ingest_one(self, uf: str, entity: dict[str, Any], *, run_rio: bool) -> None:
```

**geo-enrichment insertion** (after existing `resolve_municipio` block, lines 173–188):
```python
# brave/lanes/tripadvisor/atrativos.py — insert after existing ibge_match block (lines 173-188)
        # Existing first attempt (name fuzzy-match; haversine skipped — no coords in listing card)
        ibge_match = resolve_municipio(
            name,
            uf,
            self._ibge_records,
            candidate_lat=lat,
            candidate_lng=lng,
        )

        # NEW: geo-enrichment via Nominatim (TA-15) — only when first attempt missed
        if ibge_match is None and self._geocoder is not None:
            geo = await self._geocoder.geocode(location_id, name, uf)
            if geo is not None:
                ibge_match = resolve_municipio(
                    geo.get("municipio_name") or name,          # primary: address name
                    uf,
                    self._ibge_records,
                    candidate_lat=geo["lat"],
                    candidate_lng=geo["lon"],                   # secondary: haversine
                    max_distance_km=50.0,                       # relaxed (spike: seats 15-25km out)
                )

        if ibge_match is None:
            quarantine_poison(
                session=self._session,
                nascente_id=None,
                task_name="brave.ta.atrativos.ibge_unmatched",
                error=f"ibge_unmatched: could not resolve '{name}' in UF={uf}",
                payload={"uf": uf, "locationId": location_id, "name": name},
            )
            return
```

---

### `tests/unit/lanes/tripadvisor/test_atrativos.py` — EDIT: add regression tests

**Analog:** `tests/unit/lanes/tripadvisor/test_atrativos.py` (self — extend existing file)

**Import additions** (add after line 22):
```python
from tests.fakes.fake_nominatim import FakeGeocoderClient
```

**_make_coordless_card helper** (extend `_make_card` idiom at lines 43–63):
```python
def _make_coordless_card(name: str = "Cachoeira do Tabuleiro") -> dict[str, Any]:
    """Build a coordless card whose name does NOT match any IBGE município name.

    Used for regression tests: this card quarantines without geo-enrichment
    and resolves via Nominatim when a FakeGeocoderClient is injected.
    """
    return {
        "locationId": 312332,
        "name": name,
        "review_count": 100,
        "rating": 4.0,
        "category": "Waterfalls",
        # lat/lng deliberately absent — mirrors real AttractionsFusion listing card
    }
```

**Regression test — coordless resolves via geo-enrichment** (mirror test structure at lines 96–132):
```python
class TestAtrativosGeoEnrichment:
    """Regression tests for TA-15: coordless card geo-enrichment via Nominatim."""

    @pytest.mark.asyncio
    async def test_coordless_resolves_via_geo(self) -> None:
        """Coordless card that previously quarantined now resolves to correct município.

        Fixtures: "Cachoeira do Tabuleiro" (locationId=312332, MG) → Nominatim returns
        Conceição do Mato Dentro coords → resolve_municipio haversine 50km matches.
        """
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # IBGE record for Conceição do Mato Dentro (MG, spike-verified coords)
        ibge_records = [
            IbgeMunicipio("3117900", "Conceição do Mato Dentro", "MG", -19.047, -43.426),
        ]
        destino_rio_map = {"3117900": (_PARENT_RIO_ID, "tripadvisor:destination:303380")}
        card = _make_coordless_card()
        fake_ta = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            geo_ids={"MG": _GEO_ID_MG},
        )
        # FakeGeocoderClient returns the Nominatim geocode result for this locationId
        fake_geo = FakeGeocoderClient(
            fixture_results={
                "312332": {
                    "lat": -19.047,
                    "lon": -43.426,
                    "osm_id": 123,
                    "municipio_name": "Conceição do Mato Dentro",
                }
            }
        )

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_ta,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=ibge_records,
                destino_rio_map=destino_rio_map,
                geocoder=fake_geo,        # inject FakeGeocoderClient
            )
            await ingest.produce("MG", run_rio=False)

        assert mock_store_raw.called, (
            "store_raw must be called — coordless card must resolve via geo-enrichment "
            "instead of quarantining as ibge_unmatched"
        )
        payload = mock_store_raw.call_args.kwargs["payload"]
        assert payload["municipio_id"] == "3117900"
        assert len(fake_geo.geocode_calls) == 1
        assert fake_geo.geocode_calls[0]["location_id"] == "312332"

    @pytest.mark.asyncio
    async def test_quarantine_after_both_fail(self) -> None:
        """ibge_unmatched quarantine fires only after both name-match AND geo-enrichment fail."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        ibge_records = [IbgeMunicipio("3550308", "São Paulo", "SP", -23.55, -46.63)]
        card = _make_coordless_card()
        fake_ta = FakeTripAdvisorClient(
            fixture_attractions={_GEO_ID_MG: [card]},
            geo_ids={"MG": _GEO_ID_MG},
        )
        # Geocoder returns no match (all misses — both strategies fail)
        fake_geo = FakeGeocoderClient(fixture_results={})

        with patch("brave.lanes.tripadvisor.atrativos.quarantine_poison") as mock_q:
            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_ta,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=ibge_records,
                destino_rio_map={},
                geocoder=fake_geo,
            )
            await ingest.produce("MG", run_rio=False)

        # quarantine_poison called with ibge_unmatched (not some other task_name)
        quarantine_calls = [
            c for c in mock_q.call_args_list
            if c.kwargs.get("task_name") == "brave.ta.atrativos.ibge_unmatched"
        ]
        assert len(quarantine_calls) == 1

    @pytest.mark.asyncio
    async def test_no_geocoder_unchanged(self) -> None:
        """geocoder=None → existing behavior unchanged (no Phase-11/13 regression)."""
        from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

        # Use the existing _IBGE_RECORDS fixture (Uberlândia matches the card name)
        card = _make_card(name="Uberlândia")
        fake_ta = _make_fake_client(card)

        with (
            patch("brave.lanes.tripadvisor.atrativos.store_raw") as mock_store_raw,
            patch("brave.lanes.tripadvisor.atrativos.process_nascente_record"),
        ):
            mock_nascente = MagicMock()
            mock_nascente.id = uuid.uuid4()
            mock_store_raw.return_value = mock_nascente

            ingest = TripAdvisorAtrativosIngest(
                ta_client=fake_ta,
                session=MagicMock(),
                config=_make_config(),
                ibge_records=_IBGE_RECORDS,
                destino_rio_map=_DESTINO_RIO_MAP,
                # geocoder NOT passed — defaults to None
            )
            await ingest.produce("MG", run_rio=False)

        assert mock_store_raw.called
```

---

## Shared Patterns

### run_real_externals guard
**Source:** `brave/clients/places.py` lines 181–199
**Apply to:** `brave/clients/nominatim.py` `__init__`
```python
from brave.config.settings import AppConfig
if not AppConfig().run_real_externals:
    raise RuntimeError(
        "NominatimGeocoderClient: run_real_externals=False — "
        "use NullGeocoderClient / FakeGeocoderClient in the default test suite. "
        "Set RUN_REAL_EXTERNALS=true to enable real API calls."
    )
```

### Protocol structural compliance check
**Source:** `brave/clients/null_tripadvisor.py` lines 65–70
**Apply to:** `brave/clients/null_nominatim.py`, `brave/clients/nominatim.py`, `tests/fakes/fake_nominatim.py`
```python
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import GeocoderClientProtocol
    _c: GeocoderClientProtocol = NullGeocoderClient()  # noqa: F841
```

### Redis key-prefix / setex / _decode cache pattern
**Source:** `brave/lanes/tripadvisor/geo.py` lines 32–36, 39–45, 92–106
**Apply to:** `brave/clients/nominatim.py` (cache by locationId)
```python
# Key construction
key = f"{NOMINATIM_CACHE_KEY_PREFIX}{location_id}"   # "brave:geo:{location_id}"
# Read
raw = _decode(self._redis.get(key))
if raw:
    return json.loads(raw)
# Write after miss
self._redis.setex(key, NOMINATIM_CACHE_TTL, json.dumps(result))
```

### pydantic-settings config field with SettingsConfigDict
**Source:** `brave/config/settings.py` lines 232–287 (`TripAdvisorConfig`)
**Apply to:** `NominatimConfig` (new class)
```python
model_config = SettingsConfigDict(env_prefix="BRAVE_NOMINATIM_")
# CR-02: NO Field(alias=...) anywhere in this class.
```

### tenacity @retry on async methods
**Source:** `brave/clients/places.py` lines 29, 223–228
**Apply to:** `brave/clients/nominatim.py` `geocode` method
```python
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
```

### respx GET mock + side_effect in unit tests
**Source:** `tests/unit/lanes/tripadvisor/test_client.py` lines 375–391 (POST version; adapt to GET)
**Apply to:** `tests/unit/clients/test_nominatim.py`
```python
with respx.mock:
    def capture_request(request):
        nonlocal captured_body
        captured_body = request
        return httpx.Response(200, json=[...])

    respx.get("https://nominatim.openstreetmap.org/search").mock(
        side_effect=capture_request
    )
    result = await client.geocode(...)
```

### Test class structure (existing atrativos test)
**Source:** `tests/unit/lanes/tripadvisor/test_atrativos.py` lines 43–85 (`_make_card`, `_make_fake_client`, `_make_config`)
**Apply to:** New regression tests in `test_atrativos.py` — extend same helpers, add `_make_coordless_card`

---

## No Analog Found

All files have direct analogs in the codebase. No new external patterns are required.

---

## Key Constraints Summary

These must be respected exactly (sourced from CONTEXT.md and RESEARCH.md):

| Constraint | Source | What to enforce |
|------------|--------|-----------------|
| CR-02: no `Field(alias=...)` | `settings.py` comment + RESEARCH | `NominatimConfig` fields resolve only from exact `BRAVE_NOMINATIM_*` names |
| D-18 import rule | CLAUDE.md | `atrativos.py` imports geocoder from `brave.clients` (not lane submodule) |
| `max_distance_km=15.0` default unchanged | RESEARCH anti-patterns | Pass `max_distance_km=50.0` ONLY at geo-enrichment call site; do not change default |
| `geocoder=None` default | RESEARCH Pattern 2 | Last `__init__` param; `None` = byte-identical behavior to today |
| LGPD: persist only lat/lon/osm_id/municipio_name | CONTEXT decision #8 | Result dict has exactly 4 keys; never log or cache raw `address` |
| `geopy` forbidden | RESEARCH alternatives | Raw httpx only — preserves respx mockability |
| Rate limit ≥1 req/s | CONTEXT decision #6 | `min_request_interval=1.1` in-process `asyncio.sleep` before each real call |
| Cache negative results | RESEARCH Pattern 3 | Store `{"__no_match": True}` sentinel in Redis to prevent repeated queries |

---

## Metadata

**Analog search scope:** `brave/clients/`, `brave/lanes/tripadvisor/`, `brave/config/`, `tests/fakes/`, `tests/unit/clients/`, `tests/unit/lanes/tripadvisor/`
**Files scanned:** 11 source files read directly
**Pattern extraction date:** 2026-06-25
