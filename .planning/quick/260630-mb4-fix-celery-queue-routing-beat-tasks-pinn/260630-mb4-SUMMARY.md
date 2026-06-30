---
phase: quick-260630-mb4
plan: "01"
subsystem: celery-queue-routing
tags: [celery, beat, queue-routing, docker-compose, regression-test, conventions]
dependency_graph:
  requires: []
  provides:
    - brave/tasks/beat_schedule.py (no queue pins — beat dispatches to default celery queue)
    - brave/tasks/celery_app.py (task_default_queue=celery explicit)
    - docker-compose.yml (worker + beat service stubs)
    - tests/unit/test_celery_queue_routing.py (regression guard)
    - CLAUDE.md (single-queue model documented in Conventions)
  affects:
    - Celery beat dispatch routing (all 3 beat entry types)
    - Worker queue consumption model
tech_stack:
  added: []
  patterns:
    - Single-queue Celery model — all tasks (beat + .delay) on default celery queue; options.queue pin removed from beat schedule
key_files:
  created:
    - tests/unit/test_celery_queue_routing.py
  modified:
    - brave/tasks/beat_schedule.py
    - brave/tasks/celery_app.py
    - docker-compose.yml
    - CLAUDE.md
decisions:
  - "Solution A (single queue) locked by operator — no task_routes, no -Q flag, all tasks on default celery queue"
  - "No Dockerfile exists so docker-compose worker/beat services use image: python:3.12-slim placeholder with TODO comment"
  - "Pre-existing test_desmembramento.py failures (4 tests) confirmed out-of-scope; not caused by this changeset"
metrics:
  duration: "6m"
  completed: "2026-06-30"
  tasks_completed: 2
  files_changed: 5
---

# Phase quick-260630-mb4 Plan 01: Fix Celery Queue Routing (brave.sweep beat pins) Summary

**One-liner:** Removed `brave.sweep` queue pins from all 3 BRAVE_BEAT_SCHEDULE entry types so beat-dispatched tasks land on the default `celery` queue consumed by the worker.

## What Changed

### Task 1: Remove brave.sweep queue pins and wire docker-compose worker+beat services

**brave/tasks/beat_schedule.py** (commit `adfc066`):
- Removed `"options": {"queue": "brave.sweep"}` from all 3 entry types:
  - `sweep-{uf}-daily` loop (27 entries)
  - `sweep-atrativos-{uf}-daily` loop (27 entries)
  - `ta-keepalive` entry (1 entry)
- Added module-level comment documenting the single-queue model and deferred dedicated-lane rationale

**brave/tasks/celery_app.py** (commit `adfc066`):
- Added `task_default_queue="celery"` to `app.conf.update()` with explanatory comment
- No existing conf keys changed; no task_routes added

**docker-compose.yml** (commit `adfc066`):
- Added `worker` service: `celery -A brave.tasks.celery_app:app worker --loglevel=info`, no -Q flag, depends_on postgres+redis (service_healthy), env defaults use container-network hostnames (`postgres:5432`, `redis:6379`), `RUN_REAL_EXTERNALS: "0"`, `BRAVE_USE_FAKEREDIS` NOT set
- Added `beat` service: `celery -A brave.tasks.beat_schedule beat --loglevel=info`, depends_on redis (service_healthy), same env pattern, `BRAVE_USE_FAKEREDIS` NOT set, "only one beat instance" comment
- Both services use `image: python:3.12-slim` placeholder with `TODO: add build: .` comment (no Dockerfile in repo root)

### Task 2: Regression test + CLAUDE.md Conventions update

**tests/unit/test_celery_queue_routing.py** (commit `e4c770c`):
- `test_no_beat_entry_pins_unconsumed_queue`: imports BRAVE_BEAT_SCHEDULE, asserts no entry's `options.queue` is set to a non-default value
- `test_celery_app_default_queue_is_celery`: imports `app`, asserts `app.conf.task_default_queue == "celery"`
- Offline (pure import + attribute checks), no Redis, no Postgres, no markers needed

**CLAUDE.md** (commit `e4c770c`):
- Replaced Conventions placeholder with "Celery — Single-Queue Model (established 260630-mb4)" section documenting: queue model rationale, worker/beat start commands, redbeat restart note, deferred dedicated-lanes note

## Verification Results

```
# 1. No brave.sweep queue pins remain
grep '"queue"' brave/tasks/beat_schedule.py  → PASS: zero matches

# 2. Explicit default queue set
grep task_default_queue brave/tasks/celery_app.py
→ task_default_queue="celery",  # Explicit default — matches worker start cmd and beat dispatch

# 3. Regression tests
pytest tests/unit/test_celery_queue_routing.py -v → 2 passed, 0 failed

# 4. docker-compose services present
grep -E "^\s+(worker|beat):" docker-compose.yml → both present

# 5. Full unit suite (excl. pre-existing test_desmembramento.py failures)
pytest tests/unit/ --ignore=tests/unit/test_desmembramento.py → 661 passed, 0 failed
```

## Commits

| Task | Commit | Message |
|------|--------|---------|
| Task 1 | `adfc066` | fix(quick-260630-mb4): remove brave.sweep queue pins and add worker+beat services |
| Task 2 | `e4c770c` | test(quick-260630-mb4): regression guard for queue routing + conventions doc |

## Deviations from Plan

### Pre-existing failures — out of scope

**4 tests in `tests/unit/test_desmembramento.py` fail** (pre-existing, unrelated to this fix):
- `test_desmembramento_agent_happy_path`
- `test_desmembramento_offline_null_llm_does_not_crash`
- `test_desmembramento_agent_malformed_output_quarantined`
- `test_desmembramento_agent_empty_destinos_skips`

These tests import `brave.lanes.destinos.desmembramento` — nothing our changeset touched. Confirmed pre-existing by checking that our diff covers only `beat_schedule.py`, `celery_app.py`, and `docker-compose.yml`. Logged to `deferred-items.md`.

### No Dockerfile — docker-compose services use placeholder image

**[Rule 2 - Missing functionality]** No `Dockerfile` exists in the project root. Per plan instructions ("if no Dockerfile exists, add a TODO: comment"), both worker and beat services use `image: python:3.12-slim` as a placeholder with a `# TODO: add build: .` comment instructing the operator to run via `.venv/bin/celery` in the meantime. This is the correct behavior per plan spec.

## Known Stubs

The docker-compose worker/beat services use `image: python:3.12-slim` which will not actually run the brave application without a Dockerfile or a pre-built image. The services are structural stubs only — operators must run worker/beat via `.venv/bin/celery` until a Dockerfile is added.

## Threat Flags

None. This fix only removes routing keys from beat schedule entries and adds an explicit default queue setting. No new network endpoints, auth paths, file access patterns, or schema changes introduced.

## Self-Check: PASSED

- [x] `brave/tasks/beat_schedule.py` — modified, committed in adfc066
- [x] `brave/tasks/celery_app.py` — modified, committed in adfc066
- [x] `docker-compose.yml` — modified, committed in adfc066
- [x] `tests/unit/test_celery_queue_routing.py` — created, committed in e4c770c
- [x] `CLAUDE.md` — modified, committed in e4c770c
- [x] Commit adfc066 confirmed: `git log --oneline | grep adfc066`
- [x] Commit e4c770c confirmed: `git log --oneline | grep e4c770c`
- [x] 2 new tests pass: `pytest tests/unit/test_celery_queue_routing.py` → 2 passed
- [x] Full unit suite: 661 passed, 0 failed (excl. 4 pre-existing desmembramento failures)
