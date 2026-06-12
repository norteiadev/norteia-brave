---
phase: 02-destinos-lane
plan: "06"
subsystem: api
tags: [dlq, validate, fastapi, flag_modified, tdd]
dependency_graph:
  requires: [02-01, 02-02, 02-04]
  provides: [DEST-05, validate-endpoint, batch-validate-endpoint]
  affects: [dlq.py, test_destinos_lane.py]
tech_stack:
  added: []
  patterns:
    - flag_modified + normalized dict reassignment for SQLAlchemy JSONB column mutation
    - reprocess_record (not process_nascente_record) for DLQ re-score path
    - Celery dispatch-or-sync-fallback (push_destination_task.delay with promote_to_mar fallback)
key_files:
  created:
    - tests/integration/test_destinos_lane.py
  modified:
    - brave/api/routers/dlq.py
decisions:
  - "D-07: PATCH validate sets validacao_humana_value=100 in normalized, re-scores via reprocess_record, dispatches push_destination_task when routing=='mar'"
  - "D-08: POST validate-batch applies single-record logic to all DLQ records per UF; uf required, limit 1-1000"
  - "Batch endpoint writes per-record audit rows (not a single batch summary row) to preserve individual steward audit trail"
metrics:
  duration: 13m
  completed: "2026-06-12"
  tasks: 1
  files: 2
requirements: [DEST-05]
---

# Phase 02 Plan 06: DLQ Validate Endpoints Summary

DLQ steward-validate endpoints (D-07, D-08) added to `dlq.py`: PATCH validate sets `validacao_humana_value=100` via `flag_modified`, re-scores via `reprocess_record`, and dispatches `push_destination_task` when routing reaches "mar".

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | TDD failing tests for validate + validate-batch | 92cc120 | tests/integration/test_destinos_lane.py |
| 1 (GREEN) | validate_dlq_record + validate_batch endpoints | 7f4a926 | brave/api/routers/dlq.py |

## Verification Results

All success criteria met:

- PATCH `/api/v1/dlq/{rio_id}/validate` is registered as a FastAPI route
- POST `/api/v1/dlq/validate-batch` is registered as a FastAPI route
- Both endpoints use `flag_modified` + `reprocess_record` (verified by grep — 2 matches)
- validate endpoint conditionally dispatches `push_destination_task` only when `routing=="mar"`
- Both endpoints write audit rows with `action="dlq_validated"`, `actor="steward"`
- No existing DLQ endpoints (reprocess/descarte/list) modified
- DB round-trip test passes: `normalized["validacao_humana_value"] == 100.0` re-read from DB after PATCH
- Existing FastAPI endpoint tests: 9/9 pass

Test results:
```
tests/integration/test_destinos_lane.py     9 passed
tests/integration/test_fastapi_endpoints.py 9 passed
```

## Deviations from Plan

None — plan executed exactly as written.

## TDD Gate Compliance

- RED gate: commit 92cc120 — `test(02-06): add failing tests for validate_dlq_record + validate_batch endpoints`
- GREEN gate: commit 7f4a926 — `feat(02-06): add validate_dlq_record + validate_batch endpoints to dlq.py`

Both gates present in git log in correct RED → GREEN order.

## Known Stubs

None — both endpoints are fully implemented with real logic.

## Threat Flags

All threats are addressed per the plan's threat register:

| Threat | Mitigation Applied |
|--------|--------------------|
| T-02-06-01: No authz on PATCH/validate | Accepted — internal endpoint; Phase 4 adds Bearer auth |
| T-02-06-02: validate-batch wildcard abuse | `uf=Query(...)` required parameter (no wildcard) |
| T-02-06-03: DoS via large limit | `limit=Query(100, ge=1, le=1000)` enforced at FastAPI layer |
| T-02-06-04: flag_modified omission | `flag_modified` called in both endpoints; DB round-trip test proves persistence |
| T-02-06-05: audit repudiation | `write_audit(actor="steward")` in both endpoints per record |

## Self-Check: PASSED

All files exist and commits are present in git log.
