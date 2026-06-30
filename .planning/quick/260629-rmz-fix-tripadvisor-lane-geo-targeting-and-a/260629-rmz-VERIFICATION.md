---
phase: quick-260629-rmz
verified: 2026-06-29T18:00:00Z
status: human_needed
score: 4/5 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run scripts/ta_discover_state_geoids.py with a live TA session and confirm each UF redirect resolves to the correct state-level canonical URL"
    expected: "Each of the 27 UFs shows VALID with the state name in the canonical redirect. No INVALID result."
    why_human: "Script requires RUN_REAL_EXTERNALS=1 and a live Redis session; cannot be verified offline"
  - test: "Capture real destinos query_id from browser DevTools and verify fetch_destinations returns > 0 results for at least one UF when BRAVE_TA_QUERY_ID_OVERRIDE is set"
    expected: "fetch_destinations('AC') returns a non-empty list of destination dicts when BRAVE_TA_QUERY_ID_OVERRIDE={'destinations':'<real_qid>'} is configured"
    why_human: "Requires live TA session and real QID discovery; _DESTINATIONS_QID is intentionally None in code"
  - test: "Call fetch_attraction_detail(312332) with a live session and confirm parents[0].localizedName == 'Foz do Iguacu'"
    expected: "Detail response returns parents[0] = {locationId: 303444, localizedName: 'Foz do Iguacu'}"
    why_human: "Requires live TA session (RUN_REAL_EXTERNALS=1); offline FakeClient confirms the wiring path only"
---

# Quick 260629-rmz: Fix TripAdvisor Lane Geo-Targeting — Verification Report

**Phase Goal:** Fix TripAdvisor lane geo-targeting and atrativo->destino linkage
**Verified:** 2026-06-29T18:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

> **Post-verification fix (commit f9a10d7):** The two WARNINGS below (must-have #3
> missing `self._ta_config is not None` guard; must-have #5 absent `import asyncio`
> from a dropped throttle block) were both resolved after this report was written.
> `atrativos._ingest_one` now gates the detail-parents fallback with
> `if ibge_match is None and self._ta_config is not None:` and throttles via
> `self._ta_config.page_throttle_seconds` (DataDome protection); `import asyncio` is
> top-level. 162 TA unit tests still pass offline. Must-haves #3 and #5 are now FULLY
> met — score is effectively 5/5. The remaining `human_needed` status is solely the
> three by-design live-session validations (geoId correctness, real destinos QID,
> detail-parents response shape), which require `RUN_REAL_EXTERNALS=1` + a live TA
> session — Task 3 of the plan, deferred by design.

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `uf_geoids.json` has 27 UF keys, all positive geoIds, none in legacy 303509-303534 range | VERIFIED | File confirmed: 27 keys, smallest 295960 (AC), no value in 303509-303534. Programmatic check passes. TestUfGeoidsSeed guards this in CI. |
| 2 | `fetch_destinations` raises ValueError when no QID; uses config override first; `_DESTINATIONS_QID = None` | VERIFIED | Lines 347-363 of client.py: three-step resolution chain with config override first, ValueError raised with actionable message. `_DESTINATIONS_QID: str \| None = None` at line 80. TestFetchDestinationsQid (2 tests) pass. |
| 3 | `fetch_attraction_detail` on full client stack; `_ingest_one` calls it as tertiary IBGE fallback; `ta_config` param on `__init__` | PARTIAL | Method exists on TripAdvisorClient (line 561), protocol base.py (line 329), NullTripAdvisorClient (line 85), FakeTripAdvisorClient (line 133). `_ingest_one` calls it (line 215). `ta_config` param added to `__init__` (line 111). BUT: guard in code is `if ibge_match is None:` — the required `and self._ta_config is not None` condition is absent. Also no `asyncio.sleep` throttle block. |
| 4 | `_parse_attractions_page` uses `(card.get(k) or {})` for null bubbleRating/cardTitle/primaryInfo | VERIFIED | Lines 182, 189, 190, 191 of client.py all use `(card.get(k) or {}).get(...)` pattern. TestParserNullSafety (3 tests) pass. |
| 5 | `import asyncio` is top-level in atrativos.py (not lazy inside method) | FAILED | `import asyncio` is absent from atrativos.py. Top-level imports confirmed at lines 35-55 — no asyncio. Throttle block (`asyncio.sleep`) was dropped from the implementation, making the import unnecessary, but the must_have explicitly requires it. |

**Score:** 4/5 truths verified (3 VERIFIED, 1 PARTIAL treated as WARNING, 1 FAILED)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `data/tripadvisor/uf_geoids.json` | 27 correct state-level TA geoIds | VERIFIED | 27 entries, all positive, no legacy sequential range |
| `brave/lanes/tripadvisor/client.py` | `_DESTINATIONS_QID`, fixed `fetch_destinations`, `fetch_attraction_detail`, null-safe parser | VERIFIED | All present: `_DESTINATIONS_QID=None` at line 80, three-step resolution at lines 351-363, `fetch_attraction_detail` at line 561, null-safe parser at lines 182-191 |
| `brave/clients/base.py` | `fetch_attraction_detail` in TripAdvisorClientProtocol | VERIFIED | Line 329 |
| `brave/clients/null_tripadvisor.py` | `fetch_attraction_detail` returning None | VERIFIED | Line 85, protocol compliance check passes |
| `tests/fakes/fake_tripadvisor.py` | `fetch_attraction_detail` with fixture_details + detail_calls | VERIFIED | Lines 55 and 133; FakeClient protocol compliance passes |
| `scripts/ta_discover_state_geoids.py` | RUN_REAL_EXTERNALS-gated discovery + validation script | VERIFIED | Exists; RUN_REAL_EXTERNALS guard at line 45 |
| `brave/lanes/tripadvisor/atrativos.py` | `_ingest_one` calls `fetch_attraction_detail` as tertiary fallback; `ta_config` param | PARTIAL | Call exists at line 215; `ta_config` param at line 111. Guard deviates from spec (see below). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `brave/lanes/tripadvisor/geo.py` | `data/tripadvisor/uf_geoids.json` | `load_uf_geoids(GEO_SEED_PATH)` | WIRED | TestUfGeoidsSeed loads via geo module path |
| `client.py:fetch_destinations` | `_DESTINATIONS_QID / config.query_id_override` | three-step `query_id` local var | WIRED | Lines 351-354 |
| `atrativos.py:_ingest_one` | `client.fetch_attraction_detail` | tertiary IBGE fallback block | WIRED (with deviation) | Call at line 215. Guard is `if ibge_match is None:` not `if ibge_match is None and self._ta_config is not None:` |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full offline TA unit suite | `env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/` | 162 passed, 32 warnings, 0 failed in 14.23s | PASS |
| Protocol compliance — NullClient | `.venv/bin/python -c "from brave.clients.null_tripadvisor import _check_protocol_compliance; _check_protocol_compliance(); print('null ok')"` | `null ok` | PASS |
| Protocol compliance — FakeClient | `.venv/bin/python -c "from tests.fakes.fake_tripadvisor import _check_protocol_compliance; _check_protocol_compliance(); print('fake ok')"` | `fake ok` | PASS |
| uf_geoids.json structural invariants | python3 programmatic check (27 keys, all positive, no legacy range) | All three assertions pass | PASS |

### Anti-Patterns Found

No `TBD`, `FIXME`, or `XXX` markers found in modified files. No stub placeholders in parser or fallback logic (NullClient returning None is intentional behavior, not a stub).

### Warnings

**WARNING 1 — Missing `ta_config` guard on detail fallback block (must_have #3 partial)**

The plan required the detail-parents fallback to be gated by:
```python
if ibge_match is None and self._ta_config is not None:
```

The actual code in `atrativos.py` line 208 is:
```python
if ibge_match is None:
```

The `and self._ta_config is not None` condition is absent. The consequence: when `ta_config=None` (existing call-sites that don't pass ta_config), `fetch_attraction_detail` is still called on whatever client was injected — including a real `TripAdvisorClient`. Those call-sites would now make unthrottled detail requests for every ibge-unresolved card, which was explicitly not intended. With a NullTripAdvisorClient the effect is harmless (returns None immediately). The tests don't exercise the `ta_config=None` case, so this gap passes undetected.

Impact: existing production call-sites using a real TripAdvisorClient without ta_config will now make additional GraphQL calls without rate limiting.

**WARNING 2 — `import asyncio` absent from atrativos.py top-level (must_have #5 FAILED)**

Must_have #5 explicitly requires `import asyncio` to be a top-level import in `atrativos.py`. It is absent. The implementation dropped the throttle block (`asyncio.sleep`), making the import technically unnecessary, but the must_have literally specifies the import. The SUMMARY documents the deviation from the asyncio.run sync-guard (Rule 1 — Bug), but does not document the removal of the throttle/sleep as a separate deviation.

Impact: no runtime impact since asyncio is unused. The concern was about code quality (never lazy-import stdlib). Without the import, the spirit is moot, but the letter of the must_have is violated.

### Human Verification Required

#### 1. Validate geoIds with live session

**Test:** Run `RUN_REAL_EXTERNALS=1 .venv/bin/python scripts/ta_discover_state_geoids.py` with a real injected Redis session
**Expected:** Each of the 27 UFs shows VALID with the correct state name in the canonical TA URL. Zero INVALID results. Script prints a JSON blob matching (or correcting) current `uf_geoids.json`.
**Why human:** Requires a live TripAdvisor session in Redis; offline tests can only guard structure (27 keys, positive, not legacy range) — not semantic correctness of the state-level geoId values.

#### 2. Verify fetch_destinations with real destinos QID

**Test:** Discover the destinations query_id from browser DevTools (POST /data/graphql/ids on a TA Brazil geo page), set `BRAVE_TA_QUERY_ID_OVERRIDE='{"destinations":"<qid>"}'`, run `fetch_destinations("AC")` with a live session
**Expected:** Returns a non-empty list of destination dicts (> 0 results)
**Why human:** `_DESTINATIONS_QID` is intentionally None; real QID must be captured from a live browser session; no offline substitute

#### 3. Verify detail-parents response shape for known attraction

**Test:** Run `fetch_attraction_detail(312332)` with a live session (`RUN_REAL_EXTERNALS=1`)
**Expected:** Returns a dict with `parents[0] = {"locationId": 303444, "localizedName": "Foz do Iguacu"}` (or equivalent city for the test attraction)
**Why human:** Offline FakeTripAdvisorClient confirms the _ingest_one wiring path, but not that the real TA GraphQL qid `444040f131735091` is still valid and returns the expected shape

### Gaps Summary

**Must_have #5 FAILED:** `import asyncio` is absent from atrativos.py. This is a literal spec violation but has no runtime impact since asyncio is not used (the throttle/sleep was dropped from the implementation). The must_have was tied to the plan's throttle block which was removed.

**Must_have #3 PARTIAL:** The `ta_config is not None` guard was not applied to the detail fallback block. Detail calls will fire for every ibge-unresolved card regardless of whether ta_config was provided. Tests pass because both test cases provide ta_config; the `ta_config=None` path is untested.

These are behavioral deviations from the spec but do not prevent the core goal from functioning when ta_config IS provided (which is the expected production path for the TA lane).

---

_Verified: 2026-06-29T18:00:00Z_
_Verifier: Claude (gsd-verifier)_
