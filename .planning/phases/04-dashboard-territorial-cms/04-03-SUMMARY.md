---
phase: 04-dashboard-territorial-cms
plan: 03
subsystem: dashboard-backend
tags: [fastapi, read-aggregation, dlq, dash-01, d-01, bearer-auth]
requires:
  - "04-01: require_bearer Bearer dependency + DashboardConfig"
  - "brave/core/models.py: RioRecord.score_breakdown/normalized, NascenteRecord.payload, AuditLog"
provides:
  - "GET /api/v1/dlq/{rio_id} — full DLQ detail read endpoint (DASH-01 backend half)"
  - "brave/api/routers/dashboard.py — Phase 4 read-aggregation router (accretes monitor/cost/funnels later)"
affects:
  - "DLQ UI slice (plan 04) — now has a real contract to fetch + MSW-mock against"
tech-stack:
  added: []
  patterns:
    - "read-only Bearer-guarded GET (require_bearer dependency)"
    - "db.get + 404 idiom (copy-exact from dlq.py)"
    - "AuditLog windowed/ordered read for per-record event log"
key-files:
  created:
    - "brave/api/routers/dashboard.py"
  modified:
    - "brave/api/main.py"
    - "tests/integration/test_dashboard_endpoints.py"
decisions:
  - "D-01: dashboard reads go through a thin read-only FastAPI router; UI never touches the DB directly"
  - "signals extracted best-effort from RioRecord.normalized → NascenteRecord.payload 'signals' key, default {}"
  - "whatsapp_log = AuditLog rows for this rio_id ordered created_at ASC (oldest-first transcript order)"
metrics:
  duration: "~12min"
  completed: 2026-06-16
---

# Phase 04 Plan 03: DLQ Detail Endpoint (DASH-01 backend) Summary

A single Bearer-guarded read endpoint, `GET /api/v1/dlq/{rio_id}`, that returns the full DLQ detail the review panel needs — §7.6 per-criterion `score_breakdown` + Rio `normalized` + joined Nascente raw `payload` + extracted `signals` + the per-record WhatsApp/steward event log (`AuditLog`) — in one call, on a new read-only `dashboard.py` router. Built TDD, proven offline against the live docker-compose Postgres.

## What Was Built

- **`brave/api/routers/dashboard.py`** (NEW) — Phase 4 read-aggregation router. `get_dlq_detail` loads the `RioRecord` (`db.get`), 404s on unknown id (copy-exact dlq.py idiom), joins `NascenteRecord` for the raw payload, extracts `signals` from `normalized`/payload, and reads the `AuditLog` rows for this `rio_id` ordered `created_at` ascending. Guarded by `dependencies=[Depends(require_bearer)]` — the 401 fires before any DB work. Read-only: `db.get` + `select`, no writes, no pipeline logic.
- **`brave/api/main.py`** (MODIFIED) — imports and registers `dashboard_router` under a `# Phase 4` block. The route `/api/v1/dlq/{rio_id}` is now live.
- **`tests/integration/test_dashboard_endpoints.py`** (EXTENDED) — 5 new `dlq_detail` tests: missing-Bearer → 401 (pre-DB), unknown id → 404, full-shape (all 11 keys), §7.6 criteria present in `score_breakdown`, and whatsapp_log ordered + no cross-record leak. Added an `authed_client` fixture (valid Bearer header) and a `_make_dlq_record` seed helper.

## How It Was Verified

- `pytest tests/integration/test_dashboard_endpoints.py -k dlq_detail` → 5 passed (incl. 4 `@pytest.mark.integration` against live Postgres).
- Full `test_dashboard_endpoints.py` → 23 passed (no regression to the 04-01 auth tests).
- `python -c "from brave.api.main import app"` → route `/api/v1/dlq/{rio_id}` registered.
- `grep -n score_breakdown brave/api/routers/dashboard.py` → matches.
- `ruff check` → all checks passed.

## TDD Gate Compliance

- **RED** `73330d6` — `test(04-03)`: 5 failing dlq_detail tests (route returned 404 not 401, no endpoint).
- **GREEN** `389046a` — `feat(04-03)`: endpoint implementation; tests pass.
- **Wiring** `7d9bd6c` — `feat(04-03)`: router registration (Task 2).
- No REFACTOR commit needed.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] ruff lint fixes on touched files**
- **Found during:** Task 1 (after GREEN)
- **Issue:** ruff flagged import-ordering and `timezone.utc` → `datetime.UTC` (UP017) in the new test code.
- **Fix:** `ruff check --fix` applied; folded into the Task 1 commit. No behavior change.
- **Files modified:** tests/integration/test_dashboard_endpoints.py, brave/api/main.py (import order)
- **Commit:** 389046a / 7d9bd6c

All else executed exactly as written.

## Deferred Issues (out of scope)

- **Pre-existing test-isolation flake** in `tests/integration/test_atrativos_gate.py` (quality-rating-webhook + inbound tests): fail when run *after* `test_dashboard_endpoints.py` in the same session due to a module-scoped `client` fixture + the shared `get_redis()` fakeredis singleton leaking state across modules. **Each passes in isolation.** Not introduced by 04-03 (a read-only DLQ-detail endpoint that touches no Redis). Logged to `deferred-items.md`; owner = a future gate/test-harness fix.

## Threat Surface

Honored the plan `<threat_model>`:
- **T-04-09 (Spoofing):** `get_dlq_detail` guarded by `Depends(require_bearer)`; 401 before any DB work — proven by `test_dlq_detail_no_bearer_returns_401` (no DB fixture).
- **T-04-10 (Information Disclosure):** returns only Nascente payload + Rio normalized + score breakdown + audit log; no secrets/tokens; this destinos/atrativos pre-contact lane carries no phone PII in the DLQ detail (phone masking deferred to plan-07 conversation/gate reads). `_extract_signals` surfaces only the signals block.
- **T-04-11 (Tampering):** endpoint is read-only (`db.get` + `select`), no writes.
- No new security surface beyond the planned endpoint; no new packages (T-04-SC).

## Known Stubs

None — the endpoint is fully wired to real models (`RioRecord`, `NascenteRecord`, `AuditLog`) and returns live data.

## Self-Check: PASSED

All created/modified files exist; RED 73330d6, GREEN 389046a, wiring 7d9bd6c commits present.
