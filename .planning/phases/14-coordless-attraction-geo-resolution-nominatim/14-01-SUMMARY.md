---
phase: "14"
plan: "01"
subsystem: "geocoder-client"
tags: [nominatim, geocoder, client, redis-cache, rate-limit, lgpd, ta-14]
dependency_graph:
  requires: []
  provides:
    - "GeocoderClientProtocol in brave.clients.base (10th protocol)"
    - "NominatimGeocoderClient (real client) in brave.clients.nominatim"
    - "NullGeocoderClient (offline stub) in brave.clients.null_nominatim"
    - "NominatimConfig (BRAVE_NOMINATIM_* prefix) in brave.config.settings"
    - "FakeGeocoderClient (test fixture) in tests.fakes.fake_nominatim"
  affects:
    - "brave.config.settings.AppConfig (nominatim field added)"
    - "Wave 2 plan 14-02 (atrativos.py geo-enrichment wires FakeGeocoderClient)"
tech_stack:
  added: []
  patterns:
    - "respx.mock GET + side_effect for httpx client tests"
    - "fakeredis.FakeRedis for Redis cache unit tests"
    - "tenacity @retry on async geocode method"
    - "asyncio.sleep rate-limit enforcement (≥1 req/s Nominatim policy)"
    - "LGPD 4-key result dict (lat/lon/osm_id/municipio_name only)"
    - "__no_match sentinel for negative caching"
key_files:
  created:
    - brave/clients/nominatim.py
    - brave/clients/null_nominatim.py
    - tests/fakes/fake_nominatim.py
    - tests/unit/clients/test_nominatim.py
  modified:
    - brave/clients/base.py
    - brave/config/settings.py
decisions:
  - "UF_NAME dict is module-level in nominatim.py (not on NominatimConfig) — keeps config lean and avoids giant env var value"
  - "NominatimGeocoderClient uses self._config.base_url (not _NOMINATIM_SEARCH_URL constant) so tests can override via NominatimConfig(base_url=...) without env patching"
  - "test_rate_limit uses direct attribute injection (_last_request_ts) + monkeypatched asyncio.sleep to avoid real-time waits"
  - "7 TA-14 tests are top-level functions (not class methods) to produce exact node IDs matching 14-VALIDATION.md"
metrics:
  duration: "~12 minutes"
  completed: "2026-06-25"
  tasks_completed: 2
  tasks_total: 2
  files_created: 4
  files_modified: 2
  tests_added: 12
  tests_passing: 12
---

# Phase 14 Plan 01: Geocoder Client Triad Summary

**One-liner:** Nominatim geocoder client triad — async httpx + Redis cache + rate limit + LGPD 4-key result, with FakeGeocoderClient and 12 offline unit tests covering all 7 TA-14 behaviors.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Geocoder client triad + NominatimConfig | 9a8b161 | base.py, nominatim.py, null_nominatim.py, settings.py |
| 2 | FakeGeocoderClient + TA-14 unit tests | 144665a | fake_nominatim.py, test_nominatim.py |

## What Was Built

### Task 1: Client Triad

**`brave/clients/base.py`** — Updated module docstring from "Nine protocols" to "Ten protocols (CORE-11 + TA-01 + TA-14)". Appended `GeocoderClientProtocol` class with async `geocode(location_id, name, uf) → dict | None` signature and LGPD docstring.

**`brave/clients/nominatim.py`** — Real geocoder client:
- Module-level `UF_NAME` dict (all 27 BR state codes → full names)
- `NOMINATIM_CACHE_KEY_PREFIX = "brave:geo:"`, `NOMINATIM_CACHE_TTL = 86_400 * 30`
- `_decode()` helper (mirrors geo.py), `_is_retryable()` for tenacity
- `NominatimGeocoderClient.__init__`: run_real_externals guard (raises RuntimeError when False)
- `geocode()` with tenacity `@retry` (3 attempts, exponential backoff): Redis cache hit → rate-limit sleep → httpx GET → negative sentinel cache → LGPD 4-key result
- Address precedence: `municipality → city → town → village → county`
- `_check_protocol_compliance()` at module bottom

**`brave/clients/null_nominatim.py`** — `NullGeocoderClient.geocode` always returns None, zero network/Redis, `_check_protocol_compliance()`.

**`brave/config/settings.py`** — `NominatimConfig` (5 fields, `BRAVE_NOMINATIM_*` env prefix, zero `Field(alias=...)` per CR-02) inserted before `AppConfig`. `AppConfig.nominatim: NominatimConfig = Field(default_factory=NominatimConfig)` added.

### Task 2: Test Infrastructure

**`tests/fakes/fake_nominatim.py`** — `FakeGeocoderClient` records `geocode_calls` list, returns `fixture_results.get(location_id)`, `_check_protocol_compliance()`.

**`tests/unit/clients/test_nominatim.py`** — 12 tests, all offline:
- 7 top-level functions matching exact TA-14 node IDs from 14-VALIDATION.md
- `test_guard_raises`, `test_request_params`, `test_address_precedence`, `test_cache_by_location_id`, `test_rate_limit`, `test_null_returns_none`, `test_lgpd_no_pii`
- Plus `test_negative_result_cached`, `TestNullGeocoderClient`, `TestFakeGeocoderClient` (3 more tests)

## Verification Results

```
1. Full geocoder tests: 12 passed in 0.50s
2. FakeGeocoderClient protocol compliance: OK
3. NullGeocoderClient protocol compliance: OK
4. NominatimConfig prefix + AppConfig.nominatim: OK
5. Full unit suite: 432 passed, 5 skipped
```

## Deviations from Plan

**1. [Rule 1 - Bug] `self._config.base_url` used in httpx.get instead of module constant `_NOMINATIM_SEARCH_URL`**
- **Found during:** Task 1 (writing geocode body)
- **Issue:** The PATTERNS.md shows `hc.get(_NOMINATIM_SEARCH_URL, ...)` but tests need to override the URL for offline mocking. Using `self._config.base_url` (which defaults to the same value) makes the client testable without patching the module constant.
- **Fix:** `geocode()` calls `hc.get(self._config.base_url, ...)`. `NominatimConfig.base_url` defaults to `"https://nominatim.openstreetmap.org/search"` so production behavior is identical.
- **Files modified:** brave/clients/nominatim.py
- **Commit:** 9a8b161

**2. [Rule 2 - Missing functionality] `test_rate_limit` uses direct attribute injection + monkeypatch instead of pure clock mocking**
- **Found during:** Task 2 (writing test_rate_limit)
- **Issue:** The PATTERNS.md suggests mock time.monotonic + asyncio.sleep. Direct injection of `client._last_request_ts = now - 0.5` is more reliable for asserting sleep is called.
- **Fix:** Sets `_last_request_ts` attribute directly and patches `asyncio.sleep` to record calls.
- **Files modified:** tests/unit/clients/test_nominatim.py
- **Commit:** 144665a

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: T-14-01 (LGPD) | brave/clients/nominatim.py | Mitigated — result dict limited to exactly 4 keys; structlog never receives name or raw address; setex stores only 4-key dict |
| threat_flag: T-14-02 (Tampering) | brave/clients/nominatim.py | Mitigated — defensive `.get()` chains; float() conversion; empty data → None + sentinel |
| threat_flag: T-14-03 (DoS) | brave/clients/nominatim.py | Mitigated — 1.1s rate limit enforced; negative sentinel prevents repeated queries; identifiable User-Agent |
| threat_flag: T-14-05 (SSRF) | brave/clients/nominatim.py | Mitigated — base_url pinned as config default; not from user input; HTTPS only |

## Known Stubs

None — all code is fully wired. `NullGeocoderClient.geocode` returning `None` is intentional (offline/CI behavior, not a stub — matches the design contract for offline mode).

## Self-Check: PASSED

- [x] `brave/clients/nominatim.py` — exists
- [x] `brave/clients/null_nominatim.py` — exists
- [x] `tests/fakes/fake_nominatim.py` — exists
- [x] `tests/unit/clients/test_nominatim.py` — exists
- [x] `brave/clients/base.py` — modified (10 protocols)
- [x] `brave/config/settings.py` — modified (NominatimConfig + AppConfig.nominatim)
- [x] Task 1 commit 9a8b161 — exists
- [x] Task 2 commit 144665a — exists
- [x] 7 TA-14 exact node IDs pass
- [x] Full unit suite 432 passed, 5 skipped
