---
phase: 05-auto-discovery-orchestration
plan: 03
subsystem: orchestration
tags: [cli, ops-trigger, fastapi, bearer-auth, sweep, celery-or-inline, offline-tests]

# Dependency graph
requires:
  - phase: 05-auto-discovery-orchestration
    plan: 01
    provides: "brave.sweep_uf(uf) recurring Destinos producer (the destinos lane the CLI/endpoint dispatch)"
  - phase: 05-auto-discovery-orchestration
    plan: 02
    provides: "discover_atrativo_task(uf) auto-chaining to the gate (the atrativos lane the CLI/endpoint dispatch)"
  - phase: 03-atrativos-lane-whatsapp-compliance
    provides: "require_steward_or_bearer dep; dispatch-then-inline fallback patterns (dlq.py swallow-all, atrativos_gate.py prod-vs-offline)"
  - phase: 04-dashboard
    provides: "DashboardConfig bearer_token + require_steward_or_bearer either-or auth used by the endpoint"
provides:
  - "brave.cli sweep <UF> [--lane destinos|atrativos|both] — on-demand ops trigger with Celery-or-inline fallback (ORCH-03)"
  - "POST /api/v1/sweep — optional Bearer-guarded HTTP ops trigger (ORCH-03/04, D-05)"
  - "_run_sweep(uf, lane) CLI helper + _dispatch(task, uf) endpoint helper reusing the established dispatch-then-inline fallback"
affects: [auto-discovery-orchestration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Ops trigger = thin surface over existing producer/chain tasks; dispatches sweep_uf and/or discover_atrativo_task, never adds a scoring/validation/outreach branch (D-02/D-07)"
    - "CLI uses the swallow-all inline fallback (operator convenience, dlq.py variant); the HTTP endpoint uses the prod-vs-offline variant (atrativos_gate.py) so a broker-down fan-out surfaces a 503 instead of silently dropping"
    - "Endpoint takes uf/lane as query params with lane typed Literal[destinos|atrativos|both] → FastAPI returns 422 on an unknown lane for free"
    - "Endpoint needs no request DB session — the dispatched tasks open their own _get_session(), keeping the 401/202 tests DB-free and offline"

key-files:
  created:
    - brave/api/routers/sweep.py
    - tests/unit/test_cli_sweep.py
    - tests/integration/test_sweep_endpoint.py
  modified:
    - brave/cli.py
    - brave/api/main.py

key-decisions:
  - "CLI is the required surface (ORCH-03); the HTTP endpoint is the optional D-05 nice-to-have — both implemented this plan"
  - "Endpoint guarded by require_steward_or_bearer (fail-closed, constant-time) — an unauthenticated caller gets 401 before any fan-out (T-05-07)"
  - "Endpoint uses the prod-vs-offline dispatch split (swallow+inline only when run_real_externals is False, else 503) so a real broker-down sweep is not silently lost"
  - "Endpoint exposes no DB dependency (tasks self-session) → the 401/202 integration tests run fully offline/keyless with .delay spied, no live DB"
  - "CLI inline path mirrors run-fixture's BRAVE_DB_URL-unset graceful-degrade message instead of crashing with an opaque _get_session error"

# Requirements
requirements: [ORCH-03, ORCH-04]

# Metrics
metrics:
  duration: "~25 min"
  completed: 2026-06-17
  tasks: 2
  files_changed: 5
  tests_added: 16
---

# Phase 5 Plan 03: Ops Trigger (CLI sweep + optional endpoint) Summary

On-demand ops trigger for UF sweeps: a `brave.cli sweep <UF> [--lane destinos|atrativos|both]` subcommand (required) plus an optional Bearer-guarded `POST /api/v1/sweep` endpoint, both reusing the established dispatch-Celery-then-run-inline fallback so an operator can kick the same automation the beat drives — with or without a worker — without touching the §7.6 gate or the WhatsApp send path.

## What Was Built

### Task 1 — `brave.cli sweep` subcommand (ORCH-03, D-05)
- Added `_run_sweep(uf, lane)`, `_parse_lane(args)`, and a `sweep` branch in `main()` to `brave/cli.py`.
- `--lane destinos` → `sweep_uf`; `--lane atrativos` → `discover_atrativo_task`; default `both` → both.
- Dispatch-then-inline fallback (swallow-all, mirroring `dlq.py:104-114`): `.delay(uf)` enqueues on Celery; on broker failure it falls back to `.run(uf)` synchronously.
- The UF is uppercased; an unknown `--lane` or a missing UF exits non-zero with a usage hint.
- The inline path mirrors `run-fixture`'s `BRAVE_DB_URL`-unset graceful-degrade message so an operator with no DB URL gets a clear hint instead of a stack trace.

### Task 2 — Bearer-guarded `POST /api/v1/sweep` endpoint (ORCH-03/04, D-05, security)
- Created `brave/api/routers/sweep.py` with `dependencies=[Depends(require_steward_or_bearer)]`; registered in `brave/api/main.py`.
- `uf` + `lane` query params; `lane` typed `Literal["destinos","atrativos","both"]` → 422 on an unknown lane; `uf` uppercased; returns `{"status":"accepted","uf":...,"lane":...}` with `status_code=202`.
- Dispatch uses the prod-vs-offline variant (`atrativos_gate.py:376-396`): swallow + inline `.run` only when `run_real_externals` is False; in a real environment a broker-down dispatch raises a 503 instead of silently dropping the fan-out.
- The endpoint only kicks `sweep_uf` / `discover_atrativo_task` — it never dispatches `outreach_task`, never auto-validates, and never bypasses §7.6 (D-02/D-07).

## Tests (16, all offline / keyless)
- `tests/unit/test_cli_sweep.py` (8): dispatch-both, uppercase, lane filters, inline fallback (delay raises → `.run` fires), BRAVE_DB_URL-unset graceful degrade, unknown lane / missing UF non-zero exit. Pure-unit — both `.delay` and `.run` spied (no broker, no DB).
- `tests/integration/test_sweep_endpoint.py` (8): 401 without/with-invalid Bearer (nothing dispatched), 202 + lane routing with a valid Bearer, uf uppercased, unknown lane → 422, and `outreach_task` never dispatched. `.delay` spied so no broker/DB is touched.

## Deviations from Plan

None — plan executed as written. The endpoint was implemented (the plan's optional D-05 nice-to-have) per the orchestrator's explicit instruction. One planner-discretion choice exercised: the endpoint takes `uf`/`lane` as **query params** with `lane` a `Literal` (so FastAPI yields a 422 on an invalid lane), and carries **no DB dependency** (the dispatched tasks self-session), which keeps the 401/202 tests DB-free and fully offline.

## Threat Model Compliance
- **T-05-07** (unauthenticated fan-out): endpoint requires `require_steward_or_bearer` (fail-closed, constant-time). Tests assert 401 without/with-invalid Bearer and that nothing is dispatched.
- **T-05-08** (cost amplification): no new cost path — the trigger dispatches the same idempotent producer/chain tasks; the existing cost guard (OBS-02) still caps spend.
- **T-05-09** (gate/send bypass): CLI + endpoint dispatch only `sweep_uf` and the auto-chain that STOPS at `aguardando_consulta_whatsapp`; the endpoint test asserts `outreach_task.delay` is never called.
- **T-05-SC** (supply chain): no new packages added — FastAPI + deps already vendored.

## Self-Check: PASSED
- Files created/modified verified on disk (see below).
- All 4 task commits present in git log.
