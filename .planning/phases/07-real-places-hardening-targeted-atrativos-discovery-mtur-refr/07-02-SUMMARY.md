---
phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr
plan: "02"
subsystem: api
tags: [dlq, refactor, service-extraction, validate-and-promote, fastapi, sqlalchemy]

# Dependency graph
requires:
  - phase: 01-brave-core-score-gate-boundary-contract
    provides: RioRecord model, promote_to_mar, reprocess_record, flag_modified pattern
  - phase: 02-destinos-lane
    provides: DLQ router with validate_dlq_record and validate_batch endpoints
provides:
  - brave/core/dlq/service.py with validate_and_promote_rio (importable by load-test harness)
  - brave/core/dlq/__init__.py (package init)
  - brave/api/routers/dlq.py delegates both validate call sites to the service
affects: [07-05-load-test-harness, any future code calling the 4-step validate pattern]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Service extraction: 4-step validate-and-promote pattern extracted from router into brave/core/dlq/service.py — router keeps auth/audit/Celery-dispatch; service owns flag_modified+reprocess_record+promote_to_mar"
    - "Top-level imports in service (vs lazy in router): service.py uses module-level imports for harness compatibility"

key-files:
  created:
    - brave/core/dlq/__init__.py
    - brave/core/dlq/service.py
  modified:
    - brave/api/routers/dlq.py

key-decisions:
  - "Service does NOT dispatch Celery tasks or write audit rows — caller (router) is responsible (T-07-05)"
  - "Router's if routing=='mar' block dispatches push_destination_task only; promote_to_mar removed from router (T-07-06 double-promote prevention)"
  - "Service uses top-level imports (not lazy) so loadtest harness can import it without triggering lazy-import side effects"

patterns-established:
  - "brave/core/dlq/service.py: pure domain service, no HTTP, no Celery, no audit — importable from any context"

requirements-completed: [PLACE-06]

# Metrics
duration: 3min
completed: 2026-06-18
---

# Phase 7 Plan 02: DLQ Service Extraction Summary

**Extracted the 4-step DLQ validate-and-promote pattern (flag_modified+reprocess_record+promote_to_mar) from dlq.py into brave/core/dlq/service.py::validate_and_promote_rio, eliminating inline duplication and providing an importable function for the load-test harness (Plan 07-05)**

## Performance

- **Duration:** 3 min
- **Started:** 2026-06-18T16:20:39Z
- **Completed:** 2026-06-18T16:23:14Z
- **Tasks:** 2
- **Files modified:** 3 (2 created, 1 modified)

## Accomplishments
- Created brave/core/dlq/__init__.py and brave/core/dlq/service.py with validate_and_promote_rio — the exact 4-step pattern with correct Pitfall 3 (reassign+flag_modified) and Pitfall 4 (reprocess_record not process_nascente_record) handling
- Refactored both call sites in dlq.py (validate_dlq_record + validate_batch inner loop) to delegate to service — inline duplication removed, ~42 lines reduced to ~2 lines each
- Eliminated double-promote risk (T-07-06): router's if routing=='mar' block dispatches push_destination_task only; promote_to_mar no longer called in router
- All 398 offline tests pass after refactor

## Task Commits

1. **Task 1: Create brave/core/dlq/service.py** - `10f96a2` (feat)
2. **Task 2: Refactor dlq.py to delegate to service** - `eb34432` (refactor)

**Plan metadata:** (pending final commit)

## Files Created/Modified
- `brave/core/dlq/__init__.py` - Empty package init for brave.core.dlq
- `brave/core/dlq/service.py` - validate_and_promote_rio: 4-step flag_modified+reprocess_record+refresh+promote_to_mar pattern with top-level imports
- `brave/api/routers/dlq.py` - Both validate_dlq_record and validate_batch delegate to validate_and_promote_rio; inline normalized/flag_modified/reprocess blocks removed; promote_to_mar import + calls removed from router

## Decisions Made
- Service uses top-level imports (not lazy like dlq.py) — loadtest harness imports service.py directly, lazy imports would be awkward
- Router's except Exception fallback for no-broker environments is now `pass` (not a promote_to_mar call) — service already handled promotion before the Celery dispatch was attempted; fallback is silent no-op

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Docstring in dlq.py contained 'validacao_humana_value' string**
- **Found during:** Task 2 verification
- **Issue:** The plan's verification assertion `assert 'validacao_humana_value' not in src` failed because the endpoint docstring mentioned the field by name, even though no inline code remained
- **Fix:** Updated validate_dlq_record docstring to describe behavior without naming the internal field constant (now references "delegate to validate_and_promote_rio" pattern instead)
- **Files modified:** brave/api/routers/dlq.py
- **Verification:** All 4 plan assertions pass; 398 tests pass
- **Committed in:** eb34432 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - docstring string matching assertion)
**Impact on plan:** Trivial docstring wording change — no behavior change.

## Issues Encountered
None beyond the docstring assertion above.

## User Setup Required
None - pure internal refactor, no external service configuration required.

## Next Phase Readiness
- validate_and_promote_rio is importable from brave.core.dlq.service — Plan 07-05 load-test harness can call it directly without HTTP overhead
- All existing DLQ router tests continue to pass — no behavior regression
- T-07-06 double-promote risk resolved

---
*Phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr*
*Completed: 2026-06-18*
