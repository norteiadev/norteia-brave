---
phase: 02-destinos-lane
plan: "04"
subsystem: celery-tasks
tags: [celery, push, mar, destination, tdd, D-09]
dependency_graph:
  requires: [02-01]
  provides: [push_destination_task]
  affects: [brave/tasks/pipeline.py, brave/api/routers/dlq.py (Plan 06 caller)]
tech_stack:
  added: []
  patterns: [celery-shared-task, promote-to-mar, null-norteia-api, push-destination-only]
key_files:
  created:
    - tests/integration/test_push_destination_task.py
  modified:
    - brave/tasks/pipeline.py
decisions:
  - "push_destination_task always calls push_destination (never push_attraction) — destination-specific per D-09; mirrors push_mar with entity_type-conditional dispatch removed"
  - "Docstring mentions 'never push_attraction' which triggered false positive in source-inspection test; test fixed to strip docstrings before checking code body"
metrics:
  duration: "~8 minutes"
  completed: "2026-06-12T14:57:39Z"
  tasks_completed: 1
  files_changed: 2
requirements: [DEST-05]
---

# Phase 2 Plan 4: push_destination_task Summary

Idempotent Celery task `push_destination_task` added to `brave/tasks/pipeline.py` that promotes a Mar-routed destino from DLQ via `promote_to_mar` and pushes it to norteia-api using `push_destination` exclusively (never `push_attraction`), per decision D-09.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Add failing tests for push_destination_task | 52a8e1f | tests/integration/test_push_destination_task.py |
| 1 (GREEN) | Implement push_destination_task in pipeline.py | 9ed4804 | brave/tasks/pipeline.py, tests/integration/test_push_destination_task.py |

## What Was Built

`push_destination_task` is a Celery task registered as `"brave.push_destination"` that:

1. Loads the `RioRecord` by UUID — raises `PermanentError` if not found (no retry)
2. Returns immediately (no-op) if `rio.routing != "mar"` — idempotency guard per D-09
3. Calls `promote_to_mar(session, rio)` to create/update the `MarRecord`
4. Selects `NullNorteiaApiClient` (offline) or `NorteiaApiClient` (real) based on `AppConfig.run_real_externals`
5. Builds the flat-provenance Pact payload via `_build_push_payload`
6. Always calls `api_client.push_destination(payload)` — never `push_attraction`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test source-inspection false positive on docstring**
- **Found during:** GREEN phase test run
- **Issue:** `test_push_destination_task_always_calls_push_destination` checked `push_attraction not in task_source`, but the docstring mentions "never push_attraction" — causing an assertion failure
- **Fix:** Updated test to strip docstrings before checking code body; only asserts on executable code lines
- **Files modified:** tests/integration/test_push_destination_task.py
- **Commit:** 9ed4804 (included in GREEN commit)

No other deviations. Plan executed as written.

## TDD Gate Compliance

- RED gate: commit `52a8e1f` — `test(02-04): add failing tests for push_destination_task`
- GREEN gate: commit `9ed4804` — `feat(02-04): add push_destination_task to pipeline.py (D-09)`
- REFACTOR gate: not needed — code is clean and follows push_mar pattern exactly

## Verification

```
uv run python -c "from brave.tasks.pipeline import push_destination_task; print(push_destination_task.name)"
# → brave.push_destination

uv run pytest tests/integration/test_push_destination_task.py tests/integration/test_celery_tasks.py -v
# → 9 passed
```

## Self-Check: PASSED

- [x] `brave/tasks/pipeline.py` modified — push_destination_task appended
- [x] `tests/integration/test_push_destination_task.py` created — 6 tests
- [x] Commit `52a8e1f` exists (RED: failing tests)
- [x] Commit `9ed4804` exists (GREEN: implementation)
- [x] `push_destination_task.name == "brave.push_destination"` verified
- [x] `push_mar` and `reprocess_record_task` unchanged
- [x] 9/9 tests pass
