---
phase: 12-tripadvisor-session-injection-seam-real-browser-bootstrap-ht
plan: "04"
subsystem: tripadvisor-session-failfast-dashboard
tags: [tripadvisor, session-injection, celery, redis, dashboard, tdd]
dependency_graph:
  requires:
    - brave/lanes/tripadvisor/client.py (SessionMissingError, SessionExpiredError — plan 12-03)
    - GET /api/v1/tripadvisor/session/status (TASessionStatus shape — plan 12-02)
    - brave/core/engine.py (set_source/get_source pattern)
    - dashboard/lib/engine-api.ts (EngineStatus, ENGINE_REFETCH_INTERVAL_MS)
  provides:
    - sweep_tripadvisor fail-fast on SessionMissingError + SessionExpiredError
    - brave:ta:needs_bootstrap Redis marker set on session fail-fast
    - TASessionStatus type + fetchTASessionStatus() + taSessionKeys in engine-api.ts
    - EngineControl session-health pill (three states: Pronta/Precisa bootstrap/Expirada)
    - taSessionStatus() MSW handler in engine.ts mock handlers
  affects:
    - brave/tasks/pipeline.py
    - dashboard/components/engine/EngineControl.tsx
    - dashboard/lib/engine-api.ts
    - dashboard/mocks/handlers/engine.ts
    - tests/unit/tasks/test_sweep_tripadvisor.py
    - dashboard/components/engine/__tests__/EngineControl.test.tsx
tech_stack:
  added: []
  patterns:
    - TDD (RED→GREEN): failing tests committed before implementation (both tasks)
    - Celery bind=True task testing via __wrapped__.__func__ for mock self injection
    - Best-effort Redis marker (_mark_needs_bootstrap): error swallowed, never masks session error
    - T-12-04-01: log only error_type class name, never exc str (cookie fragment safety)
    - TASessionStatus three-state discriminator: present/reason=null/reason=needs_bootstrap
    - TanStack Query enabled gate: session status fetched only when source=tripadvisor active
key_files:
  created:
    - tests/unit/tasks/__init__.py
    - tests/unit/tasks/test_sweep_tripadvisor.py
  modified:
    - brave/tasks/pipeline.py
    - dashboard/lib/engine-api.ts
    - dashboard/mocks/handlers/engine.ts
    - dashboard/components/engine/EngineControl.tsx
    - dashboard/components/engine/__tests__/EngineControl.test.tsx
decisions:
  - "_mark_needs_bootstrap uses os.environ.get('BRAVE_DB_REDIS_URL') directly (not AppConfig) to avoid circular imports in the module-level helper; best-effort pattern matches T-12-04-02 (accept: no TTL needed)"
  - "SessionMissingError/SessionExpiredError caught BEFORE PermanentError in the except chain — prevents session errors from reaching the retry or quarantine paths"
  - "Celery bind=True task tested via __wrapped__.__func__(mock_self, ...) to inject a recording mock self; sweep_tripadvisor.run() would use the real task instance"
  - "TripAdvisorDestinosIngest/TripAdvisorAtrativosIngest patched via AsyncMock that propagates stub client errors — cleanest approach without full destinos/atrativos test infrastructure"
  - "node_modules symlinked from main repo to worktree dashboard for running vitest; symlink not committed (node_modules is gitignored)"
metrics:
  duration: "~11 min"
  completed: "2026-06-24"
  tasks_completed: 2
  files_created: 2
  files_modified: 5
---

# Phase 12 Plan 04: sweep_tripadvisor Fail-Fast + EngineControl Session Health Pill Summary

**One-liner:** sweep_tripadvisor catches SessionMissingError/SessionExpiredError before PermanentError, marks Redis needs_bootstrap marker, and EngineControl renders a three-state session-health pill when TripAdvisor source is active.

## What Was Built

### Task 1: sweep_tripadvisor fail-fast (brave/tasks/pipeline.py)

Two additions at the module level:
- `_TA_NEEDS_BOOTSTRAP_KEY = "brave:ta:needs_bootstrap"` — the Redis key written on session fail-fast
- `_mark_needs_bootstrap()` — best-effort helper; imports Redis lazily, swallows errors, never logs exc str (T-12-04-01)

`sweep_tripadvisor` now imports `SessionMissingError, SessionExpiredError` from `brave.lanes.tripadvisor.client` at function entry (alongside the existing lazy imports) and adds a new except clause before `PermanentError`:

```python
except (SessionMissingError, SessionExpiredError) as exc:
    session.rollback()
    _mark_needs_bootstrap()
    logger.warning(
        "sweep_tripadvisor_session_fail_fast",
        uf=uf,
        error_type=type(exc).__name__,  # Never log exc str (T-12-04-01)
    )
    return  # No retry, no quarantine
```

### tests/unit/tasks/test_sweep_tripadvisor.py (5 tests)

- `test_missing_session_fails_fast_no_retry` — SessionMissingError → 0 retry calls
- `test_missing_session_marks_needs_bootstrap` — SessionMissingError → Redis key set
- `test_session_expired_mid_sweep_stops` — SessionExpiredError → 0 retries + Redis key
- `test_normal_exception_still_retries` — RuntimeError → 1 retry call (regression)
- `test_session_missing_error_not_quarantined` — SessionMissingError → 0 quarantine calls

Testing approach: `sweep_tripadvisor.__wrapped__.__func__(mock_self, uf="BA")` with recording mock self; TripAdvisorDestinosIngest patched to AsyncMock that propagates stub client exceptions.

### Task 2: Dashboard session-health pill

**dashboard/lib/engine-api.ts** — added:
- `TASessionStatus` interface (present, expires_in, query_ids, reason)
- `taSessionKeys.status` query key
- `fetchTASessionStatus()` fetcher

**dashboard/mocks/handlers/engine.ts** — added:
- `taSessionStatus(overrides?)` handler factory for `GET /api/api/v1/tripadvisor/session/status`
- Added to `engineHandlers` default barrel

**dashboard/components/engine/EngineControl.tsx** — added:
- `sessionLabel(s)` — Pronta / Precisa bootstrap / Expirada
- `sessionColor(s)` — emerald / amber / rose
- `showSessionStatus` gate: `selectedSource === "tripadvisor" || (state !== "idle" && data?.source === "tripadvisor")`
- `useQuery` for `taSessionKeys.status` with `enabled: showSessionStatus`
- Session-health pill JSX with `data-testid="ta-session-status"` inside `data-testid="ta-session-health"` wrapper; shows expires_in minutes alongside "Pronta"

**dashboard/components/engine/__tests__/EngineControl.test.tsx** — added 5 new tests in `describe("EngineControl — session health pill (TA-13)")`.

## TDD Gate Compliance

| Gate | Task 1 (pipeline) | Task 2 (dashboard) |
|------|-------------------|---------------------|
| RED commit | `61fbb5b` — test(12-04): add failing tests for sweep_tripadvisor session fail-fast | `5d07efd` — test(12-04): add failing tests for TA session-health pill in EngineControl |
| GREEN commit | `ceb0884` — feat(12-04): sweep_tripadvisor fail-fast on SessionMissingError/SessionExpiredError | `c40eca2` — feat(12-04): add TA session-health pill to EngineControl dashboard (TA-13) |
| REFACTOR | not needed | not needed |

## Deviations from Plan

### Adjusted Implementation Details

**Test invocation via `__wrapped__.__func__`**
- **Found during:** Task 1 RED debugging
- **Issue:** `sweep_tripadvisor.run(mock_self, uf='BA')` fails with "got multiple values for argument 'uf'" because `run` is already bound to the task instance (bind=True) — `self` cannot be injected this way.
- **Fix:** Use `sweep_tripadvisor.__wrapped__.__func__(mock_self, uf='BA')` to call the underlying function with explicit mock self.
- **Impact:** Test correctness only; no production code change.

**TripAdvisorDestinosIngest/AtrativosIngest patched via AsyncMock propagation**
- **Found during:** Task 1 RED debugging
- **Issue:** Patching `brave.tasks.pipeline.TripAdvisorClient` with a stub class doesn't work because the task uses a local import (`from brave.lanes.tripadvisor.client import TripAdvisorClient`) which bypasses the module-level attribute. Similarly, `brave.lanes.tripadvisor.client.TripAdvisorClient` patching would affect the client module but the ingest classes (Destinos/Atrativos) hold their own ta_client instance created at task time.
- **Fix:** Patch `TripAdvisorDestinosIngest` and `TripAdvisorAtrativosIngest` directly with AsyncMock produce() that calls the stub client's fetch_destinations to propagate errors. This tests the actual error propagation path cleanly.
- **Impact:** More robust test isolation; same behavioural guarantees.

**node_modules symlinked for worktree dashboard**
- **Found during:** Task 2 RED (vitest can't find node_modules in worktree)
- **Fix:** `ln -s /main/dashboard/node_modules /worktree/dashboard/node_modules` (not committed).
- **Impact:** Worktree runs vitest correctly; symlink is ephemeral (node_modules gitignored).

## Threat Model Coverage

| Threat | Status |
|--------|--------|
| T-12-04-01 Info Disclosure via error log | Mitigated — `logger.warning` logs only `error_type=type(exc).__name__`; exc str never logged |
| T-12-04-02 Redis key accumulates without TTL | Accepted — key is a simple "1" flag, overwritten on each bootstrap failure, no content growth |
| T-12-04-03 Session status visible to dashboard users | Accepted — Bearer auth already required; TASessionStatus contains no cookie values |
| T-12-04-SC Tampering via npm/pip install | Confirmed — no new packages added |

## Known Stubs

None — all data flows are wired (Redis → sweep_tripadvisor → mark signal; FastAPI status endpoint → EngineControl → pill render).

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced beyond the plan's threat model.

## Self-Check: PASSED

Files created/modified:
- [FOUND] tests/unit/tasks/__init__.py
- [FOUND] tests/unit/tasks/test_sweep_tripadvisor.py
- [FOUND] brave/tasks/pipeline.py (modified)
- [FOUND] dashboard/lib/engine-api.ts (modified)
- [FOUND] dashboard/mocks/handlers/engine.ts (modified)
- [FOUND] dashboard/components/engine/EngineControl.tsx (modified)
- [FOUND] dashboard/components/engine/__tests__/EngineControl.test.tsx (modified)

Commits:
- [FOUND] 61fbb5b — test(12-04): add failing tests for sweep_tripadvisor session fail-fast
- [FOUND] ceb0884 — feat(12-04): sweep_tripadvisor fail-fast on SessionMissingError/SessionExpiredError
- [FOUND] 5d07efd — test(12-04): add failing tests for TA session-health pill in EngineControl
- [FOUND] c40eca2 — feat(12-04): add TA session-health pill to EngineControl dashboard (TA-13)

Test results:
- Backend (5 new + 400 total): PASSED (0 failures)
- Dashboard (15 EngineControl + 158 total): PASSED (0 failures)
