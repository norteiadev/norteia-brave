---
phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr
plan: "01"
subsystem: api
tags: [google-places, places-api, field-mask, municipio, ibge, addressComponents, offline-tests]

# Dependency graph
requires:
  - phase: 06-real-externals-enablement-realllmclient-live-24-7-collection
    provides: RealPlacesClient base implementation (RUN_REAL_EXTERNALS guard + lazy init)
provides:
  - brave/clients/places.py with correct field-mask constants for text_search and place_details
  - municipio_nome + municipio_ibge populated in every text_search result dict
  - build_mtur_ibge_lookup helper for name→IBGE in-process resolution
  - 5 offline unit tests (T1–T5) asserting mask strings, municipio mapping, Timestamp conversion
affects:
  - plan 07-03 (targeted discovery uses corrected text_search + municipio fields)
  - plan 07-05 (load-test harness relies on text_search returning municipio_ibge)
  - discovery_agent._resolve_parent_destino (receives non-empty municipio_ibge now)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Field mask constants at module level (_TEXT_SEARCH_FIELD_MASK with 'places.' prefix for SearchTextResponse, _GET_PLACE_FIELD_MASK without prefix for bare Place)"
    - "Proto Timestamp safe conversion via ToDatetime(tzinfo=utc).isoformat() with AttributeError fallback"
    - "In-process name→IBGE lookup via normalized tuple key (lowercase+strip-accents, UF)"

key-files:
  created:
    - tests/unit/clients/test_real_places_client.py
  modified:
    - brave/clients/places.py

key-decisions:
  - "D-01: Two field-mask constants with different prefixes — search_text uses 'places.' because it returns SearchTextResponse.places[]; get_place uses no prefix because it returns a bare Place"
  - "D-01: publish_time proto Timestamp converted via .ToDatetime(tzinfo=utc).isoformat() with AttributeError fallback for proto-plus auto-conversion variants"
  - "D-02: ibge_lookup injected via __init__ optional param (dict[tuple[str,str],str]) to keep construction testable without Mtur I/O"
  - "D-01: place.regular_opening_hours used instead of place.current_opening_hours (stable schedule field 21 vs ephemeral field 46)"

requirements-completed:
  - PLACE-01
  - PLACE-02

# Metrics
duration: 4min
completed: 2026-06-18
---

# Phase 7 Plan 01: RealPlacesClient Field-Mask Fix + Municipio Extraction Summary

**Fixed two live-breaking Places API field-mask bugs (text_search 400 + get_place empty fields) and wired addressComponents→municipio_ibge extraction with in-process Mtur IBGE lookup.**

## Performance

- **Duration:** 4 min
- **Started:** 2026-06-18T16:14:18Z
- **Completed:** 2026-06-18T16:18:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Fixed `text_search` missing `X-Goog-FieldMask` header (was causing live 400 INVALID_ARGUMENT on every search call)
- Fixed `place_details` wrong field-mask prefix (`places.id,...` → `id,...`) that caused all get_place fields to return empty/default
- Added `_extract_municipio_from_components` + `build_mtur_ibge_lookup` + `_normalize_name` module-level helpers so `text_search` results now carry `municipio_nome` and `municipio_ibge`
- Changed `place.current_opening_hours` → `place.regular_opening_hours` and fixed proto Timestamp conversion
- 5 offline tests (T1–T5) in `test_real_places_client.py` all pass with zero network calls; full 398-test suite green

## Task Commits

1. **Task 1: Fix field masks and add municipio extraction to RealPlacesClient** - `0470761` (feat)
2. **Task 2: Write offline tests for D-01/D-02 Places client fixes** - `9d17b63` (test)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified

- `brave/clients/places.py` — Added `_TEXT_SEARCH_FIELD_MASK`, `_GET_PLACE_FIELD_MASK` constants; `_normalize_name`, `_extract_municipio_from_components`, `build_mtur_ibge_lookup` helpers; updated `__init__` with `ibge_lookup` param; fixed `text_search` metadata kwarg + municipio extraction in result loop; fixed `place_details` field mask, `regular_opening_hours`, and `publish_time` conversion
- `tests/unit/clients/test_real_places_client.py` — 5 offline unit tests (T1–T5) using AsyncMock + patch to mock `PlacesAsyncClient`

## Decisions Made

- Used module-level constants for both field masks (not inline strings) so tests can assert exact values from imports
- Injected `ibge_lookup` via constructor param to avoid Mtur I/O at test time — callers (harness, pipeline.py) build it once with `build_mtur_ibge_lookup(mtur_rows)`
- Added `AttributeError` fallback on `publish_time.ToDatetime()` to handle proto-plus auto-conversion variants gracefully

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## Known Stubs

None. `municipio_nome` and `municipio_ibge` are now fully wired from the Google Places addressComponents. The `ibge_lookup` defaults to `{}` (returning `""` for all IBGE lookups) until the caller passes a populated lookup from `build_mtur_ibge_lookup`. This is intentional and documented — plan 07-04 (Mtur refresh) provides the full dataset, and the harness (07-05) wires it end-to-end.

## Threat Flags

No new threat surface introduced. Changes are internal to the Places API client:
- `api_key` never logged (T-07-01 existing control preserved)
- `addressComponents` strings stored in canonical dict, validated downstream by Pydantic (T-07-02 accepted)
- Wrong mask → 400 loop prevented by D-01 fix (T-07-03 mitigated)
- No new packages installed (T-07-SC accepted)

## Next Phase Readiness

- `text_search` and `place_details` are unblocked for live Places calls
- `municipio_nome`/`municipio_ibge` fields now present in text_search results — `_resolve_parent_destino` (plan 07-03) can guard against empty IBGE
- `build_mtur_ibge_lookup` ready for wiring in the harness (plan 07-05)
- Plans 07-02 (DLQ) and 07-03 (targeted discovery) can proceed in parallel

## Self-Check: PASSED

All created files verified present on disk. All task commits verified in git history.

- FOUND: brave/clients/places.py
- FOUND: tests/unit/clients/test_real_places_client.py
- FOUND: 07-01-SUMMARY.md
- FOUND commit: 0470761 (Task 1 — field masks + municipio extraction)
- FOUND commit: 9d17b63 (Task 2 — offline unit tests)

---
*Phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr*
*Completed: 2026-06-18*
