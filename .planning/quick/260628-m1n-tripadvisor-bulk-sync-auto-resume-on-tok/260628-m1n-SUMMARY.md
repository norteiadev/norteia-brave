---
phase: 260628-m1n
plan: 01
subsystem: tripadvisor-lane
tags: [auto-resume, sweep-progress, beat-task, celery, fakeredis, tdd]
dependency_graph:
  requires: [phase-15-ta-bulk-sweep]
  provides: [TA-AUTO-RESUME]
  affects: [brave/lanes/tripadvisor/sweep_progress.py, brave/lanes/tripadvisor/resume.py, brave/tasks/pipeline.py, brave/api/routers/tripadvisor_session.py, brave/tasks/beat_schedule.py]
tech_stack:
  added: []
  patterns: [state-check-then-SETNX atomic gate, best-effort try/except hook, lazy Celery task import]
key_files:
  created:
    - brave/lanes/tripadvisor/resume.py
    - tests/unit/lanes/tripadvisor/test_sweep_progress_resume.py
    - tests/unit/lanes/tripadvisor/test_resume.py
    - tests/unit/api/test_ta_auto_resume.py
  modified:
    - brave/lanes/tripadvisor/sweep_progress.py
    - brave/tasks/pipeline.py
    - brave/api/routers/tripadvisor_session.py
    - brave/tasks/beat_schedule.py
    - tests/unit/lanes/tripadvisor/test_sweep_progress.py
decisions:
  - "Module-level import in tripadvisor_session.py (not lazy) so monkeypatch intercepts the binding"
  - "claim_resume: state-check BEFORE SETNX to prevent RUNNING-state caller winning gate on fresh Redis"
  - "geo_id default in start() is None (not 294280) — 294280 fallback lives only in get_resume_params"
  - "maybe_resume_bulk_sweep re-raises after self-heal so caller can log; inject hook swallows it"
metrics:
  duration: "938 seconds (~16 min)"
  completed: "2026-06-28T19:35:00Z"
  tasks_completed: 2
  files_changed: 9
---

# Phase 260628-m1n Plan 01: TripAdvisor Bulk Sweep Auto-Resume Summary

**One-liner:** Defense-in-depth auto-resume for TA bulk sweep via state-check-then-SETNX inject hook + 60s beat task using stored depth/geo_id/max_pages params.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 RED | sweep_progress tests (11 tests) | 83e6b0e | test_sweep_progress_resume.py, test_sweep_progress.py |
| 1 GREEN | sweep_progress extensions + resume.py | 2562d46 | sweep_progress.py, resume.py |
| 2 RED | wiring tests (8 + 3 tests) | e95ea2c | test_resume.py, test_ta_auto_resume.py |
| 2 GREEN | pipeline / inject hook / beat wiring | 8e7de27 | pipeline.py, tripadvisor_session.py, beat_schedule.py |

## What Was Built

### sweep_progress.py extensions
- `RESUMING = "resuming"` constant added to `_VALID_STATES` (get_progress returns `"resuming"` not `"idle"` fallback)
- `_RESUME_CLAIM_KEY = "brave:ta:sweep:resume:claiming"` — SETNX gate key with EX=30 TTL
- `start()` extended with keyword-only `depth`, `geo_id`, `target_max_pages` args; `geo_id` defaults to `None` (NOT 294280) so the `{k:v if v is not None}` filter correctly omits unset fields
- `is_paused_needs_bootstrap(redis) -> bool` — single hget state check
- `claim_resume(redis) -> bool` — state-check then SETNX then state transition to RESUMING; state check must precede SETNX
- `get_resume_params(redis) -> dict` — returns stored depth/geo_id/max_pages with fallbacks (geo_id=294280, max_pages=334)

### resume.py (new module)
- `maybe_resume_bulk_sweep(redis)` — idempotent helper; checks is_paused + session present + claim_resume wins
- Clears `TA_NEEDS_BOOTSTRAP_KEY` on success (best-effort)
- Self-heals on `sweep_tripadvisor.delay` failure: resets state to `stopped_needs_bootstrap`, deletes claim key, re-raises for observability
- Lazy import of `sweep_tripadvisor` to avoid circular imports at module load

### pipeline.py changes
- Bulk branch `sweep_progress.start()` call extended to pass `depth=depth`, `geo_id=geo_id`, `target_max_pages=max_pages or 334`
- New `ta_resume_watch` task (`brave.ta_resume_watch`, bind=False, 60s beat) with lazy import inside task body

### tripadvisor_session.py changes
- Module-level import: `from brave.lanes.tripadvisor.resume import maybe_resume_bulk_sweep` (required for monkeypatch intercept)
- Auto-resume hook after `await _run_canary(...)`: best-effort `try/except` wrapping `maybe_resume_bulk_sweep(redis)`; exception never blocks the inject response
- `TASweepProgressResponse.state` Literal extended with `"resuming"`

### beat_schedule.py changes
- Added `ta-resume-watch` entry: `brave.ta_resume_watch` at 60s on `brave.sweep` queue

## Test Counts

| File | Tests | Status |
|------|-------|--------|
| test_sweep_progress_resume.py | 11 | GREEN |
| test_sweep_progress.py (updated whitelist) | 12 | GREEN |
| test_resume.py | 8 | GREEN |
| test_ta_auto_resume.py | 3 | GREEN |
| **Full offline suite** | **574 passed, 5 skipped** | **GREEN** |

## Decisions Made

1. **Module-level import in tripadvisor_session.py** — per `plan_check_correction`, the inject hook import must be at module level (not lazy inside the function) so `monkeypatch.setattr(ts_module, "maybe_resume_bulk_sweep", ...)` intercepts the binding. There is no circular import (brave.lanes.* never imports brave.api.*).

2. **State-check before SETNX in claim_resume** — if SETNX ran first, a RUNNING-state caller finding a fresh Redis instance (empty claim key) would win the gate and wrongly return True. The state check is the mandatory first gate.

3. **geo_id default is None in start()** — the 294280 fallback belongs exclusively in `get_resume_params`. This lets `start()` be called without new kwargs (e.g., per-UF path) without writing geo_id to the hash — which `test_start_without_new_kwargs_omits_fields` asserts.

4. **Re-raise after self-heal** — `maybe_resume_bulk_sweep` re-raises the dispatch exception after resetting state so callers can log it. The inject hook's outer `try/except` swallows it; the beat task's `except Exception: logger.exception(...)` logs it. No silent swallowing of broker errors.

## Deviations from Plan

### Auto-applied per plan_check_correction

**Module-level import override** — The plan text showed a lazy in-function import pattern for `maybe_resume_bulk_sweep` in `tripadvisor_session.py`. The `plan_check_correction` directive overrode this to a module-level import. Applied as instructed; no circular import exists (confirmed by test isolation).

All other tasks executed exactly as specified.

## Known Stubs

None — all data flows are wired to real Redis state. `get_resume_params` returns stored values with documented fallbacks (not hardcoded/placeholder data).

## Threat Flags

No new network endpoints, auth paths, or trust-boundary surface introduced beyond what the threat model (`T-m1n-01` through `T-m1n-SC`) already covers.

## Self-Check: PASSED

| Item | Result |
|------|--------|
| brave/lanes/tripadvisor/resume.py | FOUND |
| brave/lanes/tripadvisor/sweep_progress.py | FOUND |
| tests/unit/lanes/tripadvisor/test_sweep_progress_resume.py | FOUND |
| tests/unit/lanes/tripadvisor/test_resume.py | FOUND |
| tests/unit/api/test_ta_auto_resume.py | FOUND |
| Commit 83e6b0e (RED task 1) | FOUND |
| Commit 2562d46 (GREEN task 1) | FOUND |
| Commit e95ea2c (RED task 2) | FOUND |
| Commit 8e7de27 (GREEN task 2) | FOUND |
| Full offline suite (574 passed, 5 skipped) | PASSED |
