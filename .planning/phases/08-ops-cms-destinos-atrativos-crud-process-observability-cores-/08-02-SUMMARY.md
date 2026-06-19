---
phase: 08-ops-cms-destinos-atrativos-crud-process-observability-cores
plan: "02"
subsystem: brave/api/routers
tags: [observability, workers, failures, celery, redis, bearer-auth]
dependency_graph:
  requires: []
  provides:
    - brave/api/routers/workers.py (GET /api/v1/workers, GET /api/v1/failures)
  affects:
    - brave/api/main.py (registration happens in plan 08-04)
tech_stack:
  added: []
  patterns:
    - Lazy celery_app import inside handler body to avoid import-time broker connection
    - inspect(timeout=1.0) + try/except wraps entire block for graceful broker-absent
    - None→{} coercion on ping/active/reserved inspect results
    - Separate Redis LLEN try/except returning null values on error
    - PoisonQuarantine list endpoint omits payload column (T-08-08)
key_files:
  created:
    - brave/api/routers/workers.py
  modified: []
decisions:
  - "D-05: GET /api/v1/workers lazy-imports celery_app to prevent import-time broker connection (Pitfall 1)"
  - "D-05: inspect(timeout=1.0) + try/except — broker absence returns 200 with broker_reachable=false, never 500 (T-08-07)"
  - "D-05: PoisonQuarantine.payload NOT serialized in /failures list — only task_name+error_message(500 char truncation)+quarantined_at (T-08-08)"
metrics:
  duration: ~4 minutes
  completed: "2026-06-19"
  tasks_completed: 2
  files_modified: 1
---

# Phase 08 Plan 02: workers.py Process Observability Summary

**One-liner:** Workers router with Celery inspect+Redis LLEN graceful-degradation and PoisonQuarantine list, both Bearer-guarded.

## What Was Built

Created `brave/api/routers/workers.py` with two process observability endpoints:

**GET /api/v1/workers** — Operator visibility into Celery worker health and Redis queue depths:
- Lazy-imports `celery_app` inside handler body (no import-time broker connection)
- `inspect(timeout=1.0)` with entire block in `try/except`; `None` returns coerced to `{}`
- `broker_reachable = bool(ping)` — always `False` when broker absent
- Per-worker dict: hostname, status (up/down from pong response), active_count, reserved_count
- Redis `LLEN("brave.sweep")` + `LLEN("celery")` in separate `try/except`; returns `null` on Redis error
- Static `beat_schedule: {entries: 54, queues: ["brave.sweep"]}` summary

**GET /api/v1/failures** — PoisonQuarantine list with aggregated counts:
- `Query(50, ge=1, le=200)` limit parameter
- `select(PoisonQuarantine).order_by(quarantined_at.desc()).limit(limit)`
- `by_task` dict counts per task_name
- Items: id, task_name, error_message (truncated at 500 chars), quarantined_at (ISO format)
- `payload` column intentionally excluded (T-08-08: can be large, contains pipeline internals)

Both endpoints use `dependencies=[Depends(require_bearer)]` (T-08-06).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | GET /api/v1/workers — Celery inspect + Redis LLEN + graceful broker-absent | a3faed9 | brave/api/routers/workers.py |
| 2 | GET /api/v1/failures — PoisonQuarantine list+counts | a3faed9 | brave/api/routers/workers.py |

## Deviations from Plan

None — plan executed exactly as written.

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|------------|
| T-08-06 | Both endpoints have `dependencies=[Depends(require_bearer)]` — 401 before any DB/broker work |
| T-08-07 | `inspect(timeout=1.0)` + `try/except` wraps entire inspect block — broker hang returns 200 with broker_reachable=false |
| T-08-08 | `PoisonQuarantine.payload` NOT included in list serialization — only task_name + error_message (truncated 500 chars) + quarantined_at |

## Known Stubs

None — all functionality fully implemented.

## Threat Flags

None — no new trust boundaries introduced. The router reads from existing Celery inspect API and PoisonQuarantine table. No new network endpoints, auth paths, or schema changes.

## Self-Check: PASSED

- [x] `brave/api/routers/workers.py` exists at correct worktree path
- [x] Commit `a3faed9` exists: `feat(08-02): workers.py process observability — GET /api/v1/workers + GET /api/v1/failures`
- [x] `GET /api/v1/workers` and `GET /api/v1/failures` routes verified via import check
- [x] `timeout=1.0` confirmed in inspect call
- [x] `or {}` coercions on ping/active/reserved confirmed
- [x] Two `try:` blocks (inspect + Redis LLEN) confirmed
- [x] `payload` not in serialization (only in docstrings/comments) confirmed
