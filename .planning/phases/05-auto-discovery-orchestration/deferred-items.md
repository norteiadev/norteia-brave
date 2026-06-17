# Phase 05 — Deferred / Out-of-Scope Items

Discoveries logged during execution that are NOT caused by the current plan's changes.
Per the executor scope boundary, these are recorded but not fixed.

## From Plan 05-02 (Atrativos FSM auto-advance)

### test_atrativos_gate.py — `.env`-secret vs test-secret collision (pre-existing)
- **What:** 5 tests in `tests/integration/test_atrativos_gate.py` fail with 401 ONLY when the
  repo `.env` is sourced before pytest. The tests use
  `os.environ.setdefault("BRAVE_WEBHOOK_SECRET", "test-atrativos-gate-webhook-secret")` and
  then send that test secret in `WEBHOOK_HEADERS`. When `.env` is loaded first, the real
  `BRAVE_WEBHOOK_SECRET` (and `BRAVE_STEWARD_SECRET`) already occupy the env, `setdefault`
  keeps the real value, and the test's baked-in secret no longer matches → 401.
- **Evidence it is pre-existing & unrelated:** Plan 05-02 never touched `atrativos_gate.py`,
  its router, or these tests. The full suite (372 tests) passes keyless
  (`BRAVE_DB_URL`-only, the CI-faithful path). The failures appear exclusively under
  `set -a; source .env`.
- **Suggested fix (future):** have these gate tests force their own secret with
  `os.environ[...] = TEST_SECRET` (not `setdefault`), or run gate tests in a secret-isolated
  subprocess. Out of scope for ORCH-02.

### Pre-existing ruff nits in touched files (not introduced by 05-02)
- `brave/tasks/pipeline.py`: `E402` (module-level `from brave.core.quarantine import ...`
  re-export at line ~241) and `N806` (`SessionFactory` variable name in `_get_session`).
  Confirmed present in `HEAD~5` before this plan's edits.
- `brave/lanes/atrativos/discovery_agent.py`: `UP037` remove-quotes on `"PlacesClientProtocol"`
  / `"LLMClientProtocol"` forward-ref annotations (pre-existing TYPE_CHECKING style).
- Left untouched to keep the diff minimal and within scope.
