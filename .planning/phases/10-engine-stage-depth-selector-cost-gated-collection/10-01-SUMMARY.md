---
phase: 10-engine-stage-depth-selector-cost-gated-collection
plan: 01
subsystem: engine
tags: [engine, depth, cost-gate, redis, fastapi]
requires:
  - brave/core/engine.py (existing state machine)
  - brave/api/routers/engine.py (existing start/stop/status)
provides:
  - "engine depth contract: NASCENTE|NASCENTE_RIO|NASCENTE_RIO_MAR + _VALID_DEPTHS"
  - "brave:engine:depth Redis key with set_depth/get_depth"
  - "get_status carries depth"
  - "POST /start server-side required-depth (422) threaded into engine_sweep_run.delay"
affects:
  - plan 10-02 (orchestrator consumes depth kwarg + constants)
  - plan 10-03 (dashboard mirrors the depth enum + start body)
tech-stack:
  added: []
  patterns:
    - "Validate untrusted body['depth'] against an allow-list BEFORE mutating state"
    - "Depth kept orthogonal to start_run (lane = entity family, depth = pipeline reach)"
key-files:
  created: []
  modified:
    - brave/core/engine.py
    - brave/api/routers/engine.py
    - tests/unit/test_engine_state.py
    - tests/integration/test_engine_endpoints.py
decisions:
  - "Depth constants exported at module level in engine.py (importable by 10-02), not buried in the router"
  - "get_depth returns None for absent OR corrupt persisted values; required-selection enforced at the API edge, not defaulted in core"
  - "engine_sweep_run.delay(depth=...) threaded now; orchestrator accepts it in 10-02 (offline tests monkeypatch dispatch)"
metrics:
  duration: ~12min
  completed: 2026-06-23
  tasks: 2
  files: 4
---

# Phase 10 Plan 01: Engine Stage-Depth Selector Summary

Operator-selectable, Redis-persisted, server-validated pipeline **depth** added to the engine state machine and its `/start`+`/status` control endpoints — the cost-checkpoint contract (`nascente` free | `nascente_rio` paid | `nascente_rio_mar` full+Mar push) that plans 10-02 (orchestrator) and 10-03 (dashboard) consume.

## What Was Built

**Task 1 — depth in the state machine (`brave/core/engine.py`):**
- Module-level constants `NASCENTE`/`NASCENTE_RIO`/`NASCENTE_RIO_MAR` (values `"nascente"`/`"nascente_rio"`/`"nascente_rio_mar"`), a `_VALID_DEPTHS` frozenset, and `_DEPTH_KEY = "brave:engine:depth"`.
- `set_depth(redis, depth)` — raises `ValueError` for anything outside the contract (never silently persisted); otherwise writes `_DEPTH_KEY`.
- `get_depth(redis)` — reuses the existing `_decode` helper; returns the depth string or `None` when absent **or corrupt** (a non-contract value persisted out-of-band reads back as `None`).
- `get_status` now carries `"depth"`.
- `start_run`/`request_stop`/`mark_idle` signatures unchanged — depth stays orthogonal to lane.

**Task 2 — required depth on `/start`, surfaced on `/status` (`brave/api/routers/engine.py`):**
- `engine_start` reads `body["depth"]` and validates it against `engine._VALID_DEPTHS`, raising `HTTPException(422, "depth is required: nascente|nascente_rio|nascente_rio_mar")` when missing/invalid.
- Validation runs **before** `start_run` and the already-running/409 branch — a bad request returns 422 even mid-run and never flips engine state.
- On success: `set_depth(redis, depth)`, `engine_sweep_run.delay(ufs=ufs, lane=lane, depth=depth)`, and the 202 body echoes `depth`.
- `require_steward_or_bearer` guards on both mutation routes left intact; broker-down revert path (`mark_idle`) preserved.
- `engine_status` surfaces `depth` via the Task-1 `get_status` change (no further router change needed).

## Tasks Completed

| Task | Name | Commits | Files |
| ---- | ---- | ------- | ----- |
| 1 | Depth in engine state machine | test `0a` RED → feat GREEN | brave/core/engine.py, tests/unit/test_engine_state.py |
| 2 | Required depth on /start + /status | test RED → feat GREEN | brave/api/routers/engine.py, tests/integration/test_engine_endpoints.py |

(See `git log` for exact short hashes — RED/GREEN gate commits present for both tasks.)

## Test Results

```
tests/unit/test_engine_state.py .............                          [13 passed]
tests/integration/test_engine_endpoints.py .............               [13 passed]
```
All offline: fakeredis only, dispatch monkeypatched, `RUN_REAL_EXTERNALS` unset, no broker. Integration run with `BRAVE_USE_FAKEREDIS=1` and `BRAVE_DB_URL` from the file's `setdefault`.

Acceptance greps:
- `grep -v '^#' brave/core/engine.py | grep -c 'brave:engine:depth'` → 1 (≥1 ✓)
- `grep -v '^#' brave/api/routers/engine.py | grep -c 'depth'` → 11 (≥3 ✓)
- `require_steward_or_bearer` present on `/start` and `/stop` (✓)

## Deviations from Plan

**1. [Rule 1 - Bug] Threaded depth into pre-existing `/start` integration tests**
- **Found during:** Task 2
- **Issue:** Existing tests (`test_start_transitions_to_running`, `test_start_twice_returns_409`, `test_stop_requests_graceful_stop`, `test_start_accepts_custom_ufs_and_lane`) called `/start` with no depth. With required-depth enforcement they would now return 422 and break.
- **Fix:** Added a valid `depth` to each of those `/start` calls (`"nascente"` / `"nascente_rio"`). No behavioral assertion of those tests changed otherwise.
- **Files modified:** tests/integration/test_engine_endpoints.py
- **Commit:** the Task-2 RED test commit.

**2. [Rule 2 - Test fixture] Widened the dispatch monkeypatch to capture kwargs**
- **Found during:** Task 2 (required by acceptance criteria)
- **Issue:** The existing `monkeypatch.setattr(..., "delay", lambda *a, **k: None)` discarded call args, so `depth` threading couldn't be asserted.
- **Fix:** Added a `dispatched` fixture; the patched `delay` records `kwargs` into it. No broker contacted.
- **Files modified:** tests/integration/test_engine_endpoints.py

## Cross-Plan Seam (expected, not a deviation)

`engine_sweep_run.delay(depth=depth)` passes a `depth` kwarg the orchestrator does not yet accept — plan **10-02** adds the parameter. Offline tests monkeypatch `delay`, so this is green now; real-broker dispatch with the new kwarg lands with 10-02. This is the contract the plan explicitly instructed to thread.

## TDD Gate Compliance

Both tasks followed RED → GREEN:
- Task 1: `test(10-01): add failing depth state-machine tests` → `feat(10-01): persist operator-selectable pipeline depth`.
- Task 2: `test(10-01): add failing required-depth endpoint tests` → `feat(10-01): enforce required depth on /start`.
No REFACTOR commits needed.

## Known Stubs

None. Depth is fully wired (persisted, validated, read back, threaded to dispatch).

## Threat Flags

None beyond the plan's register. T-10-01 (auth on /start) and T-10-02 (allow-list validation before state mutation) are both covered by explicit tests; T-10-SC — no new packages introduced.

## Self-Check: PASSED

All modified files present; all 4 task commits (2 RED + 2 GREEN) found in git log; both test files green offline.
