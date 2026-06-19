---
phase: 08-ops-cms-destinos-atrativos-crud-process-observability-cores
plan: "07"
subsystem: testing
tags: [pytest, offline-suite, cms, workers, pii-masking, auth, observability]
dependency_graph:
  requires: [08-01, 08-02, 08-04]
  provides: [D-07-test-coverage]
  affects: []
tech_stack:
  added: []
  patterns:
    - autouse fixture for env-var pinning across multi-module test runs
    - app.dependency_overrides for Redis (get_redis) in workers tests
    - monkeypatch for Celery inspect (lazy import path brave.tasks.celery_app.app.control.inspect)
    - MagicMock with side_effect=ConnectionError for llen-failure simulation
key_files:
  created:
    - tests/test_cms_endpoints.py
    - tests/test_workers_endpoints.py
  modified: []
decisions:
  - "autouse _pin_test_secrets fixture re-pins bearer/steward tokens before each test to handle pytest multi-module env-var collision"
  - "inject MagicMock Redis with llen.side_effect=ConnectionError instead of raising in get_redis itself (FastAPI DI raises 500 if dep itself raises)"
  - "filter list endpoints with rare UF codes (AM, AC, AP) to avoid pagination gaps when DB has many pre-existing records from other tests"
metrics:
  duration: "8min"
  completed_date: "2026-06-18"
---

# Phase 08 Plan 07: Offline pytest suite for Phase 8 backend endpoints Summary

Offline pytest suite (29 tests) covering all Phase 8 backend endpoints — Bearer auth enforcement, response shapes, PII masking, FSM advance conflict, and workers graceful broker-absent degradation. Closes D-07 mandate: 100% offline, CI keyless, no real Celery/Places/LLM/Redis.

## What Was Built

### tests/test_cms_endpoints.py (20 tests)

**Auth tests (no DB, T-08-01/21):**
- `test_list_destinos_bearer_required` — 401 without Bearer
- `test_get_destino_detail_bearer_required` — 401 without Bearer
- `test_promote_destino_bearer_required` — 401 without Bearer
- `test_descarte_destino_bearer_required` — 401 without Bearer
- `test_list_atrativos_bearer_required` — 401 without Bearer
- `test_get_atrativo_detail_bearer_required` — 401 without Bearer
- `test_advance_atrativo_bearer_required` — 401 without Bearer
- `test_descarte_atrativo_bearer_required` — 401 without Bearer

**Integration tests (require Postgres):**
- `test_list_destinos_with_bearer` — 200, {items,total,offset,limit} shape + item keys
- `test_list_destinos_filter_uf` — UF filter: AC records visible, TO records excluded
- `test_get_destino_detail_404` — 404 on unknown UUID
- `test_get_destino_detail` — score_breakdown (dict) + audit_log (list) + normalized present
- `test_promote_destino_steward` — 202 {status, routing, rio_id}
- `test_descarte_destino` — 200 + routing==descarte verified in DB
- `test_list_atrativos_with_bearer` — 200, sub_state key in items
- `test_list_atrativos_pii_masked` — phone_e164 NOT in response; phone_masked present (T-08-04)
- `test_get_atrativo_detail_contacts_masked` — contacts.phone_e164 absent, contacts.phone_masked present
- `test_advance_atrativo_conflict` — 409 when expected_state != actual sub_state
- `test_advance_atrativo_success` — 200 + sub_state advanced to contacts_found
- `test_descarte_atrativo` — 200 + routing==dlq

### tests/test_workers_endpoints.py (9 tests)

**Auth tests (no DB):**
- `test_workers_bearer_required` — 401 without Bearer
- `test_failures_bearer_required` — 401 without Bearer

**Workers + Redis (monkeypatch, 100% offline):**
- `test_workers_broker_down` — 200, broker_reachable=False, workers=[]
- `test_workers_broker_down_redis_llen_fails` — queues.brave.sweep=None when llen() raises
- `test_workers_broker_up` — broker_reachable=True, workers[0].status==up
- `test_workers_response_shape` — all 4 required keys + beat_schedule.entries==54

**Failures (integration):**
- `test_failures_empty` — {total,by_task,items} structure present
- `test_failures_with_data` — task_name present, error_message present
- `test_failures_payload_not_exposed` — "payload" key never in items (T-08-08)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Token collision across test modules**
- **Found during:** Task 1 verification
- **Issue:** Multiple test modules (test_atrativos_gate.py etc.) use `os.environ["BRAVE_DASHBOARD_BEARER_TOKEN"] = ...` at module load time; last-loaded wins and all prior modules' auth tests fail with 401.
- **Fix:** Switched from module-load-time env set to autouse function-scoped `_pin_test_secrets` fixture that re-pins token before every test. Token values made unique per module to avoid collisions.
- **Files modified:** tests/test_cms_endpoints.py, tests/test_workers_endpoints.py
- **Commit:** 02a3894

**2. [Rule 1 - Bug] FastAPI DI raises 500 when get_redis itself raises**
- **Found during:** Task 2 verification
- **Issue:** Plan described `monkeypatch get_redis to raise Exception` for the Redis-down scenario. When a FastAPI Depends() function raises during DI resolution, FastAPI returns 500 before the handler body runs — the handler's try/except never fires.
- **Fix:** Instead of overriding `get_redis` with a function that raises, inject a `MagicMock()` whose `.llen.side_effect = ConnectionError(...)`. The dependency resolves successfully, the handler receives the mock, then `llen()` raises inside the handler's try/except which returns `queues={...: None}` as designed. Renamed test to `test_workers_broker_down_redis_llen_fails` to describe actual scenario.
- **Files modified:** tests/test_workers_endpoints.py
- **Commit:** a003327

**3. [Rule 1 - Bug] List endpoint pagination miss**
- **Found during:** Task 1 verification
- **Issue:** `test_list_destinos_with_bearer` used default limit=50 against a DB with many pre-existing records from other integration tests; newly-created record fell past page 1.
- **Fix:** Added `?uf=AM&limit=500` filter using a rare state code (AM=Amazonas) so test creates a record that appears in the filtered result set. Same pattern applied to atrativos list test (uf=AP).
- **Files modified:** tests/test_cms_endpoints.py
- **Commit:** 02a3894

## Threat Surface Scan

Tests verify threat mitigations — no new network surface introduced:
- T-08-04 (phone_e164 PII): `test_list_atrativos_pii_masked` + `test_get_atrativo_detail_contacts_masked` assert `phone_e164` never in response body
- T-08-08 (payload exposure): `test_failures_payload_not_exposed` asserts `payload` key never in /failures items
- T-08-21 (Bearer 401): 8 auth tests across all 5 new endpoints

## Self-Check: PASSED

- FOUND: tests/test_cms_endpoints.py (467 lines, 20 tests)
- FOUND: tests/test_workers_endpoints.py (326 lines, 9 tests)
- FOUND: 08-07-SUMMARY.md
- FOUND: commit 02a3894 (test_cms_endpoints.py)
- FOUND: commit a003327 (test_workers_endpoints.py)
- Full suite: 435 passed, 1 skipped, 0 failed, 0 errors
