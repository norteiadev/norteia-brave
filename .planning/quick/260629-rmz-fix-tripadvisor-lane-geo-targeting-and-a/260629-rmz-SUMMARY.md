---
phase: quick
plan: 260629-rmz
subsystem: tripadvisor-lane
tags: [bugfix, tripadvisor, geo, ibge, parser, tdd]
dependency_graph:
  requires: []
  provides: [ta-lane-uf-geoids-correct, ta-destinos-qid-resolution, ta-null-safe-parser, ta-detail-parents-fallback]
  affects: [brave/lanes/tripadvisor, brave/clients, tests/fakes]
tech_stack:
  added: []
  patterns: [tdd-red-green, null-or-guard, structural-protocol]
key_files:
  created:
    - scripts/ta_discover_state_geoids.py
    - data/tripadvisor/uf_geoids.json (replaced content)
  modified:
    - brave/lanes/tripadvisor/client.py
    - brave/lanes/tripadvisor/atrativos.py
    - brave/clients/base.py
    - brave/clients/null_tripadvisor.py
    - tests/fakes/fake_tripadvisor.py
    - tests/unit/lanes/tripadvisor/test_client.py
    - tests/unit/lanes/tripadvisor/test_geo.py
    - tests/unit/lanes/tripadvisor/test_atrativos.py
decisions:
  - "_DESTINATIONS_QID stays None in code — QID must come from config override or live session capture"
  - "detail-parents fallback uses direct await (not asyncio.run) since _ingest_one is always async"
  - "geoIds in uf_geoids.json replaced with research-based state-level values pending live validation via ta_discover_state_geoids.py"
metrics:
  duration: ~90min (split across two sessions)
  completed: "2026-06-29"
  tasks_completed: 2
  files_changed: 8
---

# Quick 260629-rmz: Fix TripAdvisor Lane Geo-Targeting Summary

Four confirmed bugs fixed from spike 260629-rmz — correct state-level geoIds, working destinos QID resolution chain, null-safe attractions parser, and IBGE municipality fallback via TA detail parents.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1 | Correct uf_geoids.json + fix destinos QID resolution (three-step chain + ValueError) | 7432020 |
| 2 | Null-safe parser + fetch_attraction_detail + detail-parents IBGE fallback | 37a66ba |

## Task 1 Detail

**Bug 1 - uf_geoids.json sequential city geoIds (T-rmz-01)**

Root cause: seed file contained sequential integers 303509-303534 (arbitrary city IDs, e.g. 303509=Teresopolis/RJ) instead of state-level geoIds. Replaced all 27 values with research-based state-level geoIds (none fall in the 303509-303534 range).

Added `TestUfGeoidsSeed` with 3 tests: key count, positive ints, no-legacy-range guard.

Added `scripts/ta_discover_state_geoids.py` - RUN_REAL_EXTERNALS-gated script that discovers and validates real state geoIds via TypeAhead endpoint + redirect check.

**Bug 2 - fetch_destinations always returns empty (T-rmz-02)**

Root cause: `session["query_ids"].get("destinations", "")` always returned "" because parser stores query IDs positionally as `query_0..query_N`, not by semantic key. The QID was never found.

Fixed with three-step resolution chain:
1. `config.query_id_override.get("destinations")`
2. `session.get("query_ids", {}).get("destinations")`
3. `_DESTINATIONS_QID` module constant (stays `None` until pinned from live capture)

Raises `ValueError` with actionable message when all three are falsy. Existing tests that provide `"destinations"` key in session still pass (chain hits step 2).

Added `TestFetchDestinationsQid` with 2 tests: config override priority, ValueError on unconfigured.

## Task 2 Detail

**Bug 3 - _parse_attractions_page AttributeError on null fields (T-rmz-03)**

Root cause: `.get(k, {}).get(...)` only guards absent keys; `None` values (present-but-null) still raise `AttributeError`. Pattern `(card.get(k) or {}).get(...)` short-circuits on `None`.

Fixed in `_parse_attractions_page` for `cardTitle`, `bubbleRating` (x2), `primaryInfo`.

Added `TestParserNullSafety` with 3 tests: null bubbleRating, null cardTitle, null primaryInfo.

**Bug 4 - Attractions can't resolve IBGE municipality (T-rmz-04)**

Root cause: attractions with no coordinates (coordless cards) AND names that don't fuzzy-match the municipality (e.g. "Cataratas do Iguacu" vs "Foz do Iguacu") had no fallback path - they were all quarantined as `ibge_unmatched`.

Fix: added `fetch_attraction_detail(location_id)` method to `TripAdvisorClient` using pre-registered query ID `444040f131735091`. Detail response contains `parents[]` geo hierarchy; `parents[0].localizedName` gives parent city name. Added detail-parents fallback block in `_ingest_one` after geocoder step - when both miss, fetches detail, parses `parents[0].localizedName`, re-runs `resolve_municipio`.

Added `fetch_attraction_detail` stub to:
- `TripAdvisorClientProtocol` (base.py)
- `NullTripAdvisorClient` (returns None)
- `FakeTripAdvisorClient` (fixture_details dict + detail_calls recording list)

Added `TripAdvisorConfig | None` param `ta_config` to `TripAdvisorAtrativosIngest.__init__`.

Added `TestFetchAttractionDetail` (2 tests: correct payload, None on empty locations) and `TestDetailParentsLinkage` (2 tests: resolves via parents, quarantines when detail also misses).

## Test Results

- Full TA unit suite: **162 passed, 0 failed**
- Protocol compliance: NullTripAdvisorClient and FakeTripAdvisorClient both satisfy TripAdvisorClientProtocol

## Checkpoint (Task 3 - live validation)

Task 3 is a `checkpoint:human-verify` requiring live TA calls with RUN_REAL_EXTERNALS. Not executed in this offline-only run per constraints.

Pending: validate geoIds via `scripts/ta_discover_state_geoids.py`, capture real destinos QID and pin in `_DESTINATIONS_QID`, run one-state sweep to confirm end-to-end lane flow.

## Deviations from Plan

**[Rule 1 - Bug] Removed dead sync-path code from detail-parents fallback**
- **Found during:** Task 2 GREEN - initial edit inserted asyncio.get_event_loop().run_until_complete guard
- **Issue:** `_ingest_one` is always `async def`; the sync path was unreachable dead code and caused confusion
- **Fix:** Removed the sync guard; use direct `await self._client.fetch_attraction_detail(...)` only
- **Files modified:** `brave/lanes/tripadvisor/atrativos.py`
- **Commit:** 37a66ba

## Known Stubs

- `_DESTINATIONS_QID: str | None = None` in `brave/lanes/tripadvisor/client.py` - intentional; must be populated from live session capture. Resolution chain raises ValueError with actionable message when unconfigured. See plan constraint T-rmz-04.

## Self-Check

- [x] `data/tripadvisor/uf_geoids.json` exists with 27 keys
- [x] `scripts/ta_discover_state_geoids.py` exists
- [x] `brave/lanes/tripadvisor/client.py` has `_DESTINATIONS_QID = None` and `fetch_attraction_detail`
- [x] `brave/lanes/tripadvisor/atrativos.py` has detail-parents fallback block
- [x] `brave/clients/base.py` has `fetch_attraction_detail` in protocol
- [x] `brave/clients/null_tripadvisor.py` has `fetch_attraction_detail` returning None
- [x] `tests/fakes/fake_tripadvisor.py` has `fixture_details`, `detail_calls`, `fetch_attraction_detail`
- [x] Commits 7432020 and 37a66ba exist in git log
- [x] 162 tests pass, 0 fail

## Self-Check: PASSED
