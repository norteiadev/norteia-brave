---
phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
plan: 03
subsystem: tripadvisor-lane
tags: [tripadvisor, sweep, redis-state, progress, dashboard-api]
requirements-completed: [TA-12]
requires:
  - brave/core/engine.py (pure Redis-state pattern, mirrored)
  - brave/api/routers/tripadvisor_session.py (status-endpoint + auth pattern, mirrored)
  - brave/api/deps.py::require_steward_or_bearer (fail-closed auth)
provides:
  - brave/lanes/tripadvisor/sweep_progress.py (start/record_page/record_error/stop_needs_bootstrap/mark_done/get_progress/get_resume_offset)
  - GET /api/v1/tripadvisor/sweep/progress (TASweepProgressResponse)
affects:
  - 15-06 (bulk producer — calls record_page/record_error)
  - 15-07 (sweep orchestration — writer: start/record_page/stop_needs_bootstrap/mark_done + resume read)
  - 15-08 (dashboard panel — polls GET /sweep/progress)
tech-stack:
  added: []
  patterns:
    - "pure Redis-HASH state module (no DB, no dispatch) mirroring brave/core/engine.py"
    - "read-only auth-guarded status endpoint mirroring session_status"
key-files:
  created:
    - brave/lanes/tripadvisor/sweep_progress.py
    - tests/unit/lanes/tripadvisor/test_sweep_progress.py
  modified:
    - brave/api/routers/tripadvisor_session.py
    - tests/unit/api/test_tripadvisor_session.py
decisions:
  - "Single Redis HASH brave:ta:sweep:progress holds the whole snapshot (one writer surface, atomic field reads)"
  - "Progress hash is secret-free by construction — offsets/counts/state/timestamps only; cookies/session stay in brave:ta:session"
  - "TASweepProgressResponse uses extra=forbid to block future field drift leaking through the read surface"
  - "record_error defined + unit-tested now as a real callable for 15-06 (does not change state)"
metrics:
  tasks: 2
  files-created: 2
  files-modified: 2
  tests-added: 16
  duration: ~25m
  completed: 2026-06-26
---

# Phase 15 Plan 03: Live sweep-progress backend (Redis-state module + auth-guarded endpoint) Summary

A pure fakeredis-testable Redis-HASH progress module (`brave:ta:sweep:progress`) plus a bearer/steward-guarded `GET /api/v1/tripadvisor/sweep/progress` read endpoint — the single writer surface the bulk sweep (15-07) writes and the dashboard panel (15-08) polls, secret-free and 401 fail-closed.

## What was built

### Task 1 — `sweep_progress.py` pure Redis-state module (TDD)
Mirrors `brave/core/engine.py` exactly: `_decode` helper verbatim, module-level `_PROGRESS_KEY = "brave:ta:sweep:progress"` (a Redis HASH following the `brave:ta:*` convention), pure functions over a sync Redis client — no DB, no dispatch.

Functions:
- `start(redis, pages_total, resume_from_offset=0)` — HSET state=running, pages_total, zeroed counters, current/last_completed_offset seeded from `resume_from_offset`, started_at + updated_at.
- `record_page(redis, offset, ingested_delta)` — HINCRBY pages_done +1 and attractions_ingested +delta; HSET current_offset + last_completed_offset = offset; bump updated_at.
- `record_error(redis)` — HINCRBY error_count +1 (a real callable for 15-06; does NOT change state).
- `stop_needs_bootstrap(redis)` / `mark_done(redis)` — terminal states.
- `get_progress(redis) -> dict` — snapshot with EXACTLY the endpoint field set; ints decoded via `_decode`; returns `state="idle"` + zeros when the hash is absent.
- `get_resume_offset(redis) -> int` — reads `last_completed_offset` (0 when absent).

Resume contract pinned by test: after `start(pages_total=334)` then `record_page(offset=30, ingested_delta=30)`, `get_resume_offset()==30` and the consumer computes `start_page = 30 // 30 + 1 = 2` (offset 60 — the page AFTER the last completed).

### Task 2 — `GET /api/v1/tripadvisor/sweep/progress` endpoint
Added `TASweepProgressResponse(BaseModel, extra="forbid")` (Literal state ∈ {running, done, stopped_needs_bootstrap, idle}; pages_done/pages_total/attractions_ingested/current_offset/error_count: int; started_at: str | None) and a read-only handler under `dependencies=[Depends(require_steward_or_bearer)]` that returns `TASweepProgressResponse(**sweep_progress.get_progress(redis))`. Module imported as `sweep_progress_state` to avoid shadowing the handler name.

## Security / threat mitigations

- **T-15-03-01 (Information Disclosure — endpoint):** `require_steward_or_bearer` (constant-time, fail-closed). Asserted: unauthenticated `GET /sweep/progress` → 401.
- **T-15-03-02 (Information Disclosure — hash + logs):** the hash stores only offsets/counts/state/timestamps. Two layers verify this: a module test asserts the live hash keys are a subset of the non-secret whitelist and disjoint from {cookies, session, session_id, datadome, proxy, user_agent, query_ids}; an endpoint test asserts the JSON response carries none of those fields. `extra="forbid"` on the response model blocks future field drift. No cookie/session value is logged (the endpoint logs nothing sensitive — it is a verbatim serialize).

## Tests

16 tests added (10 module + 6 endpoint), all 100% offline via fakeredis with `RUN_REAL_EXTERNALS` unset:
- Module: key convention, absent→idle, absent resume offset=0, start running snapshot, resume-offset seeding, record_page increments + resume arithmetic, two-page accumulation, record_error increments (state unchanged), terminal states, secret-free invariant.
- Endpoint: idle when no run, running snapshot, no-secret-fields response, 401 unauthenticated.

Acceptance commands (both green):
- `pytest tests/unit/lanes/tripadvisor/test_sweep_progress.py -x -q` → 12 passed
- `pytest tests/unit/api/test_tripadvisor_session.py -k "sweep_progress or progress or auth" -x -q` → 6 passed
- Combined run of both files → 34 passed (no regressions to the existing session suite).

## Deviations from Plan

None — plan executed exactly as written. (Module imported under the alias `sweep_progress_state` in the router to avoid the handler/module name collision the patterns doc anticipated; not a behavior deviation.)

## Known Stubs

None. The module's writer functions (`start`, `record_page`, `stop_needs_bootstrap`, `mark_done`) and the resume read are not yet *called* by orchestration — that wiring is explicitly 15-06/15-07's scope per the plan dependency graph. They are real, tested callables, not stubs.

## Commits

- `0a05046` test(15-03): add failing tests for sweep_progress Redis-state module [TA-12]
- `6e15be0` feat(15-03): implement sweep_progress Redis-state module [TA-12]
- `5d59943` feat(15-03): add auth-guarded GET /sweep/progress endpoint [TA-12]

## Self-Check: PASSED

- brave/lanes/tripadvisor/sweep_progress.py — FOUND
- tests/unit/lanes/tripadvisor/test_sweep_progress.py — FOUND
- brave/api/routers/tripadvisor_session.py (modified: TASweepProgressResponse + /sweep/progress) — FOUND
- tests/unit/api/test_tripadvisor_session.py (modified: 4 new progress tests) — FOUND
- Commits 0a05046, 6e15be0, 5d59943 — present in git log
