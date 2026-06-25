---
phase: "14"
plan: "02"
subsystem: "atrativos-geo-enrichment"
tags: [nominatim, geocoder, geo-enrichment, ibge, atrativos, pipeline, ta-15, tdd]
dependency_graph:
  requires:
    - "14-01 (GeocoderClientProtocol, NominatimGeocoderClient, NullGeocoderClient, FakeGeocoderClient)"
  provides:
    - "TripAdvisorAtrativosIngest with optional geocoder arg (async _ingest_one + TA-15 geo-enrichment)"
    - "sweep_tripadvisor wired with geocoder selection mirroring ta_client pattern"
    - "TA-15 regression tests (test_coordless_resolves_via_geo, test_quarantine_after_both_fail, test_no_geocoder_unchanged)"
    - "ibge default-radius invariant (test_resolve_municipio_default_max_distance_km_is_15)"
  affects:
    - "brave.lanes.tripadvisor.atrativos (geo-enrichment before quarantine)"
    - "brave.tasks.pipeline.sweep_tripadvisor (geocoder selection + injection)"
tech_stack:
  added: []
  patterns:
    - "Optional geocoder injection via TYPE_CHECKING import (D-18)"
    - "async _ingest_one promotion pattern"
    - "Geo-enrichment block: geocoder.geocode → resolve_municipio(max_distance_km=50.0)"
    - "Geocoder selection in pipeline.py mirroring ta_client block"
    - "FakeGeocoderClient injection for offline regression tests"
    - "inspect.signature invariant assertion for default parameter guard"
key_files:
  created: []
  modified:
    - brave/lanes/tripadvisor/atrativos.py
    - brave/tasks/pipeline.py
    - tests/unit/lanes/tripadvisor/test_atrativos.py
    - tests/unit/lanes/tripadvisor/test_ibge.py
    - tests/unit/tasks/test_sweep_tripadvisor.py
decisions:
  - "geocoder=None default in __init__ preserves byte-identical behavior for all existing callers (no regression)"
  - "max_distance_km=50.0 passed only at geo-enrichment call site; ibge.py default 15.0 unchanged"
  - "quarantine_poison fires only after BOTH name-match AND geo-enrichment fail (T-15-04 mitigated)"
  - "NominatimGeocoderClient patched in test_sweep_tripadvisor helper (guard reads fresh AppConfig, not pipeline-patched one)"
metrics:
  duration: "~9 minutes"
  completed: "2026-06-25 (Tasks 1-3; Level-3 checkpoint pending)"
  tasks_completed: 3
  tasks_total: 4
  files_created: 0
  files_modified: 5
  tests_added: 4
  tests_passing: 436
---

# Phase 14 Plan 02: Atrativos Geo-Enrichment Wire Summary

**One-liner:** Nominatim geocoder injected into TripAdvisorAtrativosIngest._ingest_one as optional geo-enrichment step before ibge_unmatched quarantine, wired into sweep_tripadvisor production path; 4 new offline regression tests + ibge 15km default-radius invariant.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | atrativos.py — geocoder arg + async _ingest_one + geo-enrichment | 6ff4d57 | atrativos.py |
| 2 | pipeline.py — geocoder selection + injection | e26ade2 | pipeline.py, test_sweep_tripadvisor.py |
| 3 | test_atrativos.py + test_ibge.py — regression + invariant | 6395e9f | test_atrativos.py, test_ibge.py |
| 4 | Level-3 — Real MG sweep | PENDING | — |

## What Was Built

### Task 1: atrativos.py — Geocoder Arg + Async _ingest_one + Geo-Enrichment

**`brave/lanes/tripadvisor/atrativos.py`** — Updated:
- `GeocoderClientProtocol` imported under `TYPE_CHECKING` (D-18 — no runtime circular import)
- `__init__` gains last param `geocoder: "GeocoderClientProtocol | None" = None`; stored as `self._geocoder`
- `produce()` loop changed from `self._ingest_one(...)` to `await self._ingest_one(...)`
- `_ingest_one` promoted from `def` to `async def`
- Geo-enrichment block inserted after existing `ibge_match` assignment:
  - Only fires when `ibge_match is None and self._geocoder is not None`
  - Calls `await self._geocoder.geocode(location_id, name, uf)` → `resolve_municipio(..., max_distance_km=50.0)`
  - `quarantine_poison` fires only when `ibge_match` is STILL None after both attempts
- `ibge.py resolve_municipio` default `max_distance_km=15.0` unchanged

### Task 2: pipeline.py — Geocoder Selection Block

**`brave/tasks/pipeline.py`** — Updated:
- Geocoder selection block inserted immediately after ta_client selection block
- When `run_real_externals=True`: `NominatimGeocoderClient(config=app_config.nominatim, redis=_redis_lib.from_url(_ta_redis_url))` — reuses `_redis_lib` and `_ta_redis_url` already in scope
- When `run_real_externals=False`: `NullGeocoderClient()` — same pattern as NullTripAdvisorClient branch
- `geocoder=geocoder` passed to `TripAdvisorAtrativosIngest` construction
- All imports inside branches (D-18 local import style)

**`tests/unit/tasks/test_sweep_tripadvisor.py`** — Bug fix:
- `NominatimGeocoderClient.__init__` guard reads `AppConfig()` internally (not the pipeline-patched one)
- Fixed by patching `brave.clients.nominatim.NominatimGeocoderClient` in the test helper to return `NullGeocoderClient()`
- All 5 sweep_tripadvisor tests pass

### Task 3: Regression Tests + Invariant

**`tests/unit/lanes/tripadvisor/test_atrativos.py`** — Extended:
- `FakeGeocoderClient` import added
- `_make_coordless_card()` helper added (locationId=312332, name="Cachoeira do Tabuleiro", no lat/lng)
- `TestAtrativosGeoEnrichment` class with 3 `@pytest.mark.asyncio` tests:
  - `test_coordless_resolves_via_geo`: FakeGeocoderClient returns coords → card resolves to 3117900; store_raw called; geocode_calls has 1 entry for "312332"
  - `test_quarantine_after_both_fail`: both strategies fail → quarantine fires with `brave.ta.atrativos.ibge_unmatched`; store_raw NOT called
  - `test_no_geocoder_unchanged`: geocoder omitted → Uberlândia card resolves via name-match alone; store_raw called

**`tests/unit/lanes/tripadvisor/test_ibge.py`** — Extended:
- `test_resolve_municipio_default_max_distance_km_is_15` top-level function: `inspect.signature` invariant guard asserts default == 15.0

## Verification Results

```
1. Task 3 targeted tests: 32 passed (test_atrativos + test_nominatim + test_ibge)
2. test_coordless_resolves_via_geo: PASSED
3. test_quarantine_after_both_fail: PASSED
4. test_no_geocoder_unchanged: PASSED
5. ibge default-radius invariant: PASSED
6. Pipeline wiring: grep -q "geocoder=geocoder" OK, grep -q "NominatimGeocoderClient(" OK, grep -q "NullGeocoderClient()" OK
7. Full offline unit suite: 436 passed, 5 skipped
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] NominatimGeocoderClient guard breaks test_sweep_tripadvisor tests**
- **Found during:** Task 2
- **Issue:** `NominatimGeocoderClient.__init__` guard calls `AppConfig().run_real_externals` internally to check the flag. The existing `test_sweep_tripadvisor.py` helper patches `brave.tasks.pipeline.AppConfig`, not `brave.config.settings.AppConfig` used inside `nominatim.py`. When `run_real_externals=True` is set via `mock_app_config` but `RUN_REAL_EXTERNALS` env var is not set, the guard raises `RuntimeError` — which the test's generic exception handler incorrectly routes to the retry path, causing 4 test failures.
- **Fix:** Added `monkeypatch.setattr("brave.clients.nominatim.NominatimGeocoderClient", lambda config, redis: NullGeocoderClient())` to the test helper — replaces the real client constructor with a no-op that returns `NullGeocoderClient()`, which is structurally identical (GeocoderClientProtocol) and correct for offline tests.
- **Files modified:** `tests/unit/tasks/test_sweep_tripadvisor.py`
- **Commit:** e26ade2

## Threat Surface Mitigated

| Threat | File | Status |
|--------|------|--------|
| T-15-01 (LGPD) | atrativos._ingest_one | Mitigated — only lat/lon/osm_id from geo dict flow into payload; municipio_name used for matching only |
| T-15-02 (Tampering) | atrativos._ingest_one | Mitigated — `geo.get("municipio_name") or name` fallback; max_distance_km=50.0 is a fixed literal |
| T-15-03 (DoS) | atrativos._ingest_one | Mitigated — NullGeocoderClient in offline suite; cache in NominatimGeocoderClient prevents re-calls |
| T-15-04 (Elevation) | atrativos._ingest_one | Mitigated — quarantine fires when ibge_match is STILL None after BOTH attempts; test_quarantine_after_both_fail verifies |

## Known Stubs

None — all code is fully wired. Level-3 checkpoint pending human verification of real MG sweep.

## Pending: Task 4 — Level-3 Checkpoint

Level-3 gate requires a real MG sweep with `RUN_REAL_EXTERNALS=1`. See checkpoint details below.

## Self-Check: PASSED

- [x] `brave/lanes/tripadvisor/atrativos.py` — modified (geocoder arg + async + geo-enrichment)
- [x] `brave/tasks/pipeline.py` — modified (geocoder selection + injection)
- [x] `tests/unit/lanes/tripadvisor/test_atrativos.py` — modified (3 new TA-15 tests)
- [x] `tests/unit/lanes/tripadvisor/test_ibge.py` — modified (invariant test)
- [x] `tests/unit/tasks/test_sweep_tripadvisor.py` — modified (NominatimGeocoderClient patch)
- [x] Task 1 commit 6ff4d57 — exists
- [x] Task 2 commit e26ade2 — exists
- [x] Task 3 commit 6395e9f — exists
- [x] test_coordless_resolves_via_geo passes
- [x] test_quarantine_after_both_fail passes
- [x] test_no_geocoder_unchanged passes
- [x] ibge max_distance_km default 15.0 invariant passes
- [x] Full unit suite 436 passed, 5 skipped
