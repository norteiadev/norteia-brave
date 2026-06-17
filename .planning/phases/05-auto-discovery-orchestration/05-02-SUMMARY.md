---
phase: 05-auto-discovery-orchestration
plan: 02
subsystem: orchestration
tags: [celery, atrativos, sub-state-fsm, advance_sub_state, enqueue-chain, discovery, human-gate, sqlalchemy-savepoint, replay-safety]

# Dependency graph
requires:
  - phase: 03-atrativos-lane-whatsapp-compliance
    provides: advance_sub_state guard, DiscoveryAgent/ContactFinderAgent/SignalAgent (inline sub_state guards), find_contacts_task/gather_signals_task/outreach_task, atrativos_gate.py:378 (the sole outreach dispatch)
  - phase: 01-foundation
    provides: process_nascente_record (§7.6 routing, idempotent by canonical_key), Celery app (acks_late), _get_session BRAVE_DB_URL pattern, quarantine wrapper
  - phase: 05-auto-discovery-orchestration (plan 01)
    provides: SAVEPOINT-isolated integration-test pattern; the discover_atrativo_task analog
provides:
  - "Discovery-side FSM substrate: DiscoveryAgent.produce now creates the Rio + seeds sub_state='discovered' (finding #1 closed)"
  - "ORCH-02 self-enqueue chain: discover_atrativo_task -> find_contacts_task -> gather_signals_task, keyed on sub_state queries (D-03)"
  - "Terminal-at-the-gate guarantee: the automatic chain stops at sub_state='aguardando_consulta_whatsapp' and triggers NO outreach (D-07)"
  - "Offline e2e chain test proving advance-to-gate + stop + no-auto-outreach + replay-noop"
affects: [05-03 ops-trigger-cli, auto-discovery-orchestration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "FSM init at the producer: a lane producer calls process_nascente_record + advance_sub_state(expected_state=None) inline to seed the NULL anchor (mirrors mtur.py; D-18 boundary kept)"
    - "Self-enqueue chain keyed on sub_state queries, never on producer return values (DiscoveryAgent.produce returns None) — self-healing across restarts (D-03)"
    - "Dispatch-then-inline-fallback (try .delay / except .run) so an operator/test with no broker advances the chain synchronously"
    - "Fan-out detach-safety: materialize the rio IDs (select id column) BEFORE dispatching, because the inline .run fallback expires ORM rows mid-loop"
    - "Nested-task integration test: real per-task sessions (production fidelity) + deterministic synthetic-UF cleanup, instead of a single shared SAVEPOINT session"

key-files:
  created:
    - tests/integration/test_atrativos_chain_e2e.py
  modified:
    - brave/lanes/atrativos/discovery_agent.py
    - brave/tasks/pipeline.py
    - tests/integration/test_atrativos_lane_e2e.py
    - tests/unit/lanes/test_discovery_agent.py

key-decisions:
  - "finding #1: DiscoveryAgent.produce seeds the FSM via process_nascente_record + advance_sub_state(expected_state=None, next_state='discovered') — without it the contact_finder precondition is never met and the chain is dead"
  - "finding #2 (LOCKED): KEEP the three agents' inline sub_state precondition guards; only the discovery-side INIT uses advance_sub_state. No agent refactor. Replay-safety preserved — a duplicate dispatch hits the inline guard and no-ops (D-04)"
  - "D-07 invariant: gather_signals_task is the terminal auto-step; NO enqueue tail after it; outreach_task stays dispatched only by atrativos_gate.py:378. test_no_auto_outreach asserts call_count==0"
  - "Fan-out queries by sub_state (entity_type='attraction' AND uf=uf AND sub_state='discovered'), never by produce's return value (D-03)"
  - "Chain e2e uses a synthetic UF ('ZZ') so the fan-out query is independent of leaked 'discovered' rows in the shared dev DB; real per-task sessions + targeted cleanup (SAVEPOINT-on-one-connection is too fragile for a nested chain)"

patterns-established:
  - "Producer-seeds-the-FSM (process_nascente_record + advance_sub_state NULL-anchor) for lane producers that previously only wrote Nascente"
  - "Detach-safe Celery fan-out: select the id column and dispatch by id string, never iterate live ORM objects across a dispatch"

requirements-completed: [ORCH-02, ORCH-04]

# Metrics
duration: 18min
completed: 2026-06-17
---

# Phase 5 Plan 02: Atrativos FSM Auto-Advance Summary

**Closed the two gaps that left the Atrativos sub-state FSM stalled at `discovered`: production discovery now seeds the Rio at `sub_state='discovered'` (finding #1), and `discover_atrativo_task` self-enqueues the `find_contacts → gather_signals` chain (keyed on sub_state queries) so a borderline attraction auto-advances to the human WhatsApp gate (`aguardando_consulta_whatsapp`) and STOPS there — with a test proving the automatic chain triggers no outreach (D-07) and replay is a no-op (D-04).**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-06-17T18:06:22Z
- **Completed:** 2026-06-17T18:23:56Z
- **Tasks:** 3 (TDD throughout) + 2 deviation fixes
- **Files:** 5 (1 created, 4 modified)

## Accomplishments

- **finding #1 closed (ORCH-02):** `DiscoveryAgent.produce` now, after `store_raw` + the discovery audit, calls `process_nascente_record(self._session, nascente, self._config)` and `advance_sub_state(..., expected_state=None, next_state="discovered", actor="discovery_agent")`. Every ingested attraction now has a RioRecord at `sub_state='discovered'` — the chain's `sub_state='discovered'` query finally has an anchor. `produce` still returns `None`.
- **ORCH-02 chain wired:** `discover_atrativo_task` (after produce + commit) queries `RioRecord` where `entity_type='attraction' AND uf=uf AND sub_state='discovered'` and dispatches `find_contacts_task` per row (dispatch-then-inline-fallback). `find_contacts_task` (after commit, only if it advanced to `contacts_found`) dispatches `gather_signals_task`. `gather_signals_task` is left **terminal** — a borderline (<85%) record lands `aguardando_consulta_whatsapp` and the chain stops.
- **finding #2 honored:** the three agents' inline `sub_state` precondition guards were kept unchanged; only the discovery-side INIT uses `advance_sub_state`. Replay-safety is preserved by those guards (a duplicate dispatch no-ops).
- **D-07 invariant proven:** `gather_signals_task` has no enqueue tail and never references `outreach_task`; `test_no_auto_outreach` spies `outreach_task.delay`/`.run` and asserts `call_count == 0` across a full chain run. `atrativos_gate.py:378` remains the sole outreach trigger (untouched).
- **Offline e2e:** `tests/integration/test_atrativos_chain_e2e.py` (4 tests) drives the chain inline via the sync fallback and asserts advance-to-gate, stop-at-gate (no auto Mar promotion), no-auto-outreach, and replay-noop — 100% offline/keyless, deterministic, zero shared-DB leakage.
- **Whole suite green keyless:** 372 passed with `BRAVE_DB_URL`-only (the CI-faithful path).

## Task Commits

1. **Task 1 (RED):** failing test for discovery-side FSM init (finding #1) — `31aabe8` (test)
2. **Task 1 (GREEN):** initialize FSM substrate at discovery — `1726ef9` (feat)
3. **Task 2:** wire the FSM enqueue chain discover→find_contacts→gather_signals — `dde0753` (feat)
4. **Task 3:** offline e2e atrativos auto-chain (advance/stop/no-outreach/replay) — `527abc2` (test)
5. **Deviation (Rule 3):** patch FSM-init collaborators in DiscoveryAgent unit tests — `3fa36bb` (fix)
6. **Deviation (Rule 1):** detach-safe fan-out + deterministic chain e2e — `a441648` (fix)

_TDD gate sequence for Task 1: `test(...)` (31aabe8) → `feat(...)` (1726ef9). Tasks 2–3 wire/verify against the structural + e2e gates._

## Files Created/Modified

- `brave/lanes/atrativos/discovery_agent.py` — imports `process_nascente_record` + `advance_sub_state`; after each attraction's `store_raw`+audit, creates the Rio and seeds `sub_state='discovered'` (idempotent; D-18 boundary kept).
- `brave/tasks/pipeline.py` — enqueue tails: `discover_atrativo_task` fans out `find_contacts_task` per `sub_state='discovered'` row (by id, dispatch-then-inline); `find_contacts_task` enqueues `gather_signals_task` only if it advanced to `contacts_found`. `gather_signals_task` left terminal. No outreach added.
- `tests/integration/test_atrativos_chain_e2e.py` — **created**: 4 offline e2e tests + a real-session fixture with synthetic-UF cleanup.
- `tests/integration/test_atrativos_lane_e2e.py` — added `test_discovery_inits_sub_state_discovered` (Task 1 RED→GREEN) + `func` import.
- `tests/unit/lanes/test_discovery_agent.py` — patched the two new FSM-init collaborators out of the mock-session unit tests (deviation #1).

## Decisions Made

- **finding #1 — seed at the producer:** the discovery-side init uses `advance_sub_state(expected_state=None)` (the NULL anchor), exactly the sequence the existing e2e test used to perform manually; it is now production code.
- **finding #2 — no agent refactor:** keep the inline guards (`contact_finder_agent.py:72`, `signal_agent.py:172`). They already deliver D-04 replay-safety; refactoring all three agents to `advance_sub_state` was rejected as broader-surface/higher-risk for no functional gain.
- **Terminal at the gate (D-07):** add nothing after `gather_signals_task`. The automation boundary (human WhatsApp gate) is enforced structurally (no enqueue) and asserted (`test_no_auto_outreach`).
- **Chain query by sub_state (D-03):** the fan-out keys on the FSM, never on `produce`'s return value (which is `None`), so a missed enqueue self-heals on the next sweep.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] DiscoveryAgent unit tests crashed on the new FSM-init collaborators**
- **Found during:** Task 1 (after GREEN, running the broader unit suite)
- **Issue:** `test_discovery_agent.py` drives `produce()` against a `MagicMock` session and patches `store_raw`. Task 1 added real `process_nascente_record` + `advance_sub_state` calls, which then ran against the mock rio/nascente → `AttributeError: Mock object has no attribute 'sub_state'`.
- **Fix:** Patched `brave.lanes.atrativos.discovery_agent.process_nascente_record` and `...advance_sub_state` in the two affected tests (they assert the store_raw/parent-guard contract, not Rio creation — that is covered against a real DB in `test_atrativos_lane_e2e.py`).
- **Files modified:** tests/unit/lanes/test_discovery_agent.py
- **Commit:** `3fa36bb`

**2. [Rule 1 - Bug] Fan-out raised DetachedInstanceError when the inline fallback ran**
- **Found during:** Task 3 (running the chain e2e deterministically without `.env`)
- **Issue:** `discover_atrativo_task` iterated live `RioRecord` ORM objects while calling `find_contacts_task.run(...)` inline; the nested task's commit expired/detached those objects, so the next `str(rio.id)` access raised `DetachedInstanceError`. (Latent in production too — surfaced because the test shares the engine.)
- **Fix:** Materialize the discovered rio **ids** up front (`select(RioRecord.id)...`) and dispatch by id string; never hold ORM objects across a dispatch.
- **Files modified:** brave/tasks/pipeline.py
- **Commit:** `a441648`

**3. [Rule 1 - Bug, test layer] Chain e2e was non-deterministic against the shared dev DB**
- **Found during:** Task 3
- **Issue:** The original test used `uf='BA'` and a single SAVEPOINT-shared session; the fan-out `sub_state='discovered'` query picked up leaked `BA` rows from earlier phases, and cross-session MVCC visibility under one connection made reads see stale state.
- **Fix:** Switched the chain e2e to real per-task sessions (production fidelity) keyed on a **synthetic UF (`ZZ`) + IBGE (`2999999`)** so the fan-out matches only this test's record, with a deterministic UF-scoped `_purge_uf` cleanup before and after each test. Verified repeatable + zero leakage.
- **Files modified:** tests/integration/test_atrativos_chain_e2e.py
- **Commit:** `a441648`

**Total deviations:** 3 auto-fixed (2 bugs incl. 1 production fan-out fix, 1 blocking). Production chain logic matches the plan; deviation #2 hardened it.

## Out-of-Scope / Deferred

Logged to `.planning/phases/05-auto-discovery-orchestration/deferred-items.md`:
- `test_atrativos_gate.py` 5 failures occur **only** when `.env` is sourced (the tests' `setdefault` secret collides with the real `BRAVE_WEBHOOK_SECRET`). Pre-existing; the file was untouched by this plan; the full suite passes keyless. Not fixed (out of scope).
- Pre-existing ruff nits in `pipeline.py` (E402/N806) and `discovery_agent.py` (UP037) confirmed present before this plan. Left untouched.

## Known Stubs

None. The chain wires real producers/agents end-to-end; no placeholder data paths introduced.

## Threat Flags

None. No new network endpoint, auth path, file access, or schema change was introduced. The plan's `<threat_model>` mitigations were honored: T-05-04 (no auto-outreach) is structurally enforced + asserted; T-05-05 (replay) is covered by the inline guards + id-keyed fan-out; T-05-06 (malformed input) keeps the existing validate-or-skip + quarantine wrapper. No new package installs (T-05-SC).

## User Setup Required

None — 100% offline/keyless by default (`run_real_externals=False` → FakePlaces/FakeApify/FakeLLM). Integration tests require docker-compose Postgres+Redis (already up) and `BRAVE_DB_URL`.

## Next Phase Readiness

- ORCH-02 (Atrativos FSM auto-advance) and the Atrativos half of ORCH-04 (offline-testable, gate untouched) are closed.
- Ready for **05-03** (ops-trigger CLI, ORCH-03): the CLI's `sweep <uf> --lane atrativos` path can dispatch `discover_atrativo_task` and rely on the now-wired chain to drive records to the gate.
- Note for 05-03: for any test exercising a NESTED chain of internally-committing tasks, prefer the real-session + synthetic-UF-cleanup pattern here over a single shared SAVEPOINT session.

## Self-Check: PASSED

- FOUND: `tests/integration/test_atrativos_chain_e2e.py`
- FOUND: `brave/lanes/atrativos/discovery_agent.py` (process_nascente_record + advance_sub_state seed present)
- FOUND: `brave/tasks/pipeline.py` (find_contacts_task fan-out + gather_signals_task enqueue present)
- FOUND: `.planning/phases/05-auto-discovery-orchestration/05-02-SUMMARY.md`
- FOUND commit: `31aabe8` (Task 1 RED), `1726ef9` (Task 1 GREEN)
- FOUND commit: `dde0753` (Task 2), `527abc2` (Task 3)
- FOUND commit: `3fa36bb` (deviation 1), `a441648` (deviation 2/3)

---
*Phase: 05-auto-discovery-orchestration*
*Completed: 2026-06-17*
