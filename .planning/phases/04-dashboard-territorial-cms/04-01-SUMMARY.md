---
phase: 04-dashboard-territorial-cms
plan: 01
subsystem: auth
tags: [fastapi, bearer-auth, hmac, pydantic-settings, dependency-injection]

# Dependency graph
requires:
  - phase: 02-destinos-lane
    provides: DLQ mutation endpoints (reprocess/validate/validate-batch/descarte) + require_steward pattern
  - phase: 03-atrativos-lane
    provides: WhatsApp gate mutation endpoints (approve/reject) + local require_steward
provides:
  - DashboardConfig (BRAVE_DASHBOARD_BEARER_TOKEN) — fail-closed Bearer config
  - require_bearer FastAPI dependency — constant-time, fail-closed, never-logged Bearer gate (D-02)
  - require_steward_or_bearer either-or guard on the 4 DLQ + 2 gate mutation routes (R4)
  - tests/integration/test_dashboard_endpoints.py — offline Bearer + either-or contract
affects: [dashboard read endpoints, dashboard.py router, dashboard BFF, conversations, cost, funnels, monitor]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Bearer-at-the-edge BFF auth (D-02): require_bearer mirrors require_steward, swaps header"
    - "Either-or mutation auth (R4): valid X-Steward-Secret OR valid Bearer passes, both hmac.compare_digest, fail-closed"

key-files:
  created:
    - tests/integration/test_dashboard_endpoints.py
  modified:
    - brave/config/settings.py
    - brave/api/deps.py
    - brave/api/routers/dlq.py
    - brave/api/routers/atrativos_gate.py

key-decisions:
  - "require_steward_or_bearer placed in deps.py (co-located with get_steward_config/get_dashboard_config), not duplicated per-router"
  - "Local require_steward functions in dlq.py/atrativos_gate.py left defined (unused by routes now) to avoid touching unrelated code; ruff-clean"
  - "Either-or fail-closed: an unset BRAVE_DASHBOARD_BEARER_TOKEN can never validate a Bearer-presented request (write-to-production boundary preserved)"

patterns-established:
  - "Bearer auth dependency: Header(None, alias='Authorization') → removeprefix('Bearer ').strip() → hmac.compare_digest"
  - "Either-or guard: try steward compare, return on match; else try bearer compare, return on match; else 401"

requirements-completed: [DASH-06]

# Metrics
duration: 6min
completed: 2026-06-16
---

# Phase 4 Plan 01: Dashboard Bearer Auth + Either-Or Mutation Guard Summary

**Bearer-at-the-edge FastAPI auth (DASH-06/D-02) with constant-time hmac.compare_digest plus an either-or steward/Bearer guard letting a single dashboard operator token drive the existing DLQ + WhatsApp-gate mutation endpoints without breaking Phase 2/3 steward callers (R4).**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-06-16T19:07Z
- **Completed:** 2026-06-16T19:13Z
- **Tasks:** 2 (both TDD: RED → GREEN)
- **Files modified:** 5

## Accomplishments
- `DashboardConfig` reads `BRAVE_DASHBOARD_BEARER_TOKEN` (env_prefix only, no alias per CR-02), fail-closed default `Field(default="")`.
- `require_bearer` dependency mirrors `require_steward` exactly — strips the `Bearer ` prefix, constant-time `hmac.compare_digest`, fail-closed on unset token, 401 before any DB work, token never logged.
- `require_steward_or_bearer` either-or guard: passes on a valid `X-Steward-Secret` OR a valid `Authorization: Bearer`, both compared constant-time and fail-closed; swapped onto the 4 DLQ mutation routes and 2 atrativos-gate mutation routes.
- Offline pytest (18 cases) proving the Bearer security contract and either-or coexistence; the live-DB integration cases additionally prove bearer-only and steward-only both pass auth end-to-end (404, not 401).
- Existing `test_fastapi_endpoints.py` (9 cases) still green — no Phase 2/3 steward regression.

## Task Commits

Each task committed atomically (TDD RED → GREEN):

1. **Task 1: DashboardConfig + require_bearer** — `04c8dd1` (test, RED) → `cc4110d` (feat, GREEN)
2. **Task 2: Either-or steward/Bearer guard (R4)** — `76761d6` (test, RED) → `e659ed8` (feat, GREEN)

_TDD gate compliance: each task has a `test(...)` commit preceding its `feat(...)` commit. No REFACTOR commits needed._

## Files Created/Modified
- `brave/config/settings.py` — added `class DashboardConfig(BaseSettings)` (BRAVE_DASHBOARD_BEARER_TOKEN, no alias).
- `brave/api/deps.py` — added `import hmac`, `from fastapi import Depends, Header, HTTPException, status`; `get_dashboard_config`, `require_bearer`, `require_steward_or_bearer`.
- `brave/api/routers/dlq.py` — imported `require_steward_or_bearer`; swapped it onto reprocess/validate/validate-batch/descarte routes.
- `brave/api/routers/atrativos_gate.py` — imported `require_steward_or_bearer`; swapped it onto approve/reject routes.
- `tests/integration/test_dashboard_endpoints.py` — NEW offline + integration auth tests.

## Decisions Made
- Placed `require_steward_or_bearer` in `deps.py` (co-located with the config getters) rather than duplicating it in each router, so both routers import one canonical guard.
- Left the now-unused local `require_steward` functions in both routers in place (ruff-clean, still referenced in their structural-template docstrings) to keep the diff scoped to the auth swap.

## Deviations from Plan
None - plan executed exactly as written.

## Threat Model Compliance
- **T-04-01 (Spoofing / require_bearer):** constant-time `hmac.compare_digest`, fail-closed on unset token, 401 before any DB work — verified by direct-callable tests + code-path assertion.
- **T-04-02 (EoP / either-or guard):** either-or still requires ONE valid secret; unset Bearer token does not grant Bearer-presented requests — verified by `test_either_or_bearer_unset_does_not_grant`. Steward path unchanged (no regression in `test_fastapi_endpoints.py`).
- **T-04-03 (Info disclosure):** secret never logged; `DashboardConfig` env_prefix-only (no alias) — asserted by `test_require_bearer_never_logs_secret`.
- **T-04-04 (Tampering / config):** fail-closed `Field(default="")` rejects all callers when unset — `test_dashboard_config_fail_closed_default` + `test_require_bearer_fail_closed_when_token_unset`.
- **T-04-SC:** no new package installs — confirmed (no dependency changes).

## Issues Encountered
- First Task 2 GREEN run raised `NameError: require_steward_or_bearer` from `atrativos_gate.py` — the import had been added to `dlq.py` but not the gate router. Added the import; both routers green. (Caught immediately by the offline suite at module import.)

## User Setup Required
**Environment variable for the dashboard operator token must be set in deploy/dev:**
- `BRAVE_DASHBOARD_BEARER_TOKEN` — the single operator Bearer token the dashboard BFF presents. **Fail-closed:** with it unset, all Bearer requests are rejected 401. The existing `BRAVE_STEWARD_SECRET` continues to work either-or.

## Next Phase Readiness
- The auth contract every later dashboard slice depends on now exists: `require_bearer` (read surface) + `require_steward_or_bearer` (mutations the BFF drives).
- Ready for Plan 04-02+: the `dashboard.py` read-aggregation router (monitor/cost/funnels/conversations/DLQ-detail) guarded by `require_bearer`, and the greenfield Next.js dashboard.

## Self-Check: PASSED

All 5 modified/created source files exist; all 4 task commits (04c8dd1, cc4110d, 76761d6, e659ed8) present in git history.

---
*Phase: 04-dashboard-territorial-cms*
*Completed: 2026-06-16*
