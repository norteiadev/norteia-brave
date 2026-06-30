---
phase: quick-260630-ftx
verified: 2026-06-30T12:00:00Z
status: passed
score: 8/8 must-haves verified
overrides_applied: 0
---

# quick-260630-ftx: TA Atrativo Geo-Linkage Verification Report

**Phase Goal:** Implement TripAdvisor atrativo geo-linkage via the d3d4987463b78a39 cityName query (SPIKE-2)
**Verified:** 2026-06-30
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `state_name_to_uf('State of Parana')=='PR'`; `state_name_to_uf('Federal District')=='DF'`; `state_name_to_uf('State of Sao Paulo')=='SP'`; unknown→None | VERIFIED | Runtime confirmed: all 4 spot-checks OK. `_TA_STATE_CANONICAL` has 27 unique UFs, 28 keys. |
| 2 | `fetch_attraction_geo` posts qid `d3d4987463b78a39` with `{locationId, eventType:"PAGEVIEW", isGeoPage:true}`, parses `data[0].data.gtmData.locationData`, returns normalized dict; reuses session/cookie/proxy wiring; 403/429→SessionExpiredError; None on shape errors | VERIFIED | `client.py` lines 617–688. qid appears 3 times. Non-Brazil guard (countryId != 294280) at line 671. Parse errors → None at line 687. Session wiring identical to `fetch_attraction_detail`. |
| 3 | `atrativos._ingest_one` tertiary fallback calls `fetch_attraction_geo` → `state_name_to_uf` → `resolve_municipio(city_name, derived_uf, ...)`; broken `parents[0].localizedName` block is GONE; ta_config gate + throttle retained | VERIFIED | `atrativos.py` lines 208–224. `fetch_attraction_detail` has 0 matches in `atrativos.py`. `localizedName` appears only in a descriptive comment (line 204), never as a live code path. |
| 4 | `NullTripAdvisorClient.fetch_attraction_geo` returns None; `FakeTripAdvisorClient.fetch_attraction_geo` returns from `fixture_geo` dict and records `geo_calls` | VERIFIED | `null_tripadvisor.py` line 96–105: returns None. `fake_tripadvisor.py` lines 41, 59, 67, 75, 153–163: `fixture_geo` param, `geo_calls` list, method body returns `self._fixture_geo.get(location_id)`. |
| 5 | `fetch_attraction_geo` added to `TripAdvisorClientProtocol` in `base.py` | VERIFIED | `base.py` lines 345–358: abstract method with correct docstring and return type `dict | None`. |
| 6 | `TestDetailParentsLinkage` class REMOVED from `test_atrativos.py` (grep count == 0); `TestAtrativosGeoFallback` present | VERIFIED | grep -c returns 0 for `TestDetailParentsLinkage`. `TestAtrativosGeoFallback` found at 2 matches in `test_atrativos.py` with 3 test methods (geo resolves IBGE, ta_config=None gates call, geo→None no-crash). |
| 7 | `test_uf_names.py` covers all 27 UFs parametrized; `Federal District`→`DF`; `State of ` strip; ASCII-fold (`State of Sao Paulo`→`SP`); unknown→None | VERIFIED | File exists (143 lines). `_ALL_UF_CASES` list has 27 entries. DF via `Federal District` (live-confirmed bare-name). ASCII-fold tests for `São Paulo`→`SP`, `Pará`→`PA`. Dict invariant tests (28 keys, 27 unique UFs). |
| 8 | All prior TA offline tests continue to pass | VERIFIED | `env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ --tb=no` → **214 passed, 0 failed** in 9.91s |

**Score:** 8/8 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/lanes/tripadvisor/uf_names.py` | `_TA_STATE_CANONICAL` (28 keys, 27 UFs) + `state_name_to_uf()` | VERIFIED | 84 lines; conditional "State of " strip; NFKD ASCII-fold; "federal district"→"DF" and "distrito federal"→"DF" both present |
| `tests/unit/lanes/tripadvisor/test_uf_names.py` | Parametrized 27-UF coverage | VERIFIED | 143 lines; `_ALL_UF_CASES` parametrized list; DF bare/PT forms; ASCII-fold; unknown→None; dict invariants |
| `brave/clients/base.py` | `fetch_attraction_geo` in `TripAdvisorClientProtocol` | VERIFIED | Lines 345–358; abstract method with correct return type and LGPD docstring |
| `brave/lanes/tripadvisor/client.py` | Real `fetch_attraction_geo` with qid `d3d4987463b78a39` | VERIFIED | Lines 617–688; correct qid, payload vars, parse path, non-Brazil guard, error handling |
| `brave/lanes/tripadvisor/atrativos.py` | Rewired IBGE fallback via `fetch_attraction_geo` + `state_name_to_uf` | VERIFIED | Lines 56 (import), 208–224 (fallback block); no `fetch_attraction_detail` call; `localizedName` comment-only |
| `brave/clients/null_tripadvisor.py` | `fetch_attraction_geo` returning None | VERIFIED | Lines 96–105 |
| `tests/fakes/fake_tripadvisor.py` | `fixture_geo`, `geo_calls`, `fetch_attraction_geo` method | VERIFIED | All three present; `geo_calls.append` at line 162; returns `self._fixture_geo.get(location_id)` |
| `tests/unit/lanes/tripadvisor/test_atrativos.py` | `TestAtrativosGeoFallback` (3 tests); `TestDetailParentsLinkage` ABSENT | VERIFIED | 3 geo-fallback tests confirmed; `TestDetailParentsLinkage` grep returns 0 |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `atrativos.py` | `uf_names.py` | `from brave.lanes.tripadvisor.uf_names import state_name_to_uf` | WIRED | Line 56 (import); used at line 218 (`state_name_to_uf(geo["state_name"])`) |
| `atrativos.py` | `TripAdvisorClientProtocol.fetch_attraction_geo` | `await self._client.fetch_attraction_geo(loc_id_int)` | WIRED | Line 216 in `_ingest_one` tertiary fallback |
| `client.py` | `_TA_GRAPHQL_URL` | POST payload with `preRegisteredQueryId "d3d4987463b78a39"` | WIRED | Line 651; payload posted to `_TA_GRAPHQL_URL` at line 655 |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `state_name_to_uf('State of Parana')` | Python runtime | `'PR'` | PASS |
| `state_name_to_uf('Federal District')` | Python runtime | `'DF'` | PASS |
| `state_name_to_uf('State of Sao Paulo')` | Python runtime | `'SP'` | PASS |
| `state_name_to_uf('unknown state xyz')` | Python runtime | `None` | PASS |
| 27 unique UFs / 28 keys in `_TA_STATE_CANONICAL` | Python runtime | `27 UFs, 28 keys` | PASS |
| Full offline TA suite | `pytest tests/unit/lanes/tripadvisor/ --tb=no` | `214 passed, 0 failed in 9.91s` | PASS |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `atrativos.py` | 204 | `localizedName` in comment | Info | Descriptive comment referencing the removed code path — not a live code path. Acceptable. |

No blockers, no stubs, no dead references in live code.

---

### Human Verification Required

None. This task is fully offline-verifiable: no live TA session, no UI, no external service.

---

## Gaps Summary

No gaps. All 8 must-haves verified against the merged codebase.

---

_Verified: 2026-06-30_
_Verifier: Claude (gsd-verifier)_
