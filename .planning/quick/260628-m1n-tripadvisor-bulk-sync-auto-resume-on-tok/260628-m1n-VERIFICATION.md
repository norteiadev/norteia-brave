---
phase: 260628-m1n
verified: 2026-06-28T20:10:00Z
status: passed
score: 7/7 must-haves verified
overrides_applied: 0
---

# Quick Task 260628-m1n: TripAdvisor Bulk Sweep Auto-Resume Verification Report

**Task Goal:** TripAdvisor bulk sync survives session-token expiry and AUTO-RESUMES when the operator re-injects a fresh token via TWO triggers (POST /tripadvisor/session inject hook + 60s beat reconciler ta_resume_watch) calling one race-safe idempotent helper maybe_resume_bulk_sweep, which re-dispatches sweep_tripadvisor(bulk_national=True) from the saved offset using persisted resume params (depth/geo_id/max_pages). Exactly-once under concurrency (atomic claim), no infinite re-dispatch once running, and self-heal (state resets to stopped_needs_bootstrap if dispatch fails). No change to the engine per-UF tripadvisor dispatch.

**Verified:** 2026-06-28T20:10:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | claim_resume is atomic (state-check BEFORE SETNX) | VERIFIED | `sweep_progress.py:247-252` — `if not is_paused_needs_bootstrap(redis): return False` executes before `redis.set(_RESUME_CLAIM_KEY, "1", nx=True, ex=30)`. A RUNNING-state caller returns False before the SETNX is ever attempted. |
| 2 | maybe_resume_bulk_sweep: gated on paused + session; clears needs_bootstrap; dispatches with stored params; self-heals on dispatch failure | VERIFIED | `resume.py:50-102` — four sequential gates (is_paused, session exists, claim_resume wins, then best-effort delete of TA_NEEDS_BOOTSTRAP_KEY). Dispatch failure self-heals via `sweep_progress.stop_needs_bootstrap(redis)` + `redis.delete(sweep_progress._RESUME_CLAIM_KEY)` + re-raise. |
| 3 | Inject hook calls maybe_resume_bulk_sweep at module level (patchable) best-effort | VERIFIED | `tripadvisor_session.py:32` — `from brave.lanes.tripadvisor.resume import maybe_resume_bulk_sweep` (module-level). Hook at lines 283-291 wraps the call in `try/except Exception: logger.warning(...)`, ensuring inject response is never blocked. |
| 4 | Beat task ta_resume_watch wired into the beat schedule at 60s | VERIFIED | `beat_schedule.py:69-75` — `BRAVE_BEAT_SCHEDULE["ta-resume-watch"] = {"task": "brave.ta_resume_watch", "schedule": 60.0, ...}`. Task defined at `pipeline.py:2001-2030` with `name="brave.ta_resume_watch"`, `time_limit=30`, `ignore_result=True`. |
| 5 | No re-dispatch once running — is_paused_needs_bootstrap returns False for any state other than stopped_needs_bootstrap | VERIFIED | `sweep_progress.py:219-227` — single `hget` then `_decode(raw) == STOPPED_NEEDS_BOOTSTRAP`. start() flips state to RUNNING; RUNNING/DONE/RESUMING/IDLE all return False. |
| 6 | Resume uses stored depth/geo_id/max_pages from original start() call | VERIFIED | Bulk branch at `pipeline.py:1044-1051` passes `depth=depth, geo_id=geo_id, target_max_pages=max_pages or 334` to `sweep_progress.start()`. `get_resume_params()` returns these with fallbacks (geo_id=294280, max_pages=334). `maybe_resume_bulk_sweep` calls `sweep_tripadvisor.delay("BR", params["depth"], bulk_national=True, max_pages=params["max_pages"], geo_id=params["geo_id"])`. |
| 7 | Engine per-UF tripadvisor dispatch untouched | VERIFIED | `pipeline.py:1929` — `sweep_tripadvisor.delay(uf, depth=effective_depth)` — no bulk_national, geo_id, or max_pages args. The bulk branch (lines 1026-1073) and per-UF engine path are disjoint branches. |

**Score:** 7/7 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/lanes/tripadvisor/sweep_progress.py` | RESUMING constant + claim_resume + get_resume_params + is_paused_needs_bootstrap + start() new kwargs | VERIFIED | All exports present; claim_resume protocol correct (state-check then SETNX then RESUMING transition); geo_id default is None (not 294280); get_resume_params 294280 fallback confirmed |
| `brave/lanes/tripadvisor/resume.py` | maybe_resume_bulk_sweep — single idempotent helper; self-heals on dispatch failure | VERIFIED | Module-level imports of sweep_progress + BRAVE_TA_SESSION_KEY; lazy import of sweep_tripadvisor inside function body; self-heal block deletes claim key and resets state before re-raising |
| `brave/tasks/pipeline.py` | sweep_progress.start() receives depth/geo_id/target_max_pages in bulk branch; new brave.ta_resume_watch task | VERIFIED | Lines 1044-1051 and 2001-2031 respectively |
| `brave/api/routers/tripadvisor_session.py` | inject hook calls maybe_resume_bulk_sweep post-canary; TASweepProgressResponse includes "resuming" | VERIFIED | Module-level import at line 32; hook at lines 283-291 after _run_canary (line 280); TASweepProgressResponse.state Literal includes "resuming" at line 125 |
| `brave/tasks/beat_schedule.py` | ta-resume-watch 60s schedule entry | VERIFIED | Lines 69-75: `"task": "brave.ta_resume_watch", "schedule": 60.0` |
| `tests/unit/lanes/tripadvisor/test_sweep_progress_resume.py` | 11 TDD tests for sweep_progress extensions | VERIFIED | 11 tests all passing (confirmed by test run) |
| `tests/unit/lanes/tripadvisor/test_resume.py` | 8 TDD tests for maybe_resume_bulk_sweep including self-heal | VERIFIED | 8 tests all passing; self-heal test at line 138 asserts state reset + claim key deleted |
| `tests/unit/api/test_ta_auto_resume.py` | 3 TDD tests: inject hook fires maybe_resume_bulk_sweep; ta_resume_watch unit | VERIFIED | 3 tests all passing; patch target correctly uses module-level binding |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `resume.py:maybe_resume_bulk_sweep` | `sweep_progress.py` | `from brave.lanes.tripadvisor import sweep_progress` | WIRED | Module-level import; calls is_paused_needs_bootstrap, claim_resume, get_resume_params, stop_needs_bootstrap |
| `resume.py:maybe_resume_bulk_sweep` | `pipeline.py:sweep_tripadvisor` | lazy `from brave.tasks.pipeline import sweep_tripadvisor` inside function | WIRED | Lazy import at line 75 avoids circular; `.delay("BR", ...)` call at line 79 |
| `tripadvisor_session.py:inject_session` | `resume.py:maybe_resume_bulk_sweep` | module-level import + try/except call after _run_canary | WIRED | Import at line 32; call at line 287 inside best-effort try/except |
| `pipeline.py:ta_resume_watch` | `resume.py:maybe_resume_bulk_sweep` | lazy `from brave.lanes.tripadvisor.resume import maybe_resume_bulk_sweep` | WIRED | Lazy import inside task body at line 2023; call at line 2027 |
| `beat_schedule.py` | `brave.ta_resume_watch` | `BRAVE_BEAT_SCHEDULE["ta-resume-watch"]["task"]` | WIRED | `"task": "brave.ta_resume_watch"` at schedule=60.0 |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| ta-resume-watch present in beat schedule | `python -c "from brave.tasks.beat_schedule import BRAVE_BEAT_SCHEDULE; print('ta-resume-watch' in BRAVE_BEAT_SCHEDULE)"` | True | PASS |
| "resuming" in TASweepProgressResponse Literal | `python -c "from brave.api.routers.tripadvisor_session import TASweepProgressResponse; import typing; print(typing.get_args(TASweepProgressResponse.model_fields['state'].annotation))"` | `('running', 'done', 'stopped_needs_bootstrap', 'idle', 'resuming')` | PASS |
| sweep_progress exports all new symbols | `python -c "from brave.lanes.tripadvisor.sweep_progress import claim_resume, is_paused_needs_bootstrap, get_resume_params, RESUMING; print('OK')"` | OK | PASS |
| No circular import in resume.py | `python -c "from brave.lanes.tripadvisor.resume import maybe_resume_bulk_sweep; print('OK')"` | OK | PASS |
| Full offline unit suite | `.venv/bin/python -m pytest tests/unit -p no:warnings` (RUN_REAL_EXTERNALS unset) | 574 passed, 5 skipped | PASS |
| Targeted new tests only | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_sweep_progress_resume.py tests/unit/lanes/tripadvisor/test_resume.py tests/unit/api/test_ta_auto_resume.py -p no:warnings -v` | 22 passed | PASS |

---

### Anti-Patterns Found

None. No TBD/FIXME/XXX markers, no stub returns, no hardcoded empty data. `get_resume_params` fallbacks (geo_id=294280, max_pages=334) are documented defaults for the national sweep, not stubs.

---

### Human Verification Required

None. All behaviors verified programmatically via fakeredis tests and import checks.

---

## Gaps Summary

No gaps. All 7 must-have truths verified against the actual codebase. The implementation matches the plan precisely, with one documented deviation (module-level import in tripadvisor_session.py instead of lazy in-function import) that was applied per plan_check_correction and confirmed non-circular.

---

_Verified: 2026-06-28T20:10:00Z_
_Verifier: Claude (gsd-verifier)_
