---
phase: 260628-jvk
plan: "01"
subsystem: engine-toggle
tags: [engine, redis-latch, dashboard, ux-fix]
dependency_graph:
  requires: []
  provides: [engine-enabled-latch, engine-toggle-persistence]
  affects: [brave/core/engine.py, brave/api/routers/engine.py, dashboard/lib/engine-api.ts, dashboard/components/painel/PainelTopbar.tsx, dashboard/components/engine/EngineControl.tsx]
tech_stack:
  added: []
  patterns: [redis-latch-for-operator-intent, enabled-vs-state-separation]
key_files:
  created:
    - tests/unit/api/test_engine_latch.py
  modified:
    - brave/core/engine.py
    - brave/api/routers/engine.py
    - tests/unit/test_engine_state.py
    - dashboard/lib/engine-api.ts
    - dashboard/mocks/handlers/engine.ts
    - dashboard/components/painel/PainelTopbar.tsx
    - dashboard/components/engine/EngineControl.tsx
    - dashboard/components/painel/__tests__/PainelTopbar.test.tsx
    - dashboard/components/engine/__tests__/EngineControl.test.tsx
decisions:
  - "enabled latch is orthogonal to state — state is the dispatch lifecycle signal; enabled is operator intent. mark_idle never clears enabled."
  - "engine_stop always clears enabled regardless of whether engine was running — so toggle stays OFF even on idle stop call."
  - "motorOn = data?.enabled ?? (state !== idle) as fallback for clients not yet receiving the new field."
  - "EngineControl branch condition changed from state===idle to !enabled for symmetry with PainelTopbar."
metrics:
  duration: ~20min
  completed: 2026-06-28
  tasks_completed: 2
  files_modified: 9
---

# Phase 260628-jvk Plan 01: Engine Toggle Persistence Summary

**One-liner:** Adds a Redis `enabled` latch that persists operator intent across dispatch-lifecycle idle transitions, so the dashboard motor switch stays ON until the operator explicitly clicks stop.

## What Was Built

The dashboard motor switch was reverting to OFF on page refresh because `motorOn` was derived from `state`, which returns to `idle` the moment `engine_sweep_run` finishes dispatching all UFs — even though Celery workers continue processing.

**Backend (Task 1):**
- Added `_ENABLED_KEY = "brave:engine:enabled"` Redis key
- Added `set_enabled(redis, enabled)` and `is_enabled(redis)` to `brave/core/engine.py`
- `start_run` now sets the latch (`redis.set(_ENABLED_KEY, "1")`)
- `mark_idle` does NOT touch the latch — latch is independent of dispatch lifecycle
- `get_status` now always includes `"enabled": is_enabled(redis)` in the response dict
- `engine_stop` router now calls `set_enabled(redis, False)` unconditionally before returning (even when engine was already idle)

**Dashboard (Task 2):**
- `EngineStatus.enabled: boolean` added to the TypeScript interface
- `engineStatus()` MSW factory defaults `enabled: false`
- `PainelTopbar`: `motorOn = data?.enabled ?? (state !== "idle")` with graceful fallback; `onToggleMotor` branches on `motorOn` (not `state === "idle"`); motor label derived from `motorOn` (shows "Ligado" when `enabled=true` even if `state=idle`)
- `EngineControl`: `const enabled = data?.enabled ?? false`; start/stop branch condition changed from `state === "idle"` to `!enabled`

**Tests:**
- Backend: 7 new assertions in `test_engine_state.py` + new `tests/unit/api/test_engine_latch.py` (4 API-level tests)
- Dashboard: 3 new Vitest tests (2 in PainelTopbar, 1 in EngineControl); updated 6 existing tests to include `enabled` field in status overrides

## TDD Gate Compliance

- Task 1 RED: `b77c95e` — failing tests committed before implementation
- Task 1 GREEN: `f158409` — implementation that makes tests pass
- Task 2 RED: `958471f` — failing dashboard tests committed before implementation
- Task 2 GREEN: `279afa7` — dashboard implementation that makes tests pass

## Test Results

| Suite | Before | After |
|-------|--------|-------|
| Backend unit (`tests/unit/`) | 543 passed, 5 skipped | 550 passed, 5 skipped |
| Dashboard (Vitest) | 276 passed | 281 passed |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `stopping` state test needed `enabled: true`**
- **Found during:** Task 2 GREEN — first run of dashboard tests
- **Issue:** The existing "stopping state disables the button" test used `engineStatus({ state: "stopping" })` without `enabled`. After changing `EngineControl` to branch on `!enabled` instead of `state === "idle"`, the default `enabled: false` caused the stop button not to render, failing the test.
- **Fix:** Added `enabled: true` to the stopping state override in `EngineControl.test.tsx` (consistent with how the running state and other tests were already updated).
- **Files modified:** `dashboard/components/engine/__tests__/EngineControl.test.tsx`
- **Commit:** `279afa7`

## Known Stubs

None — all data is wired to real Redis state. The `enabled` field is a new Redis key, not a placeholder.

## Threat Flags

No new trust boundaries introduced. The `brave:engine:enabled` Redis key sits behind the same `require_steward_or_bearer` guard as the existing engine state key (T-jvk-01: accepted per threat register).

## Self-Check: PASSED
