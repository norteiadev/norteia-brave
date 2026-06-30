---
phase: quick-260630-ftx
plan: "01"
subsystem: tripadvisor-lane
tags: [tripadvisor, geo-linkage, ibge, atrativos, graphql]
dependency_graph:
  requires: [quick-260629-rmz]
  provides: [ta-atrativo-geo-linkage]
  affects: [brave/lanes/tripadvisor/atrativos.py, brave/clients/base.py]
tech_stack:
  added: [brave/lanes/tripadvisor/uf_names.py]
  patterns: [graphql-single-hop-geo, conditional-state-of-strip, nfkd-ascii-fold]
key_files:
  created:
    - brave/lanes/tripadvisor/uf_names.py
    - tests/unit/lanes/tripadvisor/test_uf_names.py
  modified:
    - brave/clients/base.py
    - brave/clients/null_tripadvisor.py
    - tests/fakes/fake_tripadvisor.py
    - brave/lanes/tripadvisor/client.py
    - brave/lanes/tripadvisor/atrativos.py
    - tests/unit/lanes/tripadvisor/test_atrativos.py
decisions:
  - Implemented fetch_attraction_geo as a new method (not a replacement for fetch_attraction_detail) — detail method is kept for other potential callers
  - state_name_to_uf uses conditional 'State of ' prefix strip (not always-strip) to handle live-confirmed 'Federal District' bare-name for DF
  - _TA_STATE_CANONICAL uses 28 keys (27 UFs + 1 extra for DF) for both PT/EN bare forms
  - Non-Brazil guard (countryId != 294280) returns None rather than raising
metrics:
  duration_minutes: 25
  completed_date: "2026-06-30"
  tasks_total: 3
  tasks_completed: 3
  files_created: 2
  files_modified: 6
  tests_added: 56
  tests_total_ta_suite: 214
---

# Phase quick-260630-ftx Plan 01: TA Atrativo Geo-Linkage Summary

**One-liner:** TA atrativo IBGE fallback rewired from broken parents[0].localizedName to single GraphQL query d3d4987463b78a39 (cityName/stateName direct), with uf_names.py mapping all 27 Brazilian UFs including DF's live-confirmed bare English form.

## What Was Built

**Task 1 — UF name map + protocol stack (185c8cc)**
- Created `brave/lanes/tripadvisor/uf_names.py`: `_TA_STATE_CANONICAL` dict (28 keys — 27 UFs plus one extra for DF's dual form) + `state_name_to_uf()` that conditionally strips "State of " prefix, applies NFKD ASCII-fold, and does dict lookup. Live-confirmed DF arrives as "Federal District" (no prefix) — the conditional strip ensures it is NOT mangled.
- Created `tests/unit/lanes/tripadvisor/test_uf_names.py`: 42 parametrized tests covering all 27 UFs, both DF forms (English bare + Portuguese), ASCII-fold (accented input), unknown→None, whitespace tolerance, and canonical dict invariants (28 keys, 27 unique UF values).
- Added `fetch_attraction_geo` abstract method to `TripAdvisorClientProtocol` in `brave/clients/base.py`.
- Added `fetch_attraction_geo` returning None to `NullTripAdvisorClient`.
- Added `fixture_geo: dict[int, dict|None]`, `geo_calls: list[int]`, and `fetch_attraction_geo` to `FakeTripAdvisorClient`.

**Task 2 — Real client + tests (21d334f)**
- Added `TripAdvisorClient.fetch_attraction_geo` to `brave/lanes/tripadvisor/client.py`: POSTs `{locationId, eventType:"PAGEVIEW", isGeoPage:True}` with `preRegisteredQueryId "d3d4987463b78a39"`; parses `data[0].data.gtmData.locationData`; non-Brazil guard (countryId != 294280 → None); city_geo_id extracted as last non-empty segment of `locationHierarchy`; 403/429 → `SessionExpiredError`; all parse errors → None; reuses same session/cookie/UA/proxy wiring as `fetch_attraction_detail`.
- Added `TestFetchAttractionGeo` (6 tests: happy path Foz fixture, correct qid assertion, malformed→None, non-Brazil guard, 403/429 → SessionExpiredError) and `TestFakeTripAdvisorClientGeo` (3 tests: fixture returns dict, returns None on miss, geo_calls recording) to `tests/unit/lanes/tripadvisor/test_client.py`.

**Task 3 — Rewire atrativos + cleanup (bdb92b7)**
- Rewired `TripAdvisorAtrativosIngest._ingest_one` fallback: replaced `fetch_attraction_detail` + `parents[0].localizedName` (broken — field absent in live TA data per SPIKE-2) with `fetch_attraction_geo` + `state_name_to_uf`; no reference to `localizedName` in any live code path (one descriptive comment only).
- Updated `fetch_attraction_detail` docstring in `client.py` to note it is no longer called by `_ingest_one`.
- Removed `TestDetailParentsLinkage` (both methods) from `test_atrativos.py` — the `parents[0].localizedName` code path no longer exists.
- Added `TestAtrativosGeoFallback` (3 offline tests): geo fallback resolves IBGE via fetch_attraction_geo, ta_config=None gates the call, geo→None no-crash path.

## Verification Results

```
.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ — 214 passed, 0 failed
state_name_to_uf('Federal District') == 'DF'  — OK
grep 'localizedName' atrativos.py  — 1 match (comment only, not code path)
grep -c 'TestDetailParentsLinkage' test_atrativos.py  — 0
grep -c 'd3d4987463b78a39' client.py  — 3
_TA_STATE_CANONICAL has 27 UFs (28 keys)  — OK
```

## Commits

| Task | Commit | Files |
|------|--------|-------|
| 1: UF map + protocol stack | 185c8cc | uf_names.py, test_uf_names.py, base.py, null_tripadvisor.py, fake_tripadvisor.py |
| 2: Real client + tests | 21d334f | client.py, test_client.py |
| 3: atrativos rewire + cleanup | bdb92b7 | atrativos.py, client.py, test_atrativos.py |

## Deviations from Plan

None — plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes introduced beyond what the plan's `<threat_model>` covers. `fetch_attraction_geo` follows the same session/cookie trust boundary as `fetch_attraction_detail` (T-ftx-01 mitigated via except block; T-ftx-02 accepted; T-ftx-03 mitigated via derived_uf None-check).

## Self-Check: PASSED

All 8 files confirmed present. All 3 commits (185c8cc, 21d334f, bdb92b7) confirmed in git log. Full TA offline suite: 214 passed, 0 failed.
