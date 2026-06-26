---
phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
plan: 05
subsystem: tripadvisor-lane / geocoding
tags: [geocoding, nominatim, ibge, haversine, lgpd, national-uf, TA-12]
requires:
  - "brave/clients/base.py::GeocoderClientProtocol.geocode_national (stub, 15-02)"
  - "tests/fakes/fake_nominatim.py::FakeGeocoderClient.geocode_national (15-02)"
  - "brave/clients/nominatim.py::NominatimGeocoderClient.geocode (TA-14)"
  - "brave/lanes/tripadvisor/ibge.py::haversine_km, IbgeMunicipio (TA-03)"
provides:
  - "brave/clients/nominatim.py::NominatimGeocoderClient.geocode_national"
  - "brave/lanes/tripadvisor/ibge.py::resolve_municipio_national"
affects:
  - "15-06 bulk all-Brazil attractions lane (consumes both primitives)"
tech-stack:
  added: []
  patterns:
    - "National geocode mirrors per-UF geocode with a namespaced cache key (brave:geo:natl:{id})"
    - "Pure-haversine nearest-seat resolution over all IBGE records (no UF filter, no library)"
key-files:
  created:
    - "tests/unit/lanes/tripadvisor/test_geo_national.py"
  modified:
    - "brave/clients/nominatim.py"
    - "brave/lanes/tripadvisor/ibge.py"
decisions:
  - "National cache key namespaced as brave:geo:natl:{location_id} so a national result never collides with a per-UF geocode for the same id"
  - "resolve_municipio_national default radius 50.0 km (relaxed) — IBGE coords are the município seat; natural attractions sit ~15-25 km out (Phase 14)"
metrics:
  duration: "~15 min"
  completed: "2026-06-26"
  tasks: 2
  files: 3
requirements-completed: [TA-12]
---

# Phase 15 Plan 05: National Geo Primitives Summary

Two reuse-friendly geo primitives that let the all-Brazil bulk attractions lane derive `uf` + município from a geocoded card alone — national forward-geocode (`"{name}, Brazil"`, no UF) plus a pure-haversine nearest-IBGE-seat resolver over all 5570 records — resolving the BLOCKING national-UF gap with no parent-destino dependency.

## What Was Built

### Task 1 — `NominatimGeocoderClient.geocode_national` (`brave/clients/nominatim.py`)
- `async def geocode_national(self, location_id, name) -> dict | None` mirroring `geocode` but with `q = f"{name}, Brazil"` and no UF segment.
- Distinct cache key `f"{NOMINATIM_CACHE_KEY_PREFIX}natl:{location_id}"` (`brave:geo:natl:{id}`) so a national result never collides with a per-UF geocode for the same location.
- Reuses the existing `min_request_interval` rate-limit, the `@retry(_is_retryable)` tenacity policy, the `{"__no_match": true}` negative sentinel, the municipality→city→town→village→county precedence chain, and the `config.cache_ttl` setex (CR-01 discipline).
- Returns EXACTLY the 4 LGPD-safe keys (`lat`, `lon`, `osm_id`, `municipio_name`); logs only `location_id` + resolved `municipio` (never name/address). Matches the protocol/null/fake stubs added in 15-02 — signature unchanged.

### Task 2 — `resolve_municipio_national` (`brave/lanes/tripadvisor/ibge.py`)
- `resolve_municipio_national(candidate_lat, candidate_lng, records, *, max_distance_km=50.0) -> IbgeMunicipio | None`.
- Pure haversine over ALL records (no UF filter), picks the global minimum-distance seat, returns it only within `max_distance_km`. Reuses the existing `haversine_km` — no external library.
- The returned record carries `.uf` (derived state) and `.ibge_code` (município) — no per-UF input needed. None coords / beyond-radius → None.

Together these resolve the national-UF blocker: a whole-Brazil attraction with no parent destino gets uf+município from its coordinates.

## Tests

`tests/unit/lanes/tripadvisor/test_geo_national.py` (11 tests, 100% offline, respx + fakeredis, `RUN_REAL_EXTERNALS` unset):
- geocode_national: 4-key result + `q == "Instituto Inhotim, Brazil"`; second call cache-hit (respx count==1); namespaced key populated while per-UF key stays empty; empty response → None + `__no_match` sentinel in the national key; LGPD exactly-4-keys; address-precedence (county fallback).
- resolve_national: near-seat resolves with derived `.uf`/`.ibge_code`; global-minimum scan (Salvador/BA); mid-Atlantic (>50 km) → None; None coords → None; default `max_distance_km == 50.0` invariant.

Verification (plan `<verify>` commands, RUN_REAL_EXTERNALS unset):
- `pytest test_geo_national.py -k geocode_national` → 6 passed
- `pytest test_geo_national.py -k resolve_national` → 5 passed
- Full file + existing `test_ibge.py` + `test_nominatim.py` → 36 passed (no regressions)

## Deviations from Plan

None — plan executed exactly as written. The TDD cycle ran RED → GREEN per task. The `setex` DeprecationWarning surfaced during GREEN is pre-existing (the original `geocode` uses the same call) and out of scope per the scope boundary; no auto-fix attempted.

## Threat Surface

No new threat surface beyond the plan's `<threat_model>`. Both mitigations were applied as correctness requirements:
- T-15-05-01 (Information Disclosure): geocode_national returns only the 4 LGPD-safe keys and logs only `location_id` + `municipio` — asserted by `test_geocode_national_lgpd_no_pii`.
- T-15-05-02 (Denial of Service): reuses the ≥1 req/s rate-limit + Redis cache + negative sentinel; national cache key namespaced to avoid collisions — asserted by `test_geocode_national_caches_second_call` and `test_geocode_national_cache_key_namespaced`.

## Commits

- `750445d` test(15-05): add failing tests for national geo primitives [TA-12]
- `5cabf2c` feat(15-05): add NominatimGeocoderClient.geocode_national [TA-12]
- `3f62062` feat(15-05): add resolve_municipio_national haversine resolver [TA-12]

## Self-Check: PASSED
- FOUND: brave/clients/nominatim.py::geocode_national (grep -c == 1)
- FOUND: brave/lanes/tripadvisor/ibge.py::resolve_municipio_national
- FOUND: tests/unit/lanes/tripadvisor/test_geo_national.py (11 tests, all green)
- FOUND commits: 750445d, 5cabf2c, 3f62062
