---
phase: 10-engine-stage-depth-selector-cost-gated-collection
plan: 03
subsystem: ui
tags: [dashboard, nextjs, vitest, msw, engine, depth, cost-gate]

requires:
  - phase: 10-engine-stage-depth-selector-cost-gated-collection (plan 01)
    provides: "engine depth contract (nascente|nascente_rio|nascente_rio_mar), required-depth on POST /start (422), depth on GET /status"
provides:
  - "EngineDepth type + DEPTH_LABELS (PT-BR) in engine-api.ts"
  - "depth on EngineStatus + depth?: EngineDepth in startEngine body"
  - "/processo depth selector (3 PT-BR options) with disabled-until-chosen Ligar motor button (ENG-01 client half)"
  - "selected depth sent in POST /start body (ENG-02 client send)"
  - "active-depth read-back rendered while running from /status (ENG-02 client read-back)"
  - "MSW engine handler carries depth (default null + start echo)"
affects: [stage-badge plan 10-04, future depth-aware dashboard work]

tech-stack:
  added: []
  patterns:
    - "Required-selection UI guard: action button disabled until a cost-bearing choice is made; client never assumes a default depth (defense-in-depth; server 422 is the authority)"
    - "Status→UI read-back via a shared DEPTH_LABELS map keyed on the fixed enum, reused by selector options and the running-state display"

key-files:
  created: []
  modified:
    - dashboard/lib/engine-api.ts
    - dashboard/components/engine/EngineControl.tsx
    - dashboard/mocks/handlers/engine.ts
    - dashboard/components/engine/__tests__/EngineControl.test.tsx

key-decisions:
  - "Depth selector implemented as a native button radiogroup (role=radio) reusing existing UI primitives — no new npm packages (T-10-SC honored)"
  - "DEPTH_LABELS exported from engine-api.ts so selector options and read-back share one source of truth"
  - "Read-back rendered whenever data.depth is set in any non-idle state (running OR stopping), guarded against null"

patterns-established:
  - "Disabled-until-chosen: button disabled = pending || !selectedDepth"
  - "Capture-and-assert MSW handler reads request.json() to verify the depth round-trip offline"

requirements-completed: [ENG-01, ENG-02, ENG-07]

duration: ~9min
completed: 2026-06-23
---

# Phase 10 Plan 03: Engine Stage-Depth Selector (dashboard) Summary

**Operator-facing depth selector on /processo: three PT-BR cost-checkpoint options, a "Ligar motor" button disabled until one is picked, the chosen depth sent in the start body, and the active run's depth read back from /status — all offline via Vitest + MSW.**

## Performance

- **Duration:** ~9 min
- **Completed:** 2026-06-23
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- `EngineDepth` enum + `DEPTH_LABELS` PT-BR map added to `engine-api.ts`, with `depth` threaded onto `EngineStatus` and the `startEngine` body type (mirrors backend plan 10-01 exactly).
- `EngineControl.tsx` idle branch renders a 3-option depth radiogroup; the start button is disabled until a depth is selected (ENG-01 client half) and sends the chosen depth in `POST /start` (ENG-02 send half).
- Running/stopping branch reads back `data.depth` as "Profundidade: <PT-BR label>" on a stable `engine-active-depth` testid (ENG-02 read-back half — required, asserted).
- MSW engine handler updated: `engineStatus` defaults `depth: null` and remains overridable; `engineStartSuccess` echoes a `depth` field.
- Full dashboard suite green offline: 22 files / 140 tests, no network.

## Task Commits

Each task committed atomically (TDD RED → GREEN):

1. **Task 1: depth contract in engine-api types + MSW handler** - `a310201` (feat)
2. **Task 2 (RED): failing depth-selector tests** - `c548811` (test)
3. **Task 2 (GREEN): selector + disabled-until-chosen + read-back** - `410d885` (feat)

_Note: Task 1 is pure type/data plumbing consumed by Task 2's tests; its `bun run test -- engine-api` filter type-checks the module through EngineControl usage (no standalone engine-api test file exists by design)._

## Files Created/Modified
- `dashboard/lib/engine-api.ts` - `EngineDepth` type, `DEPTH_LABELS` PT-BR map, `depth` on `EngineStatus`, `depth?` on `startEngine` body.
- `dashboard/components/engine/EngineControl.tsx` - depth radiogroup, disabled-until-chosen start, depth in start mutation, active-depth read-back.
- `dashboard/mocks/handlers/engine.ts` - `depth: null` default + `depth` echo on start success.
- `dashboard/components/engine/__tests__/EngineControl.test.tsx` - 4 new cases (options, disabled-guard, depth-in-body, read-back) + pre-existing start test updated to pick a depth.

## Decisions Made
- Selector built from native `<button role="radio">` elements in a `role="radiogroup"` container reusing existing styling primitives — zero new dependencies (T-10-SC satisfied).
- Shared `DEPTH_LABELS` map exported from `engine-api.ts` for one source of truth across selector + read-back.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Pre-existing "start → posts and refetches status" test broke under required-selection**
- **Found during:** Task 2 (GREEN)
- **Issue:** The existing start test clicked `engine-start` directly. With the new disabled-until-chosen guard the button is disabled on load, so the click was a no-op and the status never flipped to "Varrendo".
- **Fix:** Added `await user.click(screen.getByTestId("engine-depth-nascente"))` before clicking start — mirroring the real required-selection contract. No behavioral assertion changed otherwise.
- **Files modified:** dashboard/components/engine/__tests__/EngineControl.test.tsx
- **Verification:** All 10 EngineControl tests + full 140-test suite green.
- **Committed in:** `410d885` (Task 2 GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** The fix was required because the new ENG-01 guard correctly disables a button the old test assumed enabled. No scope creep.

## Issues Encountered
None beyond the deviation above.

## Test Results

```
cd dashboard && bun run test -- EngineControl
✓ components/engine/__tests__/EngineControl.test.tsx (10 tests)  211ms
 Test Files  1 passed (1)
      Tests  10 passed (10)

cd dashboard && bun run test   (full suite)
 Test Files  22 passed (22)
      Tests  140 passed (140)
```

Acceptance greps:
- `grep -c 'depth' dashboard/components/engine/EngineControl.tsx` → 12 (≥3 ✓)
- `grep -c 'nascente_rio_mar' dashboard/lib/engine-api.ts` → 3 (≥1 ✓)

All offline — MSW only, no real network.

## Known Stubs
None. Depth is fully wired end-to-end on the client: selector → start body → status read-back, against the fixed enum.

## Threat Flags
None beyond the plan's register. T-10-06 (accidental spend) mitigated by the disabled-until-chosen guard; T-10-SC honored — no new npm packages (native radiogroup + existing Button only).

## TDD Gate Compliance
Task 2 followed RED → GREEN: `test(10-03): add failing depth-selector tests` (`c548811`) → `feat(10-03): depth selector + ... read-back` (`410d885`). No REFACTOR commit needed. (Task 1 is type/data plumbing whose contract is exercised by Task 2's tests.)

## Next Phase Readiness
- ENG-01/ENG-02 client halves complete and asserted; depth round-trip proven offline.
- Plan 10-04 (StageBadge "nascente" variant) is independent — StageBadge.tsx untouched here as instructed.

## Self-Check: PASSED

All 4 modified files present; all 3 task commits (`a310201`, `c548811`, `410d885`) found in git log; full dashboard suite green offline (140/140).

---
*Phase: 10-engine-stage-depth-selector-cost-gated-collection*
*Completed: 2026-06-23*
