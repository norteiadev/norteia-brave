---
phase: quick-260630-pfr
verified: 2026-06-30
status: passed
score: 4/4 fixes verified (offline + integration)
---

# Quick 260630-pfr — pipeline robustness (4 fixes) — Verification

All four fixes surfaced by the oa3 live painel test.

## #1 — ftx geo-linkage activated in prod
`sweep_tripadvisor` per-UF path now passes `ta_config=ta_config` to `TripAdvisorAtrativosIngest`
(pipeline.py). `ta_config` initialized to None before the `run_real_externals` branch (safe offline).
The ftx `fetch_attraction_geo` fallback (guard `if ibge_match is None and self._ta_config is not None`)
now fires in the real sweep instead of falling to Nominatim → ibge_unmatched. Unit tests capture the
constructor kwargs (ta_config set with real externals, None offline).

## #2A — per-record SAVEPOINT isolation
`MturSeedIngest.produce` + `DesmembramentoAgent.produce` wrap each record's store_raw +
process_nascente_record in `session.begin_nested()`; on failure `sp.rollback()` then
`quarantine_poison(session=self._session, nascente_id=None, ...)` writes to the OUTER transaction and
survives to the terminal commit. One bad município no longer discards the whole UF. Integration test
(BRAVE_DB_URL) proves good records commit + one quarantine row exists when a record fails.

## #2B — dlq.py commit-before-dispatch (WR-01)
`validate` and `validate_batch` now `db.commit()` after write_audit, BEFORE
`push_destination_task.delay(...)` — mirroring cms.py:342. Kills the read-before-commit race where the
worker's independent session couldn't find the uncommitted RioRecord ("RioRecord not found"). Batch is
now per-row commit (a later-row failure no longer rolls back already-promoted rows). The two integration
tests updated (routing dlq→mar on the WR-01 path).

## #4 — reset-brave-db broker purge
`scripts/reset_db.py` now purges the Celery broker (`celery` list + `_kombu*` keys) after the brave:*
flush; `--no-broker-purge` escape hatch; SKILL.md documents it. Scoped — never FLUSHALL, never the
redbeat schedule. Stops stale queued tasks (e.g. push_destination referencing reset-away rio_ids) from
re-firing on worker restart.

## Suite
- Offline: `BRAVE_USE_FAKEREDIS=1` unit suite — all green (exit 0, 100%, 0 failures).
- Integration (with BRAVE_DB_URL, real Postgres): executor run reported 896 passed, 1 skipped, 0 failed,
  incl. a deviation commit (788101c) fixing pre-existing test-DB isolation flakiness.

Deferred/out of scope (unchanged): IBGE stays CSV-in-memory (operator decision); geoId-table
correctness (rmz discovery); TA destinos QID; acks_late worker-loss amplifier (noted, not fixed).

PASSED.
