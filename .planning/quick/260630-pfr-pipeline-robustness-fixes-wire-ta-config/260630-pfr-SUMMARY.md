---
phase: quick
plan: 260630-pfr
subsystem: pipeline-robustness
tags: [savepoint, wr-01, celery, ta-config, dlq, reset-db]
dependency_graph:
  requires: []
  provides: [pipeline-savepoint-isolation, dlq-wr01-commit, ta-config-wired, broker-purge]
  affects: [sweep_uf, dlq-validate, reset-brave-db-skill]
tech_stack:
  added: []
  patterns: [SQLAlchemy SAVEPOINT (begin_nested), WR-01 commit-before-dispatch, Celery broker key scoping]
key_files:
  created: []
  modified:
    - brave/tasks/pipeline.py
    - brave/lanes/destinos/mtur.py
    - brave/lanes/destinos/desmembramento.py
    - brave/api/routers/dlq.py
    - .claude/skills/reset-brave-db/scripts/reset_db.py
    - .claude/skills/reset-brave-db/SKILL.md
    - tests/unit/tasks/test_sweep_tripadvisor.py
    - tests/unit/test_desmembramento.py
    - tests/integration/test_sweep_uf.py
    - tests/integration/test_destinos_lane.py
decisions:
  - SAVEPOINT per record in MturSeedIngest.produce() and DesmembramentoAgent.produce() — sp.rollback() releases only the nested savepoint, outer transaction remains valid for quarantine_poison write
  - WR-01 in dlq.py mirrors cms.py:342 — db.commit() before push_destination_task.delay() prevents read-before-commit race in worker's independent session
  - Broker purge scoped to "celery" + "_kombu*" keys only — never FLUSHALL, never brave:* keys (those belong to the Redis flush step above)
  - --no-broker-purge escape hatch for rare cases where queued tasks should be preserved across reset
  - Test isolation fix: desmembramento unit tests filter by source_ref prefix, batch WR-01 test uses order-independent routing assertion
metrics:
  duration: "~4h (continued from prior session)"
  completed: "2026-06-30T22:33:08Z"
  tasks_completed: 4
  files_changed: 10
---

# Quick 260630-pfr: Pipeline Robustness Fixes + Wire ta_config Summary

Per-record SAVEPOINT isolation in sweep_uf producers, WR-01 commit-before-dispatch in DLQ validate endpoints, ta_config wired to TripAdvisorAtrativosIngest per-UF constructor, and Celery broker queue purge in reset-brave-db skill.

## Tasks Completed

| # | Task | Commit | Description |
|---|------|--------|-------------|
| 1 | Wire ta_config to TripAdvisorAtrativosIngest | 7cd4f2b | `ta_config = None` before branch; `ta_config=ta_config` to constructor |
| 2 | Per-record SAVEPOINT isolation | 805826f | `session.begin_nested()` + `sp.commit()` / `quarantine_poison` in mtur.py and desmembramento.py |
| 3 | WR-01 commit-before-dispatch in dlq.py | b9cd9b4 | `db.commit()` before `push_destination_task.delay()` in single + batch validate |
| 4 | reset-brave-db broker purge | 86c2790 | `celery` + `_kombu*` key purge, `--no-broker-purge` flag, SKILL.md update |

## Verification

Full offline suite: **896 passed, 1 skipped, 0 failed** (was 891 + 5 failed before deviation fixes).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Desmembramento unit tests failing with pre-existing DB records**
- **Found during:** Post-T4 full suite run
- **Issue:** 195 `source="desm"` records from real sweeps were committed to the shared test DB. Tests queried `filter_by(source="desm")` and got 195 instead of 0 or 1.
- **Fix:** Changed queries to `filter(source=="desm", source_ref.like("desm:BA:2927408:%"))` — scopes to the specific IBGE code the test creates, ignoring unrelated committed records.
- **Files modified:** `tests/unit/test_desmembramento.py`
- **Commit:** 788101c

**2. [Rule 1 - Bug] Batch WR-01 test flaky on heap order**
- **Found during:** Post-T4 full suite run
- **Issue:** `test_validate_batch_returns_503_when_push_fails_under_real_externals` asserted `reloaded_a.routing == "mar"` (specific record). PostgreSQL heap scan order is non-deterministic; when `test_atrativos_lane_e2e.py` runs first and leaves dead tuples from rolled-back flushes, PE records land in a position where `rio_b` is scanned before `rio_a`, causing the assertion to fail.
- **Fix:** Changed to order-independent assertion: `assert "mar" in statuses` and `assert "dlq" in statuses` — verifies WR-01 correctness (one committed, one not reached) without depending on which specific record was processed first.
- **Files modified:** `tests/integration/test_destinos_lane.py`
- **Commit:** 788101c

## Implementation Notes

### T1 — ta_config scope fix
`ta_config` was only defined inside `if app_config.run_real_externals:`. The `else:` branch (NullTripAdvisorClient path) never defined it, so `TripAdvisorAtrativosIngest(ta_config=ta_config)` raised `NameError` in offline mode. Fixed with `ta_config = None` before the branch.

### T2 — SAVEPOINT semantics
`session.begin_nested()` issues `SAVEPOINT sp1`. `sp.commit()` issues `RELEASE SAVEPOINT sp1` (promotes writes to outer transaction). `sp.rollback()` issues `ROLLBACK TO SAVEPOINT sp1` (discards only that record's writes). The outer transaction remains valid for `quarantine_poison()`. The `isolated_session` fixture (test_sweep_uf.py) uses `join_transaction_mode="create_savepoint"` so inner commits become savepoint releases; outer `trans.rollback()` discards everything at teardown.

### T3 — WR-01 race condition
`get_db` commits the transaction AFTER the handler returns. Before the fix, `push_destination_task.delay(rio_id)` dispatched while the promotion was still in the open request transaction. The worker's independent session called `db.get(RioRecord, rio_id)` and saw `routing="dlq"` (pre-promotion), causing a silent no-op push. After fix: `db.commit()` before dispatch ensures the worker sees `routing="mar"`.

### T4 — Broker key scope
Only `celery` (the Celery task queue list key) and `_kombu*` (Kombu binding/unacked metadata) are deleted. Never `FLUSHALL`, never `brave:*` keys (those belong to the Redis flush step). The `--no-broker-purge` flag allows skipping the purge when stale tasks should be preserved.

## Known Stubs

None.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes introduced.

## Self-Check

All commits verified in git log: 7cd4f2b, 805826f, b9cd9b4, 86c2790, 788101c.
All modified files exist in the worktree.
Full suite: 896 passed, 1 skipped, 0 failed.

## Self-Check: PASSED
