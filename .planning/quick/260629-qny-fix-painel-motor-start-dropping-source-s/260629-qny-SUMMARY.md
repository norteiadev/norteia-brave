---
phase: quick-260629-qny
plan: 01
subsystem: painel-motor / engine-api
tags: [bug-fix, tdd, backend, dashboard, engine-source]
dependency_graph:
  requires: []
  provides: [POST /api/v1/engine/source, setEngineSource client, source propagation to /start]
  affects: [brave/api/routers/engine.py, dashboard/lib/engine-api.ts, PainelOrigem.tsx, PainelTopbar.tsx]
tech_stack:
  added: []
  patterns: [TDD RED→GREEN, useMutation for POST side-effect]
key_files:
  created:
    - tests/unit/api/test_engine_set_source_endpoint.py
  modified:
    - brave/api/routers/engine.py
    - dashboard/lib/engine-api.ts
    - dashboard/mocks/handlers/engine.ts
    - dashboard/components/painel/PainelOrigem.tsx
    - dashboard/components/painel/PainelTopbar.tsx
    - dashboard/components/painel/__tests__/PainelOrigem.test.tsx
    - dashboard/components/painel/__tests__/PainelTopbar.test.tsx
decisions:
  - Renamed mutation variable to `activateSource` (not `setSource`) to avoid conflict with useState setter `[source, setSource]` in PainelOrigem
metrics:
  duration: ~12 minutes
  completed: "2026-06-29T22:44:43Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 1
  files_modified: 6
---

# Phase quick-260629-qny Plan 01: Fix Painel Motor Start Dropping Source Summary

**One-liner:** Added POST /api/v1/engine/source endpoint + wired setEngineSource in PainelOrigem (TA→tripadvisor, mtur→default) and PainelTopbar start mutation ({depth, source}) so TripAdvisor sweep lane actually reaches the backend orchestrator.

## Tasks Completed

| Task | Name | Commit | Status |
|------|------|--------|--------|
| 1 | Backend — POST /api/v1/engine/source endpoint (RED→GREEN) | 239d2e8 | Done |
| 2 | Dashboard — setEngineSource client + PainelOrigem + PainelTopbar wiring (RED→GREEN) | 96c661e | Done |

## Test Results

**Backend (Task 1):**
```
tests/unit/api/test_engine_set_source_endpoint.py — 4 passed
tests/unit/api/test_engine_source.py              — 6 passed (no regression)
tests/unit/api/test_engine_latch.py               — 4 passed (no regression)
```

**Dashboard (Task 2):**
```
Test Files: 42 passed (42)
Tests:      287 passed (287)  — up from 276 (11 new + 6 existing files touched)
```
- New: test_engine_set_source_valid, test_engine_set_source_default, test_engine_set_source_invalid_422, test_engine_set_source_no_auth (backend)
- New: "saving TA fires POST /engine/source {source: tripadvisor}" (PainelOrigem)
- New: "saving mtur fires POST /engine/source {source: default}" (PainelOrigem)
- New: "source=tripadvisor in status: picking depth fires POST /start with {depth, source: tripadvisor}" (PainelTopbar)
- taBlocked gate tests: still pass (verified in full suite)

## Changes Made

### Backend: POST /api/v1/engine/source (brave/api/routers/engine.py)
Added `engine_set_source` handler between engine_stop and the end of the router:
- Auth: `require_steward_or_bearer` (same dep as /start and /stop)
- Body: `dict = Body(default={})` — reads `body.get("source")`
- Validates against `collection_engine._VALID_SOURCES` → 422 on invalid before any Redis write
- Persists via `collection_engine.set_source(redis, source)`
- Returns `{"source": source}` with status 200
- No RunHistory row, no dispatch — configuration write only

### Dashboard: engine-api.ts
Added `setEngineSource(source: EngineSource)` export:
- Calls `apiFetch("api/v1/engine/source", { method: "POST", body: JSON.stringify({ source }) })`
- Returns `Promise<{ source: EngineSource }>`

### Dashboard: mocks/handlers/engine.ts
Added `engineSetSourceSuccess()` MSW handler:
- Intercepts `http.post(`${BASE}/source`, ...)` 
- Echoes `{ source: body.source }` with 200

### Dashboard: PainelOrigem.tsx
- Added `activateSource = useMutation({ mutationFn: (src) => setEngineSource(src), onSuccess: () => invalidate(engineKeys.status) })`
  - Named `activateSource` (not `setSource`) to avoid conflict with the useState setter `[source, setSource]`
- `inject.onSuccess`: fires `activateSource.mutate("tripadvisor")` after invalidating taSessionKeys
- Non-TA `onSave` branch: fires `activateSource.mutate("default")` before `onClose()`
  - Ensures switching back from TripAdvisor to mtur/google_places clears the stale Redis source key

### Dashboard: PainelTopbar.tsx
- `start.mutationFn`: changed from `(depth) => startEngine({ depth })` to `(depth) => startEngine({ depth, source })`
- `source` is already in scope from `const source: EngineSource = data?.source ?? "default"` (captures from render closure)

### Test fixes (non-breaking)
- **Fix 1** — PainelTopbar.test.tsx line 88: `toEqual` → `toMatchObject` so the existing "picking a depth fires /start" test doesn't break when `source` is added to the body
- **Fix 2** — PainelOrigem.test.tsx "submits parsed cURL body" test: added `engineSetSourceSuccess()` to server.use() so the new `activateSource.mutate("tripadvisor")` call in inject.onSuccess doesn't emit MSW unhandled-request noise

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Naming conflict: setSource mutation vs useState setter**
- **Found during:** Task 2 GREEN — esbuild compile error "symbol 'setSource' has already been declared"
- **Issue:** PainelOrigem already uses `const [source, setSource] = useState(...)`. Adding another `const setSource = useMutation(...)` caused a duplicate declaration error.
- **Fix:** Renamed the mutation variable to `activateSource`. All internal call sites updated accordingly (`activateSource.mutate("tripadvisor")`, `activateSource.mutate("default")`). Test assertions use MSW-intercepted HTTP calls, not the variable name directly, so no test changes were needed.
- **Files modified:** dashboard/components/painel/PainelOrigem.tsx
- **Commit:** 96c661e (folded into the GREEN commit)

## Known Stubs

None — all wiring is live end-to-end (HTTP calls intercepted by MSW in tests, real endpoint in production).

## Threat Flags

No new trust boundary surface beyond what the plan's threat model (T-qny-01, T-qny-02, T-qny-03) already covers:
- POST /api/v1/engine/source gated by `require_steward_or_bearer` ✓
- 422 on invalid source before any Redis write ✓
- `set_source()` itself raises ValueError as a second-layer guard ✓
- No new spend triggered (no start_run, no Celery dispatch) ✓

## Self-Check: PASSED

Files exist:
- tests/unit/api/test_engine_set_source_endpoint.py — FOUND
- brave/api/routers/engine.py (engine_set_source added) — FOUND
- dashboard/lib/engine-api.ts (setEngineSource added) — FOUND
- dashboard/mocks/handlers/engine.ts (engineSetSourceSuccess added) — FOUND
- dashboard/components/painel/PainelOrigem.tsx (activateSource mutation wired) — FOUND
- dashboard/components/painel/PainelTopbar.tsx (source passed to startEngine) — FOUND

Commits exist:
- 239d2e8 (Task 1: backend + tests) — FOUND
- 96c661e (Task 2: dashboard wiring + tests) — FOUND
