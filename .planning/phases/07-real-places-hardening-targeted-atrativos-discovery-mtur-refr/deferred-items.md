# Deferred Items — Phase 07

## Pre-existing Test Isolation Bug

**File:** `tests/integration/test_destinos_lane.py::test_notebooklm_corroboration_boosts_mtur`
**Discovered during:** Plan 07-06 execution
**Status:** Pre-existing — not caused by plan 07-06 changes

### Root Cause

`_make_dlq_record()` and `test_mtur_lane_end_to_end` call `db_session.commit()` explicitly,
leaving Porto Seguro (IBGE `2927408`) records in the DB across test runs. The conftest
`db_session` fixture only calls `session.rollback()` on teardown — which cannot undo
already-committed rows.

When `test_notebooklm_corroboration_boosts_mtur` runs after these tests, the
`NotebookLMIngest` IBGE-match query finds the OLD committed record (UUID `7e61859d...`) and
boosts THAT record's `corroboracao_value`. The test then checks the NEWLY created record's
`corroboracao_value` and finds 0.0 — causing the assertion to fail.

### Evidence

- Confirmed: test fails even with `git stash` (no plan 07-06 changes) when run in the full
  integration suite.
- Confirmed: test passes when run in isolation (`pytest tests/integration/test_destinos_lane.py
  ::test_notebooklm_corroboration_boosts_mtur -v` alone).
- DB query confirms: `SELECT ... WHERE municipio_id='2927408'` returns 1 pre-existing row
  with `routing='dlq'` and `corroboracao_value='0.0'` before the test runs.

### Fix (deferred)

Option 1: In `test_notebooklm_corroboration_boosts_mtur`, delete any pre-existing
`municipality_id='2927408'` records before creating the test record.

Option 2: Change `_make_dlq_record` to not call `db_session.commit()`, relying only on
`flush()`. The endpoint tests should work with flushed (not committed) data since they use
the same session.

Option 3: Add a test-level cleanup fixture that truncates rio_records before integration
tests.

### Impact

- 1 pre-existing failure in full offline suite; does NOT affect correctness of plan 07-06
  changes (harness corroboration boost and offline unit tests).
