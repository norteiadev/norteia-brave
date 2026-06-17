---
phase: 05-auto-discovery-orchestration
plan: 01
subsystem: orchestration
tags: [celery, shared_task, beat-schedule, destinos, desmembramento, mtur, quarantine, sqlalchemy-savepoint]

# Dependency graph
requires:
  - phase: 02-destinos-lane
    provides: MturSeedIngest + DesmembramentoAgent producers (produce(uf)), MturClient CSV reader, DesmembramentoResult schema
  - phase: 01-foundation
    provides: Celery app (acks_late/reject_on_worker_lost), beat_schedule sweep-{uf}-daily entry, _get_session BRAVE_DB_URL pattern, quarantine_poison helper
  - phase: 03-atrativos-lane-whatsapp-compliance
    provides: discover_atrativo_task — the exact analog (decorator + session lifecycle + quarantine wrapper)
provides:
  - "brave.sweep_uf(uf) registered Celery task — the recurring Destinos producer the beat schedule already expected"
  - "Composition of MturSeedIngest.produce(uf) (idempotent seed) + DesmembramentoAgent.produce(uf) (LLM discovery) behind one task"
  - "Savepoint-isolated integration-test pattern for tasks that call session.commit() internally"
affects: [05-02 atrativos-fsm-auto-advance, 05-03 ops-trigger-cli, auto-discovery-orchestration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Recurring Destinos sweep = compose existing producers in a quarantine-wrapped shared_task (no new scoring branch)"
    - "FileNotFoundError (missing seed CSV) explicitly re-raised as PermanentError so it quarantines instead of retrying"
    - "Integration tests for commit()-ing tasks use a connection-bound session in join_transaction_mode='create_savepoint' so the outer rollback discards everything"

key-files:
  created:
    - tests/integration/test_sweep_uf.py
  modified:
    - brave/tasks/pipeline.py

key-decisions:
  - "sweep_uf is producer-only (D-02) — no auto-validate, no scoring branch; §7.6 + human DLQ gate stays the promotion path"
  - "A missing Mtur CSV is a PermanentError (quarantined), never a retried transient"
  - "Offline test patches tests.fakes.fake_llm.FakeLLMClient at the import site to inject a schema-valid DesmembramentoResult (sweep builds its own fake internally)"
  - "Test isolation via SAVEPOINT join mode rather than the plain db_session fixture (sweep_uf commits internally → rollback-after-commit would be a no-op and leak rows)"

patterns-established:
  - "Compose-producers-in-a-quarantine-wrapped-task: copy discover_atrativo_task's decorator/session/quarantine verbatim, swap only the body"
  - "Savepoint-isolated integration session for internally-committing tasks"

requirements-completed: [ORCH-01, ORCH-04]

# Metrics
duration: 9min
completed: 2026-06-17
---

# Phase 5 Plan 01: brave.sweep_uf Destinos Sweep Summary

**Implemented the phantom `brave.sweep_uf(uf)` Celery task — composing the idempotent Mtur seed re-ingest with the recurring Desmembramento LLM discovery behind one quarantine-wrapped, producer-only task so the 27-UF daily beat fan-out finally resolves to a real producer.**

## Performance

- **Duration:** 9 min
- **Started:** 2026-06-17T17:52:04Z
- **Completed:** 2026-06-17T18:01:04Z
- **Tasks:** 2 (1 implementation, 1 test) — TDD
- **Files modified:** 2 (1 created, 1 modified)

## Accomplishments
- `brave.sweep_uf` is now a registered `@shared_task` (`name="brave.sweep_uf"`, `acks_late`, `reject_on_worker_lost`, `time_limit=600`) — the beat entry `sweep-{uf}-daily` resolves; no more unregistered-task error.
- The task composes `MturSeedIngest(MturClient(), session, ScoreConfig()).produce(uf)` then `DesmembramentoAgent(llm, MturClient(), session, ScoreConfig()).produce(uf)` and commits — producer-only, records routed by the producers' existing `process_nascente_record` (D-02, no new scoring branch).
- A missing Mtur seed CSV (`FileNotFoundError`) is re-raised as `PermanentError` and lands a `PoisonQuarantine` row with `task_name="brave.sweep_uf"` — never lost (T-05-03).
- NotebookLM is NOT run inside the sweep (manual ingest only, Deferred).
- 5 offline, keyless integration tests: name-resolves, destino-ingest, idempotency (re-run is a no-op via store_raw dedup), poison-quarantine, no-NotebookLM.

## Task Commits

1. **Task 2 (RED): failing offline tests for brave.sweep_uf** - `9df519b` (test)
2. **Task 1 (GREEN): implement brave.sweep_uf** - `47d8e7a` (feat) — includes the test refinements that drive the real producer path

_TDD gate sequence: `test(...)` (9df519b) → `feat(...)` (47d8e7a). No refactor commit needed._

## Files Created/Modified
- `brave/tasks/pipeline.py` - Added the `sweep_uf` shared_task (decorator + session lifecycle + quarantine wrapper mirror `discover_atrativo_task`; body composes the two destino producers; FileNotFoundError → PermanentError → quarantine).
- `tests/integration/test_sweep_uf.py` - 5 offline tests + a `isolated_session` fixture (SAVEPOINT join mode) + a `_patch_fake_llm` helper injecting a schema-valid `DesmembramentoResult`.

## Decisions Made
- **Producer-only (D-02):** the sweep adds no scoring/validation branch — §7.6 routing + the human DLQ steward gate stay the promotion path.
- **Missing CSV is permanent:** `FileNotFoundError` is wrapped into `PermanentError` inside the inner try so it quarantines immediately rather than burning 3 retries.
- **Test isolation via SAVEPOINT:** because `sweep_uf` calls `session.commit()`, the plain rollback-based `db_session` fixture would leak rows into the shared dev DB; the test uses a connection-bound session in `join_transaction_mode="create_savepoint"` so the inner commit only releases a savepoint and the outer `trans.rollback()` discards everything.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test isolation: sweep_uf's internal commit() leaked rows into the shared DB**
- **Found during:** Task 2 (offline integration test)
- **Issue:** The plan's suggested approach (drive the real path against the bundled CSV) works, but `sweep_uf` calls `session.commit()` internally. Using the standard rollback-based `db_session` fixture made every committed Nascente/Rio row permanent in the shared docker-compose Postgres (rollback-after-commit is a no-op), which then polluted an unrelated test (`test_destinos_lane.py::test_notebooklm_corroboration_boosts_mtur`, which queries by `municipio_id=2927408`).
- **Fix:** Added an `isolated_session` fixture binding the session to an explicit connection + outer transaction in `join_transaction_mode="create_savepoint"`; the inner `commit()` releases a SAVEPOINT and the outer `trans.rollback()` at teardown discards everything. Verified zero `desm:*:trancoso` / real-CSV `mtur:BA` rows remain after the suite runs.
- **Files modified:** tests/integration/test_sweep_uf.py
- **Verification:** `test_sweep_uf.py` (5) + `test_destinos_lane.py::test_notebooklm_corroboration_boosts_mtur` pass together; post-run DB query shows 0 leaked rows.
- **Committed in:** 47d8e7a (Task 1/GREEN commit)

**2. [Rule 3 - Blocking] FakeLLMClient() with no fixture returned None → DesmembramentoAgent crashed on result.destinos**
- **Found during:** Task 1 (GREEN, first test run)
- **Issue:** `sweep_uf` builds its own `FakeLLMClient()` (no `fixture_result`) when `run_real_externals=False`; `extract()` then returns `None`, and `DesmembramentoAgent.produce` accesses `result.destinos` outside its try/except for Oferta-Principal municípios → `AttributeError`.
- **Fix:** Test helper `_patch_fake_llm` monkeypatches `tests.fakes.fake_llm.FakeLLMClient` at the import site to a factory returning `FakeLLMClient(fixture_result=DesmembramentoResult(...))` — keeps the test offline/keyless while exercising the real producer path. No production change needed (production uses the real LLM via `run_real_externals=True`).
- **Files modified:** tests/integration/test_sweep_uf.py
- **Verification:** `test_sweep_uf_ingests_destinos` passes; destination Rio rows created for BA.
- **Committed in:** 47d8e7a (Task 1/GREEN commit)

---

**Total deviations:** 2 auto-fixed (1 bug, 1 blocking) — both confined to the test layer; production `sweep_uf` matches the plan exactly.
**Impact on plan:** No scope creep. The implementation body is verbatim-faithful to the plan/PATTERNS analog; the deviations were test-harness correctness fixes (isolation + offline fake wiring).

## Cleanup Note (shared dev DB)
Early GREEN-debug `sweep_uf.run("BA")` calls (before the SAVEPOINT fixture existed) committed 7 real-CSV `mtur:BA:NNNNNNN` rows + their Rio records into the shared docker-compose Postgres. These were deleted by exact primary-key UUID to restore the DB to its prior state (which un-broke `test_destinos_lane.py::test_notebooklm_corroboration_boosts_mtur`). The new savepoint-isolated fixture prevents any recurrence.

## Issues Encountered
- See Deviations above. Both surfaced during the GREEN phase and were resolved in the test harness; the sweep_uf production code did not change after first write.

## User Setup Required
None - no external service configuration required. The sweep is 100% offline/keyless by default (`run_real_externals=False` → FakeLLMClient + bundled CSV).

## Next Phase Readiness
- ORCH-01 (Destinos sweep) closed; ORCH-04 Destinos half (offline-testable) closed.
- Ready for **05-02** (Atrativos FSM auto-advance, ORCH-02) and **05-03** (ops-trigger CLI, ORCH-03).
- Note for 05-02/05-03: the savepoint-isolated session pattern here is the right template for any test that exercises a task which commits internally.

## Self-Check: PASSED

- FOUND: `brave/tasks/pipeline.py` (`name="brave.sweep_uf"` present)
- FOUND: `tests/integration/test_sweep_uf.py`
- FOUND: `.planning/phases/05-auto-discovery-orchestration/05-01-SUMMARY.md`
- FOUND commit: `9df519b` (test RED gate)
- FOUND commit: `47d8e7a` (feat GREEN gate)

---
*Phase: 05-auto-discovery-orchestration*
*Completed: 2026-06-17*
