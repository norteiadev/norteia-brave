# Phase 14: Coordless attraction geo-resolution via Nominatim - Research

**Researched:** 2026-06-25
**Domain:** HTTP geocoding (OpenStreetMap Nominatim) + IBGE municipality matching, behind the existing typed network-boundary client pattern
**Confidence:** HIGH (approach spike-validated; all repo patterns read directly from source)

## Summary

Phase 13 left a known gap: AttractionsFusion listing cards carry no lat/lng, so `resolve_municipio` cannot run its haversine fallback, and attraction names ("Cachoeira do Tabuleiro", "Instituto Inhotim") don't fuzzy-match IBGE município names. The net result is that a real sweep's Nascente stays near 0 — every card quarantines as `ibge_unmatched`. Phase 14 inserts a geo-enrichment step **before** that quarantine: forward-geocode `name + UF + Brazil` through the public OpenStreetMap Nominatim `search` API, read the município name from `addressdetails`, and fall back to haversine on the returned lat/lon with a relaxed ~50 km radius. The spike (`scripts/spike_nominatim_geo.py`, 10 real BR attractions) geocoded 10/10 and resolved the correct município 9–10/10.

The work is two requirements: **TA-14** is a typed, mockable Nominatim client behind the network boundary (Protocol + Null + Fake, respx in tests, `RUN_REAL_EXTERNALS` opt-in, Redis cache by `locationId`, ≥1 req/s rate limit, custom User-Agent, LGPD: persist only lat/lon + OSM place id). **TA-15** is the atrativos integration: thread the geocoder into `TripAdvisorAtrativosIngest`, geo-enrich before quarantine, relax the haversine radius, and add a regression test proving a previously-quarantined coordless card now resolves.

**Primary recommendation:** Mirror the existing TripAdvisor client triad exactly. Add `GeocoderClientProtocol` to `brave/clients/base.py`; create `brave/clients/nominatim.py` (real, `@retry` tenacity, `run_real_externals` guard, raw httpx — no geocoding library), `brave/clients/null_nominatim.py` (returns no match), `tests/fakes/fake_nominatim.py` (fixture + call recording). Reuse `BRAVE_TA_*` config conventions with a new `NominatimConfig` (env_prefix `BRAVE_NOMINATIM_`, no `Field(alias=)` per CR-02). Cache by `locationId` in Redis (mirror `brave/lanes/tripadvisor/geo.py` key+TTL pattern). Inject the geocoder into `TripAdvisorAtrativosIngest.__init__` as an optional dependency (`None` → current behavior preserved, no Phase-11/13 regression).

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| TA-14 | Typed mockable Nominatim geocoding client behind network boundary: forward `search` + `addressdetails=1`, custom User-Agent, ≥1 req/s rate limit, Redis cache by `locationId`, Null/Fake, `RUN_REAL_EXTERNALS` opt-in, LGPD coords+OSM id only | Standard Stack (raw httpx, no geopy), Architecture Pattern 1 (client triad), Pattern 3 (rate limit + cache), Nominatim contract section |
| TA-15 | Atrativos geo-enrichment before `ibge_unmatched` quarantine: geocode → `address.municipality\|city\|town\|village\|county` → IBGE name-match within UF; haversine fallback relaxed ~50 km; offline respx tests + Level-3 re-validation | Architecture Pattern 2 (`resolve_municipio` extension + ingest threading), Pattern 4 (regression test), Validation Architecture |

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Forward-geocode name → lat/lon + address | API/Backend (collector network boundary) | — | All external calls live in the collector only (CLAUDE.md boundary rule); sits behind `GeocoderClientProtocol` like every other external |
| Rate-limit ≥1 req/s | API/Backend (per-process, in the client) | — | Nominatim policy is per-application; enforce in the real client, not in callers |
| Cache geocode by `locationId` | Database/Storage (Redis) | API/Backend | Mirrors `brave/lanes/tripadvisor/geo.py` Redis cache; re-sweeps hit cache (TA-14 + Nominatim caching mandate) |
| município name-match from address | API/Backend (`resolve_municipio` in `ibge.py`) | — | Pure-logic matcher; extend existing function, no new tier |
| Quarantine decision (`ibge_unmatched`) | API/Backend (`atrativos._ingest_one`) | — | Enrichment inserted before the existing quarantine call; ingest orchestrates |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `httpx` | 0.28.x (already pinned) | Async HTTP to Nominatim `search` | The repo's network-boundary HTTP client; respx mocks it deterministically. The spike used `urllib`; production must use the async `httpx` the rest of the lane uses. `[VERIFIED: brave/lanes/tripadvisor/client.py]` |
| `tenacity` | 9.1.x (already pinned) | Retry/backoff on 429/5xx | Same `@retry(retry=retry_if_exception(...), stop=stop_after_attempt(3), wait=wait_exponential(...))` decorator the real Places client uses. `[VERIFIED: brave/clients/places.py:223]` |
| `redis` (sync client) | already pinned | Cache geocode-by-`locationId` | Sync Redis handle is already threaded through the lane (geo.py, client.py). `[VERIFIED: brave/lanes/tripadvisor/geo.py]` |
| `rapidfuzz` | already pinned | município name-match within UF | `resolve_municipio` already uses `process.extractOne(scorer=fuzz.token_sort_ratio, processor=rfuzz_utils.default_process)`. Reuse for address→IBGE name match. `[VERIFIED: brave/lanes/tripadvisor/ibge.py:153]` |
| `structlog` | 26.x (already pinned) | Structured logging | Repo logging standard. LGPD: never log the address payload — log `locationId`, resolved município, cache hit/miss only. `[VERIFIED: brave/lanes/tripadvisor/client.py:43]` |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `respx` | 0.23.x (already pinned) | Mock the async httpx Nominatim call | All offline unit tests. `respx.post`/`respx.get(...).mock(side_effect=...)` — exact pattern in `tests/unit/lanes/tripadvisor/test_client.py:375`. `[VERIFIED: test_client.py]` |
| `fakeredis` | 2.36.x (already pinned) | In-process Redis for cache tests | Cache-hit/miss unit tests; `app_config`/`fake_redis` fixtures exist in `tests/conftest.py`. `[VERIFIED: tests/conftest.py]` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Raw `httpx` + hand-built query | `geopy` (`Nominatim` geocoder) | `geopy` bundles a rate limiter and UA handling, BUT: (a) it is **not** mockable by respx the way the repo mocks every other client, (b) it adds a dependency for ~15 lines of query-building, (c) it wraps a sync/async story that doesn't match the repo's async-httpx boundary. **Do NOT adopt** — stay consistent with the existing client pattern. `[ASSUMED]` |
| Public Nominatim instance | Self-hosted Nominatim | Explicitly **out of scope** (CONTEXT.md): public instance is acceptable for operator-gated, deliberately-slow sweeps. Revisit only if volume/ToS forces it. `[CITED: 14-CONTEXT.md]` |

**Installation:** No new packages — every dependency is already pinned in `pyproject.toml`. (Confirm by reading `pyproject.toml` before planning; if `httpx`/`tenacity`/`rapidfuzz`/`respx`/`fakeredis` are present, no install task is needed.)

**Version verification:** Versions above are from CLAUDE.md's Recommended Stack table (sourced from `pip index versions` at project setup, marked HIGH). No new package is introduced, so no fresh registry check is required for Phase 14.

## Package Legitimacy Audit

> Not applicable — Phase 14 installs **no new external packages**. Every library used (`httpx`, `tenacity`, `redis`, `rapidfuzz`, `structlog`, `respx`, `fakeredis`) is already a project dependency vetted at project setup. No slopcheck run required.

If the planner discovers a missing pin during planning (e.g., `httpx` not actually in `pyproject.toml`), gate that single install behind a `checkpoint:human-verify` task and run `pip index versions <pkg>` against PyPI.

## Architecture Patterns

### System Architecture Diagram

```
                  TripAdvisorAtrativosIngest.produce(uf)
                              │
                              ▼
                     _ingest_one(uf, card)
                              │
                  ┌───────────┴───────────────────────────┐
                  │  resolve_municipio(name, uf, records,  │
                  │     candidate_lat=None, candidate_lng=None)
                  │   1. rapidfuzz name-match (threshold 88)│  ── match ──► ibge_match
                  │   2. haversine (no coords → skip)       │
                  └───────────┬───────────────────────────┘
                              │ None  (the Phase-13 gap)
                              ▼
              ┌──────────────────────────────────────────────┐
              │  NEW: geo-enrichment (TA-15)                  │
              │  geocoder.geocode(location_id, name, uf)      │
              │       │                                       │
              │       ▼ (Redis cache by locationId — hit?)    │
              │   ┌─────────────┐  miss   ┌──────────────────┐│
              │   │ Redis cache │ ──────► │ NominatimClient  ││
              │   │ brave:geo:* │ ◄────── │ httpx GET search ││  ≥1 req/s, UA,
              │   └─────────────┘  store  │ addressdetails=1 ││  countrycodes=br
              │       │                   └──────────────────┘│
              │       ▼                                        │
              │   {lat, lon, osm_id, address.{municipality|   │
              │    city|town|village|county}}                 │
              │       │                                        │
              │       ▼                                        │
              │   resolve_municipio(addr_muni_name, uf, recs, │
              │       candidate_lat=lat, candidate_lng=lon,    │
              │       max_distance_km=50)  ── 1. name-match    │
              │                            ── 2. haversine 50km│
              └───────────────┬──────────────────────────────┘
                  ┌───────────┴───────────┐
            match │                       │ still None
                  ▼                       ▼
              ibge_match            quarantine "ibge_unmatched"
                  │                  (only after BOTH fail)
                  ▼
        parent destino lookup → store_raw → Rio pipeline
```

### Recommended Project Structure
```
brave/clients/
├── base.py                 # ADD: GeocoderClientProtocol (mirror PlacesClientProtocol)
├── nominatim.py            # NEW: NominatimGeocoderClient (real, httpx, tenacity, guard, cache)
└── null_nominatim.py       # NEW: NullGeocoderClient (returns no match — offline/CI default)

brave/config/settings.py    # ADD: NominatimConfig (env_prefix BRAVE_NOMINATIM_); nest in AppConfig
brave/lanes/tripadvisor/
├── ibge.py                 # EDIT: resolve_municipio — relaxed default radius is fine; threading
└── atrativos.py            # EDIT: __init__ accepts optional geocoder; _ingest_one geo-enriches

tests/fakes/
└── fake_nominatim.py       # NEW: FakeGeocoderClient (fixtures + call recording)
tests/unit/clients/
├── test_nominatim.py       # NEW: respx-mocked real client + guard + cache tests
tests/unit/lanes/tripadvisor/
└── test_atrativos.py       # EDIT: regression test — coordless card resolves via geo-enrichment
```

**Decision — where the geocoder lives:** Put the client in `brave/clients/` (not `brave/lanes/tripadvisor/`). Geocoding is a generic external system like Places, not TripAdvisor-specific. This also matches the Protocol home (`brave/clients/base.py`) and keeps the D-18 import rule clean (atrativos may import from `brave.clients`). The Redis **cache helper** (key naming `brave:geo:{locationId}`, TTL) can live alongside the client in `nominatim.py` or in a small `brave/lanes/tripadvisor/` cache module — mirror `geo.py`'s `REDIS_GEO_KEY_PREFIX` + `setex` style.

### Pattern 1: Network-boundary client triad (Protocol + Null + Fake + Real)
**What:** Every external system has a `typing.Protocol` in `base.py`, a production-safe `Null*` stub in `brave/clients/`, a `Fake*` (fixtures + call recording) in `tests/fakes/`, and a `Real*` client guarded by `run_real_externals`.
**When to use:** TA-14 — the geocoder is the 10th such system.
**Example (Protocol — add to `brave/clients/base.py`):**
```python
# Source: brave/clients/base.py (PlacesClientProtocol pattern)
class GeocoderClientProtocol(Protocol):
    """OpenStreetMap Nominatim forward-geocoder (TA-14).

    LGPD: returns ONLY coordinates + OSM place id + the município-level
    address sub-fields needed for IBGE matching. No reviewer/PII data.
    """
    async def geocode(self, location_id: str, name: str, uf: str) -> dict[str, Any] | None:
        """Forward-geocode `name + UF + Brazil` → geo dict or None.

        Returns (on hit): {"lat": float, "lon": float, "osm_id": int,
            "municipio_name": str | None}  # first of address.municipality
            |city|town|village|county. None on no result.
        Caches by location_id in Redis (one Nominatim call per attraction).
        """
        ...
```
**Real client guard (mirror exactly):**
```python
# Source: brave/clients/places.py:181-199
def __init__(self, config: "NominatimConfig", redis: Any) -> None:
    from brave.config.settings import AppConfig
    if not AppConfig().run_real_externals:
        raise RuntimeError(
            "NominatimGeocoderClient: run_real_externals=False — "
            "use NullGeocoderClient / FakeGeocoderClient in the default test suite. "
            "Set RUN_REAL_EXTERNALS=true to enable real API calls."
        )
    self._config = config
    self._redis = redis
```
**Structural compliance check (every real/null/fake file has one):**
```python
# Source: brave/clients/null_tripadvisor.py:66
def _check_protocol_compliance() -> None:
    from brave.clients.base import GeocoderClientProtocol
    _c: GeocoderClientProtocol = NullGeocoderClient()  # noqa: F841
```

### Pattern 2: `resolve_municipio` extension + ingest threading (TA-15)
**What:** Two locked decisions: (1) geo-enrichment is an **enrichment step inserted before** the existing `ibge_unmatched` quarantine, not a rewrite of the matcher; (2) the haversine radius relaxes to ~50 km. `resolve_municipio` **already accepts** `candidate_lat`/`candidate_lng`/`max_distance_km` — the cleanest path keeps the matcher's signature and passes the geocoded coords + a relaxed radius from the ingest path.
**When to use:** atrativos `_ingest_one` after the first `resolve_municipio` returns `None`.
**Recommended approach — orchestrate in `_ingest_one`, keep the matcher pure:**
```python
# Source: composed from brave/lanes/tripadvisor/atrativos.py:173 + ibge.py:112
# 1. existing first attempt (name fuzzy-match, no coords → haversine skipped)
ibge_match = resolve_municipio(name, uf, self._ibge_records,
                               candidate_lat=lat, candidate_lng=lng)

# 2. NEW geo-enrichment before quarantine (only if geocoder injected and first attempt missed)
if ibge_match is None and self._geocoder is not None:
    geo = await self._geocoder.geocode(location_id, name, uf)  # cached by locationId
    if geo is not None:
        ibge_match = resolve_municipio(
            geo.get("municipio_name") or name,          # primary: name from address
            uf, self._ibge_records,
            candidate_lat=geo["lat"], candidate_lng=geo["lon"],  # secondary: haversine
            max_distance_km=50.0,                        # relaxed (spike: seats 15-25km out)
        )

if ibge_match is None:
    quarantine_poison(... task_name="brave.ta.atrativos.ibge_unmatched" ...)
    return
```
**Threading the dependency (no Phase-11/13 regression):** add `geocoder: "GeocoderClientProtocol | None" = None` as the **last** `__init__` arg of `TripAdvisorAtrativosIngest`, defaulting to `None`. When `None`, behavior is byte-identical to today — every existing destinos/atrativos test that constructs the ingest without a geocoder is unaffected. The construction site `brave/tasks/pipeline.py:1010` passes the real/null geocoder based on `run_real_externals` (mirror how `ta_client` is selected).
**`_ingest_one` is currently sync but called from async `produce`.** It calls no awaitables today. Geo-enrichment needs `await geocoder.geocode(...)`. Two options for the planner: (a) make `_ingest_one` `async` and `await` it in `produce`'s loop, or (b) keep `_ingest_one` sync and do the (sync-Redis-cached) geocode via a sync wrapper. **Recommend (a)** — `produce` is already `async`, and the rest of the boundary is async; keeping the call async matches httpx. Verify no other caller invokes `_ingest_one` synchronously (grep shows only `produce` and `test_atrativos.py`).

### Pattern 3: Rate-limit (≥1 req/s) + Redis cache-by-locationId
**What:** Nominatim policy is **≤1 req/s, single thread, results MUST be cached.** Enforce the rate limit inside the real client (per-process); cache by `locationId` so re-sweeps never re-hit Nominatim.
**Rate limit (per-process, simplest correct form):**
```python
# Source: spike used time.sleep(1.2); async equivalent in the real client
import time
# class-level: self._min_interval = 1.1  # seconds (≥1s policy + margin)
elapsed = time.monotonic() - self._last_request_ts
if elapsed < self._min_interval:
    await asyncio.sleep(self._min_interval - elapsed)
self._last_request_ts = time.monotonic()
```
**Cache (mirror `brave/lanes/tripadvisor/geo.py:92-106`):**
```python
# Source: brave/lanes/tripadvisor/geo.py (REDIS_GEO_KEY_PREFIX + setex + _decode)
GEO_CACHE_KEY_PREFIX = "brave:geo:"        # full key: f"brave:geo:{location_id}"
GEO_CACHE_TTL = 86_400 * 30                # geocodes are stable; 30d is safe (tune in config)

key = f"{GEO_CACHE_KEY_PREFIX}{location_id}"
raw = self._redis.get(key)                 # hit → json.loads, no Nominatim call
if raw:
    return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
# ... miss → call Nominatim, then:
self._redis.setex(key, GEO_CACHE_TTL, json.dumps(result))  # cache (or cache a "no-match" sentinel)
```
**Cache the negative result too** (a `None`/sentinel) so repeated coordless misses don't re-hit Nominatim every sweep — the policy explicitly blocks clients that "send repeatedly the same query."
**Redis handle:** the sync Redis client is already available in the construction path (`pipeline.py` builds clients with redis; `geo.py`/`client.py` take `redis: Any`). Thread the same handle into the geocoder.

### Pattern 4: Offline regression test (the proof TA-15 works)
**What:** A test that builds a coordless card whose name does NOT fuzzy-match any IBGE município (e.g. "Cachoeira do Tabuleiro", MG), injects a `FakeGeocoderClient` returning the spike's address+coords for it, and asserts the card now **resolves to the correct município** (Conceição do Mato Dentro) instead of quarantining. Mirror `test_atrativos.py`'s `_make_card`/`_make_fake_client` helpers and the `patch(store_raw)` / `patch(process_nascente_record)` style.

### Anti-Patterns to Avoid
- **Calling the real Nominatim endpoint in the default suite:** every unit test must respx-mock `nominatim.openstreetmap.org` or use `FakeGeocoderClient`. Real calls only under `RUN_REAL_EXTERNALS` + a marker. (TEST-01, CLAUDE.md.)
- **Adding `geopy`:** breaks respx-mockability and the consistent client pattern. Use raw httpx.
- **Rewriting `resolve_municipio`'s default `max_distance_km=15.0`:** do NOT change the default — destinos and existing tests rely on 15 km. Pass `max_distance_km=50.0` only from the geo-enrichment call site. (Prevents Phase-11/13 regression.)
- **Logging the Nominatim address payload:** LGPD — the address object can contain street/house data. Log only `locationId`, resolved município, cache hit/miss. Persist only `lat`, `lon`, `osm_id`.
- **Reading the geocoder as a required arg:** must default to `None` so existing ingest construction is unaffected.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP retry/backoff on 429/5xx | Custom retry loop | `tenacity @retry` (Places client pattern) | Already the repo standard; `retry_if_exception(_is_retryable)` + `wait_exponential` |
| município name normalization (accents/case) | Custom unicode stripping | `rapidfuzz` `default_process` in `resolve_municipio` | Already handles 'Sao Paulo'↔'São Paulo'; reuse the existing matcher |
| Haversine distance | New math | `haversine_km` in `ibge.py` | Pure-math impl already there; pass the geocoded coords |
| Redis cache w/ TTL + bytes-decode | Ad-hoc dict | `geo.py` `setex` + `_decode` pattern | Proven key-prefix/TTL/decode idiom in the same lane |
| Query string building | String concat | The spike's `f"{name}, {UF_NAME[uf]}, Brazil"` + `urlencode`/httpx params | Spike-validated 10/10; UF→full-name map already in the spike |

**Key insight:** Phase 14 introduces almost no new logic — it composes existing repo primitives (client triad, tenacity, rapidfuzz, haversine, Redis cache) behind one new Protocol. The only genuinely new code is the Nominatim query/response parsing and the rate limiter.

## Common Pitfalls

### Pitfall 1: IBGE coords are the município *seat*, not the attraction
**What goes wrong:** Natural attractions sit 15–25 km from the seat (Cataratas 22 km, Lençóis 25 km), so the default `max_distance_km=15` haversine never fires.
**Why it happens:** IBGE CSV stores the seat centroid; Nominatim returns the attraction's true point.
**How to avoid:** Pass `max_distance_km=50.0` from the geo-enrichment call site (spike-calibrated). Prefer the **address name-match** first (more precise); haversine is the fallback.
**Warning signs:** A geocode succeeds (coords returned) but the card still quarantines — radius too tight.

### Pitfall 2: Multi-município national parks
**What goes wrong:** "Lençóis Maranhenses" geocodes to Santo Amaro (centroid) but the expected seat is Barreirinhas — a genuine multi-município feature, not a defect.
**Why it happens:** Some attractions span several municípios; any single resolution is defensible.
**How to avoid:** Accept this as correct-enough; do NOT tune thresholds to chase it. Document it as a known acceptable variance (CONTEXT.md already flags it). The Level-3 success bar is "Nascente > 0 with municípios resolved," not "100% seat-exact."
**Warning signs:** Trying to special-case national parks — stop; it's out of scope.

### Pitfall 3: Hitting the real endpoint in CI / blowing the rate limit
**What goes wrong:** A test forgets to mock; CI hits `nominatim.openstreetmap.org`, which is slow and may block the IP.
**Why it happens:** respx mock scope omitted, or `FakeGeocoderClient` not injected.
**How to avoid:** `run_real_externals` guard in the real client (`RuntimeError` if False) + respx mock in every unit test + `NullGeocoderClient` as the CI default. Add a marker (`real_browser` exists; consider `real_nominatim` or reuse `real_browser`) gated by `RUN_REAL_EXTERNALS` in `conftest.py`.
**Warning signs:** Test latency > 1 s/case; intermittent 403/429 in CI logs.

### Pitfall 4: Nominatim caching mandate
**What goes wrong:** Re-sweeping a UF re-geocodes every attraction → "repeated same query" → IP classified faulty and blocked.
**Why it happens:** No cache, or cache keyed on something that changes per sweep.
**How to avoid:** Cache by stable `locationId` with a long TTL (30 d). Cache negative results too. (Policy: "Results must be cached on your side.")
**Warning signs:** Nominatim call count ≈ attraction count on a *re*-sweep (should be ≈0 on the second pass).

### Pitfall 5: `_ingest_one` sync/async boundary
**What goes wrong:** Adding `await geocoder.geocode(...)` inside a sync `_ingest_one` raises `SyntaxError`/runtime error.
**Why it happens:** `_ingest_one` is currently sync.
**How to avoid:** Promote `_ingest_one` to `async def` and `await` it in `produce`'s loop (only caller besides tests). Update `test_atrativos.py` call sites.
**Warning signs:** `RuntimeWarning: coroutine ... never awaited`.

## Code Examples

### Nominatim `search` request (production httpx form)
```python
# Source: scripts/spike_nominatim_geo.py (validated 10/10) + brave httpx pattern
UF_NAME = {"RJ": "Rio de Janeiro", "MG": "Minas Gerais", ...}  # from spike

q = f"{name}, {UF_NAME.get(uf, uf)}, Brazil"
params = {
    "q": q,
    "format": "json",
    "limit": 1,
    "countrycodes": "br",
    "addressdetails": 1,          # NEW vs spike: gives address.municipality|city|town|...
}
headers = {"User-Agent": "norteia-brave/1.0 (leandro.freire08@gmail.com)"}  # policy-required
async with httpx.AsyncClient(timeout=15) as hc:
    resp = await hc.get("https://nominatim.openstreetmap.org/search",
                        params=params, headers=headers)
resp.raise_for_status()
data = resp.json()
if not data:
    return None
hit = data[0]
addr = hit.get("address", {})
municipio_name = (addr.get("municipality") or addr.get("city") or addr.get("town")
                  or addr.get("village") or addr.get("county"))  # locked precedence
result = {"lat": float(hit["lat"]), "lon": float(hit["lon"]),
          "osm_id": hit.get("osm_id"), "municipio_name": municipio_name}
```

### Nominatim response shape (relevant fields, `addressdetails=1`)
```json
{
  "lat": "-19.0469", "lon": "-43.4256",
  "osm_id": 123456789, "osm_type": "node",
  "display_name": "Cachoeira do Tabuleiro, Conceição do Mato Dentro, ...",
  "address": {
    "municipality": "Conceição do Mato Dentro",   // ← primary
    "city": "...", "town": "...", "village": "...", "county": "...",
    "state": "Minas Gerais", "country": "Brazil", "country_code": "br"
  }
}
```
*(LGPD: keep only `lat`, `lon`, `osm_id`, and the chosen `municipio_name`. Drop `display_name`/street fields.)*

### respx mock for the geocoder unit test
```python
# Source: tests/unit/lanes/tripadvisor/test_client.py:375 (respx side_effect pattern)
import httpx, respx
with respx.mock:
    respx.get("https://nominatim.openstreetmap.org/search").mock(
        return_value=httpx.Response(200, json=[{
            "lat": "-19.0469", "lon": "-43.4256", "osm_id": 123,
            "address": {"municipality": "Conceição do Mato Dentro",
                        "state": "Minas Gerais", "country_code": "br"},
        }])
    )
    geo = await client.geocode("312332", "Cachoeira do Tabuleiro", "MG")
    assert geo["municipio_name"] == "Conceição do Mato Dentro"
```

## Runtime State Inventory

> Phase 14 adds an enrichment step + a new cache key; it is **not** a rename/refactor. Inventory included for the one stateful surface it introduces (Redis cache).

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | Nascente attraction payloads now gain `lat`/`lon`/`osm_id` for geo-enriched cards (previously None) | Code edit only — new cards carry coords; no migration of past records (past coordless cards were quarantined, not stored) |
| Live service config | None — no external service config embeds new state | None |
| OS-registered state | None | None — verified: no OS-level registration in this lane |
| Secrets/env vars | New `BRAVE_NOMINATIM_*` env vars (User-Agent, base URL, rate interval, TTL). No secret — Nominatim needs no API key | Add `NominatimConfig`; document vars |
| Build artifacts | None | None |
| **New Redis keys** | `brave:geo:{locationId}` (geocode cache, 30d TTL) | New key namespace — document; safe to flush (re-geocodes on miss) |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.x (`asyncio_mode = "auto"`, `respx`, `fakeredis`) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]`; shared fixtures in `tests/conftest.py` |
| Quick run command | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_atrativos.py tests/unit/clients/test_nominatim.py -x` |
| Full suite command | `RUN_REAL_EXTERNALS= .venv/bin/python -m pytest` (unset the real-externals flag — see MEMORY: sourcing .env can flip it on) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TA-14 | Real client raises `RuntimeError` when `run_real_externals=False` | unit | `pytest tests/unit/clients/test_nominatim.py::test_guard_raises -x` | ❌ Wave 0 |
| TA-14 | `geocode` sends UA + `addressdetails=1` + `countrycodes=br` (respx captures request) | unit | `pytest tests/unit/clients/test_nominatim.py::test_request_params -x` | ❌ Wave 0 |
| TA-14 | Address precedence municipality→city→town→village→county parsed correctly | unit | `pytest tests/unit/clients/test_nominatim.py::test_address_precedence -x` | ❌ Wave 0 |
| TA-14 | Redis cache hit on second call → no second httpx request (respx call count == 1) | unit | `pytest tests/unit/clients/test_nominatim.py::test_cache_by_location_id -x` | ❌ Wave 0 |
| TA-14 | Rate limit ≥1 req/s enforced (mock clock / asserts sleep called) | unit | `pytest tests/unit/clients/test_nominatim.py::test_rate_limit -x` | ❌ Wave 0 |
| TA-14 | `NullGeocoderClient.geocode` returns None, no network | unit | `pytest tests/unit/clients/test_nominatim.py::test_null_returns_none -x` | ❌ Wave 0 |
| TA-14 | LGPD: result dict has only lat/lon/osm_id/municipio_name (no street/PII keys) | unit | `pytest tests/unit/clients/test_nominatim.py::test_lgpd_no_pii -x` | ❌ Wave 0 |
| TA-15 | **Regression:** coordless card that previously quarantined now resolves to correct município via geo-enrichment | unit | `pytest tests/unit/lanes/tripadvisor/test_atrativos.py::test_coordless_resolves_via_geo -x` | ❌ Wave 0 |
| TA-15 | Quarantine `ibge_unmatched` fires only after BOTH name-match AND geo-enrichment fail | unit | `pytest tests/unit/lanes/tripadvisor/test_atrativos.py::test_quarantine_after_both_fail -x` | ❌ Wave 0 |
| TA-15 | `geocoder=None` → existing behavior unchanged (no regression) | unit | `pytest tests/unit/lanes/tripadvisor/test_atrativos.py::test_no_geocoder_unchanged -x` | ⚠️ extend existing |
| TA-15 | Relaxed 50 km radius passed at geo-enrichment call site; default 15 km unchanged for destinos | unit | `pytest tests/unit/lanes/tripadvisor/test_ibge.py -x` | ⚠️ extend existing |

### Sampling Rate
- **Per task commit:** quick run command above (atrativos + nominatim client tests).
- **Per wave merge:** full suite (`RUN_REAL_EXTERNALS=` unset) — must be green, zero real network (pytest-socket / respx enforce).
- **Phase gate:** full suite green, then `/gsd:verify-work`.
- **Level-3 (operator, real, gated):** a real MG sweep (`RUN_REAL_EXTERNALS=1`, session injected, mirrors Phase-13 runbook) → assert Nascente `entity_type='attraction'` count > 0 with municípios resolved (not mass-quarantined). Observable signals: geocode hit-rate (target ~10/10 from spike), município-match accuracy (~9–10/10), cache-hit ≈100% on a second sweep pass, Nominatim call count ≈0 on re-sweep, zero real network in CI.

### Wave 0 Gaps
- [ ] `tests/unit/clients/test_nominatim.py` — covers TA-14 (guard, params, precedence, cache, rate-limit, null, LGPD)
- [ ] `tests/fakes/fake_nominatim.py` — `FakeGeocoderClient` fixture + call recording (needed by the TA-15 regression test)
- [ ] Extend `tests/unit/lanes/tripadvisor/test_atrativos.py` — regression + both-fail + no-geocoder-unchanged cases
- [ ] Optional: add a `real_nominatim` marker in `conftest.py` gated by `RUN_REAL_EXTERNALS` (or reuse `real_browser`) for the opt-in live geocode test
- [ ] No framework install needed — pytest/respx/fakeredis already present

## Security Domain

> `security_enforcement` is absent from `.planning/config.json` → treated as enabled. Phase 14 is low-attack-surface (read-only outbound GET to a public, keyless endpoint) but two controls matter.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Nominatim public API needs no credentials |
| V3 Session Management | no | Stateless GET |
| V4 Access Control | no | No new endpoint exposed |
| V5 Input Validation | yes | Validate/normalize `name`/`uf` before building the query; parse the JSON response defensively (`.get` chains, never assume keys) — mirror `_parse_attractions_page`'s try/skip style |
| V6 Cryptography | no | No secrets; HTTPS to nominatim.openstreetmap.org (httpx default) |
| V8/V9 Data Protection (LGPD) | yes | Persist only lat/lon/osm_id; never store/log address PII; cache value scrubbed to the 4 allowed fields |

### Known Threat Patterns for {Python httpx + public geocoder}
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| PII leakage via stored/logged address | Information disclosure | LGPD scrub: result dict = {lat, lon, osm_id, municipio_name} only; structlog never receives the raw address |
| Untrusted JSON response (malformed/oversized) | Tampering / DoS | Defensive `.get` parsing + `timeout=15` + `limit=1`; cache negative result |
| IP block from policy violation | Denial of Service (self-inflicted) | ≥1 req/s rate limit, single-thread, mandatory cache, identifiable User-Agent |
| HTTPS downgrade | Tampering | httpx upgrades to HTTPS; pin `https://` scheme in config base URL |

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Quarantine coordless cards as `ibge_unmatched` (Phase 13) | Geo-enrich via Nominatim before quarantine (Phase 14) | 2026-06-25 | Real MG sweep Nascente moves from ~0 to >0 |
| `resolve_municipio` haversine only when card has coords | Geocoder supplies coords → haversine fallback now reachable | this phase | Closes the Phase-13 gap |

**Deprecated/outdated:** The spike's `urllib` + blocking `time.sleep(1.2)` is **prototype only** — production uses async httpx + the in-client rate limiter + Redis cache.

## Project Constraints (from CLAUDE.md)
- **Tests never hit real externals by default:** real = opt-in flag (`RUN_REAL_EXTERNALS`); CI runs keyless. Nominatim needs no key, but the same gate applies.
- **respx for httpx mocks** (not VCR) for deterministic client mocks.
- **structlog** structured logging; secrets/PII never logged.
- **LGPD:** persist only coords + OSM place id; no address PII (locked CONTEXT decision #8).
- **CR-02:** no `Field(alias=...)` on any config field — `NominatimConfig` resolves only from exact `BRAVE_NOMINATIM_*` names.
- **D-18 import rule:** lane modules import only from `brave.core`, `brave.clients`, `brave.config`, `brave.lanes.tripadvisor.*`. Geocoder in `brave/clients/` keeps this clean.
- **GSD workflow:** edits go through a GSD command, not direct repo edits.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Reject `geopy`; use raw httpx for respx-mockability | Alternatives | Low — if planner prefers geopy, mocking story changes but outcome same; recommend httpx |
| A2 | Promote `_ingest_one` to `async` (only caller is `produce` + tests) | Pattern 2 / Pitfall 5 | Medium — verify no other sync caller exists (grep shows none) before committing |
| A3 | 30-day cache TTL for geocodes | Pattern 3 | Low — geocodes are stable; TTL is a tunable config knob |
| A4 | `addressdetails=1` returns `municipality` as a top-level `address` key for BR | Code Examples | Low — spike confirmed name-match worked; OSM BR coverage returns admin-level-2 in `municipality`/`city`. Confirm against one real response during build |
| A5 | Rate limiter can be per-process (single worker) rather than Redis-distributed | Pattern 3 | Medium — sweep is single-machine/operator-gated (CONTEXT scope), so per-process is sufficient; revisit if sweeps fan out across workers |
| A6 | No new package needed (all deps pinned) | Standard Stack | Low — verify `pyproject.toml` during planning |

## Open Questions

1. **Exact `address` key for a few BR municípios under `addressdetails=1`**
   - What we know: spike's name-match hit 4/5 sampled; precedence municipality→city→town→village→county is the locked order.
   - What's unclear: which sub-key actually populates for each município class (capital vs interior) — OSM tagging varies.
   - Recommendation: capture one real response per município class during the build (operator/Level-3) and assert the precedence chain handles all; the fallback haversine covers any name-match miss.

2. **Rate-limit scope (per-process vs distributed) if sweeps ever parallelize**
   - What we know: current sweeps are operator-gated, single-machine, ~30 attractions/UF.
   - What's unclear: whether a future multi-worker fan-out would exceed 1 req/s across processes.
   - Recommendation: ship per-process now (sufficient for scope); document a Redis token-bucket as the upgrade if/when sweeps parallelize.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Public Nominatim endpoint | TA-14 real geocode (opt-in only) | ✓ (public, keyless) | n/a | `NullGeocoderClient` (CI/default) — no fallback needed offline |
| Redis (cache) | TA-14 cache-by-locationId | ✓ (already in stack) | 7/8.x | `fakeredis` in unit tests |
| httpx / tenacity / rapidfuzz / respx / fakeredis | TA-14/TA-15 | ✓ pinned | per CLAUDE.md | — |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** None — default test path uses Null/Fake + fakeredis, no real Nominatim or Redis container required.

## Sources

### Primary (HIGH confidence)
- `scripts/spike_nominatim_geo.py` — validated forward-geocode flow, UF→name map, ≥1 req/s, 10/10 geocoded
- `brave/clients/base.py`, `brave/clients/null_tripadvisor.py`, `tests/fakes/fake_tripadvisor.py` — Protocol+Null+Fake triad
- `brave/clients/places.py:181-228` — `run_real_externals` guard + tenacity `@retry` pattern
- `brave/lanes/tripadvisor/client.py` — async httpx + respx-mockable boundary
- `brave/lanes/tripadvisor/geo.py:92-106` — Redis cache key-prefix/TTL/`setex`/`_decode` pattern
- `brave/lanes/tripadvisor/ibge.py` — `resolve_municipio` (already accepts coords + `max_distance_km`), `haversine_km`
- `brave/lanes/tripadvisor/atrativos.py:135-188` — `_ingest_one` integration point + quarantine call
- `brave/config/settings.py:232-287` — `TripAdvisorConfig` / pydantic-settings / CR-02 no-alias pattern
- `tests/conftest.py`, `tests/unit/lanes/tripadvisor/test_client.py:375` — respx side_effect mock + marker gating
- `.planning/phases/14-.../14-CONTEXT.md`, `.planning/REQUIREMENTS.md` (TA-14/TA-15) — locked decisions

### Secondary (MEDIUM confidence)
- operations.osmfoundation.org/policies/nominatim/ — usage policy: ≤1 req/s, UA/Referer required, single-thread, results MUST be cached, no bulk/systematic queries

### Tertiary (LOW confidence)
- None.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; every primitive read from repo source.
- Architecture: HIGH — mirrors an existing 9-client pattern; spike proves the flow.
- Pitfalls: HIGH — seat-distance/multi-município pitfalls are spike-observed, not assumed.
- Nominatim policy: MEDIUM-HIGH — fetched from the official OSMF policy page.

**Research date:** 2026-06-25
**Valid until:** 2026-07-25 (stable — internal patterns; Nominatim policy is long-stable)
