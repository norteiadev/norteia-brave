---
phase: quick-260701-has
plan: 01
subsystem: lanes/tripadvisor
tags: [tripadvisor, atrativos, geoids, resilience, retry]
requires:
  - "TripAdvisor GraphQL AttractionsFusion transport (qid a5cb7fa004b5e4b5) — unchanged"
provides:
  - "27 live-validated STATE geoIds in data/tripadvisor/uf_geoids.json"
  - "fetch_attractions bounded transient-retry (no silent whole-UF drop)"
  - "BRAVE_TA_ATTRACTIONS_TRANSIENT_MAX_RETRIES / _RETRY_SLEEP_SECONDS config knobs"
affects:
  - "brave/lanes/tripadvisor/atrativos.py produce() — via unchanged fetch_attractions call path"
tech-stack:
  added: []
  patterns:
    - "Bounded retry loop distinguishing transient soft-failure (status.success==false) from real-empty (status absent / success true)"
key-files:
  created: []
  modified:
    - data/tripadvisor/uf_geoids.json
    - brave/lanes/tripadvisor/client.py
    - brave/config/settings.py
    - tests/unit/lanes/tripadvisor/test_client.py
    - scripts/ta_discover_state_geoids.py
    - tests/unit/lanes/tripadvisor/test_geo.py
decisions:
  - "Whitelisted RN=303510 / RS=303530 in the placeholder-range guard rather than deleting the guard — keeps regression protection for other UFs while accepting the two live-validated in-band STATE geoIds."
metrics:
  duration: "~7 min"
  completed: "2026-07-01"
  tasks: 3
  files: 6
---

# Phase quick-260701-has Plan 01: Fix TA per-UF Atrativos (correct 27-state geoIds + transient resilience) Summary

Corrected all 27 wrong TripAdvisor UF geoIds to their live-validated STATE geoIds and made `fetch_attractions` resilient to AttractionsFusion soft-failures via a bounded, config-tunable transient-retry so an intermittent HTTP-200/`status.success==false` no longer silently drops an entire state's attractions from ingest.

## What Was Built

- **Task 1 — geoId correction:** Rewrote `data/tripadvisor/uf_geoids.json` with the 27 validated STATE geoIds from the plan ground_truth (prior set was 0/27 correct — they were city geoIds). Format preserved: sorted UF keys, integer values, 2-space indent, single trailing newline.
- **Task 2 — bounded transient-retry (TDD):** Added two `BRAVE_TA_` config knobs (`attractions_transient_max_retries` default 3, `attractions_transient_retry_sleep_seconds` default 1.0) on `TripAdvisorConfig`, then wrapped the single POST + parse in `fetch_attractions` in a bounded loop. Transient is detected ONLY when `Result[0].status` is a dict with `success is False`; status absent or `success true` falls straight through to real-empty (returns `[]` in one call). Transport/qid/payload and the `max_pages>1 → NotImplementedError` contract are unchanged. Three new offline tests (transient retried → UF not dropped; real-empty → 1 call; every-call-transient → `[]` after max_retries+1 calls).
- **Task 3 — discovery-script docstring:** Updated only the module docstring of `scripts/ta_discover_state_geoids.py` to record live reality: TypeAheadJson is DataDome rate-limited (403 with a `{"url": "...captcha-delivery..."}` body), the geoId is embedded in the result `url` field (e.g. `-g303435-`), and GraphQL (qids `a26bffd43d0e25b6` + `d3d4987463b78a39`) is the durable discovery/validation path. No executable code changed.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Stale placeholder-range guard contradicted the validated geoIds**
- **Found during:** Full-suite verification after Task 1.
- **Issue:** `tests/unit/lanes/tripadvisor/test_geo.py::test_uf_geoids_no_legacy_sequential_range` asserted that NO geoId falls in the 303509-303534 band (a prior spike 260629-rmz assumed the whole band was arbitrary city geoIds). The plan's live-validated ground truth includes RN=303510 and RS=303530, which legitimately fall in that band — so the stale test failed on the corrected file.
- **Fix:** Whitelisted the two live-validated in-band STATE geoIds (`{"RN": 303510, "RS": 303530}`) while keeping the guard active for any OTHER value drifting into the placeholder range; updated the class/test docstrings to record the exception.
- **Files modified:** tests/unit/lanes/tripadvisor/test_geo.py
- **Commit:** 599aec0

## Verification

- New TA client tests + full `tests/unit/lanes/tripadvisor/test_client.py`: **53 passed** (offline, respx-mocked — no network).
- Full offline unit suite `tests/unit`: **672 passed, 1 warning** (`BRAVE_USE_FAKEREDIS=1 env -u RUN_REAL_EXTERNALS`).
- Scope confirmed: `atrativos.py`, `pipeline.py`, and the HTML `-oa{offset}-` pagination path were NOT touched.
- Task verifies: geoIds JSON assertion `OK`; docstring AST assertion `OK` + `py_compile` OK.

## Threat Model Compliance

- **T-has-01 (DoS, retry loop):** mitigated — retries bounded by `attractions_transient_max_retries` with a sleep between attempts; the exhausted-retry test proves `[]` after exactly max_retries+1 calls.
- **T-has-02 (Info disclosure, session cookies):** preserved — no new logging of session_id/cookies; change is control-flow only.
- **T-has-03 (Tampering, uf_geoids.json):** mitigated — values hard-coded from the live-validated set; Task 1 verify asserts count, sort order, integer typing, and spot-check geoIds.

## Commits

- 8bd465b — fix(quick-260701-has): replace all 27 UF geoIds with validated STATE geoIds
- f956efe — test(quick-260701-has): add failing transient-retry tests + TA config knobs (RED)
- 7ae142e — feat(quick-260701-has): bounded transient-retry in fetch_attractions (GREEN)
- 04c1822 — docs(quick-260701-has): record live TA discovery reality in ta_discover_state_geoids
- 599aec0 — fix(quick-260701-has): whitelist validated RN/RS state geoIds in range guard

## Self-Check: PASSED

All 6 modified files present; all 5 task commits found in git history.
