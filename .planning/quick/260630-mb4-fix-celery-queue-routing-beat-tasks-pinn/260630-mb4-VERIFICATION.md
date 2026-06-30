---
phase: quick-260630-mb4
verified: 2026-06-30
status: passed
score: all must-haves verified
---

# Quick 260630-mb4 — unify Celery queue routing — Verification

## The bug (confirmed)
Beat entries pinned `options={"queue":"brave.sweep"}` while every `.delay()` (API
`engine_sweep_run` + all fan-out) used the default `celery` queue. The documented worker
start (`celery ... worker`, no -Q) consumes only `celery` → beat-scheduled tasks (daily
sweeps, ta-keepalive) were silently dropped. No task declared a queue; no task_routes existed.

## Fix verified against merged code
- `brave/tasks/beat_schedule.py`: `grep -c '"queue"'` → **0** — all 55 pins removed (27 sweep-{uf},
  27 sweep-atrativos-{uf}, 1 ta-keepalive). Beat now dispatches to the default queue.
- `brave/tasks/celery_app.py:55`: `task_default_queue="celery"` added (explicit default, matches
  worker start + beat dispatch; prevents drift). No task_routes (single-queue model).
- `docker-compose.yml`: `worker` service (`celery -A brave.tasks.celery_app:app worker`, no -Q,
  depends_on postgres+redis) and `beat` service (`celery -A brave.tasks.beat_schedule beat`,
  depends_on redis) added as stubs (image placeholder + TODO build: . — no Dockerfile yet); env
  defaults use container-network hostnames (postgres:5432 / redis:6379), `BRAVE_USE_FAKEREDIS` unset.
- `tests/unit/test_celery_queue_routing.py`: 2 tests — no beat entry pins a non-default queue;
  `task_default_queue == "celery"`. **Pass.**
- `CLAUDE.md`: single-queue model documented + worker/beat start commands + redbeat-restart note +
  deferred dedicated-lanes trigger.

## Suite
`BRAVE_USE_FAKEREDIS=1` (RUN_REAL_EXTERNALS unset): **666 passed, 0 failed.**
(4 transient test_desmembramento failures during a mid-run were DB-state pollution from the earlier
260630-ks0 live sweep — 766 leftover nascente rows; gone after a `reset-brave-db` data wipe. Not
related to this change.)

## Outcome
ONE worker (`celery -A brave.tasks.celery_app:app worker`, no -Q) now runs beat tasks + API sweeps +
fan-out — the silent beat-drop (incl. the SPIKE-1 ta-keepalive false-negative cause) is fixed.
Dedicated `brave.sweep` lanes (task_routes + 2 pools) deferred as a documented scale-up. PASSED.
