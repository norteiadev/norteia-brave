---
phase: quick-260630-pfr
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - brave/tasks/pipeline.py
  - brave/lanes/destinos/mtur.py
  - brave/lanes/destinos/desmembramento.py
  - brave/api/routers/dlq.py
  - tests/unit/tasks/test_sweep_tripadvisor.py
  - tests/integration/test_sweep_uf.py
  - tests/integration/test_destinos_lane.py
  - .claude/skills/reset-brave-db/scripts/reset_db.py
  - .claude/skills/reset-brave-db/SKILL.md
autonomous: true
requirements: [PIPELINE-ROBUSTNESS-01, PIPELINE-ROBUSTNESS-02A, PIPELINE-ROBUSTNESS-02B, SKILL-BROKER-PURGE]

must_haves:
  truths:
    - "TripAdvisor atrativos sweep calls fetch_attraction_geo (ftx geo-linkage) when run_real_externals=True — ta_config is not None inside the producer"
    - "A single bad Mtur record is quarantined and other UF records are committed — one failure does not discard the whole UF"
    - "dlq/validate and dlq/validate-batch commit the promotion to DB before dispatching push_destination_task.delay — no RioRecord-not-found race in the worker"
    - "reset-brave-db --yes purges the Celery broker queue (celery list + kombu unacked keys) in addition to Postgres + brave:* Redis keys"
  artifacts:
    - path: "brave/tasks/pipeline.py"
      provides: "ta_config=None defined before if run_real_externals block; passed as ta_config=ta_config to TripAdvisorAtrativosIngest per-UF constructor"
      contains: "ta_config=ta_config"
    - path: "brave/lanes/destinos/mtur.py"
      provides: "session.begin_nested() SAVEPOINT wrapping store_raw+process_nascente_record per municipality; per-record quarantine on except"
      contains: "begin_nested"
    - path: "brave/lanes/destinos/desmembramento.py"
      provides: "session.begin_nested() SAVEPOINT wrapping store_raw per destino item in inner loop"
      contains: "begin_nested"
    - path: "brave/api/routers/dlq.py"
      provides: "db.commit() before push_destination_task.delay() in both validate_destination (single) and validate_batch paths — WR-01 mirroring cms.py"
      contains: "db.commit()"
    - path: ".claude/skills/reset-brave-db/scripts/reset_db.py"
      provides: "--no-broker-purge flag; Celery broker queue purge step (celery list + _kombu/unacked keys) after brave:* Redis flush"
      contains: "no-broker-purge"
  key_links:
    - from: "pipeline.sweep_tripadvisor"
      to: "TripAdvisorAtrativosIngest.__init__(ta_config=ta_config)"
      via: "per-UF constructor call (~line 1098)"
      pattern: "ta_config=ta_config"
    - from: "atrativos._ingest_one"
      to: "self._client.fetch_attraction_geo"
      via: "guard: if ibge_match is None and self._ta_config is not None (atrativos.py:211)"
      pattern: "ta_config is not None"
    - from: "mtur.py MturSeedIngest.produce loop"
      to: "quarantine_poison (per-record)"
      via: "session.begin_nested() → sp.rollback() on except"
      pattern: "begin_nested"
    - from: "dlq.py validate_destination"
      to: "push_destination_task.delay"
      via: "db.commit() at WR-01 site BEFORE delay call"
      pattern: "db\\.commit\\(\\).*push_destination_task"
---

<objective>
Four independent pipeline robustness fixes surfaced by the live oa3 test run.

Purpose: The live PR TA sweep exposed that (1) ftx geo-linkage (fetch_attraction_geo) is
silently dormant because sweep_tripadvisor never passes ta_config to the atrativos producer;
(2) sweep_uf's single shared transaction means one bad Mtur record discards 168 good ones
and returns Celery SUCCESS; (3) dlq validate/batch dispatches push_destination_task before
committing the session, so the worker's independent session sees no RioRecord; (4) after
reset-brave-db, stale Celery queue tasks re-fire and hit reset-away rio_ids.

Output: Four surgical patches + targeted tests. Full offline suite stays green (~892).
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/ROADMAP.md
@.planning/STATE.md

<!-- Key interfaces the executor needs — extracted to avoid codebase exploration -->

From brave/tasks/pipeline.py (~line 990-1005, sweep_tripadvisor):
  - `ta_config` is defined ONLY inside `if app_config.run_real_externals:` block (line ~995)
  - Else branch (offline) never defines `ta_config`, so per-UF constructor at ~1098 omits it
  - Fix: add `ta_config = None` before the `if app_config.run_real_externals:` block; set
    `ta_config = TripAdvisorConfig()` inside the real-externals branch (already there); pass
    `ta_config=ta_config` to `TripAdvisorAtrativosIngest` at the per-UF constructor (~1098-1105)

From brave/lanes/tripadvisor/atrativos.py:
  - `TripAdvisorAtrativosIngest.__init__` accepts `ta_config: TripAdvisorConfig | None = None`
  - Guard at line 211: `if ibge_match is None and self._ta_config is not None:` — dormant with None

From brave/lanes/destinos/mtur.py:
  - `MturSeedIngest.produce(uf, *, run_rio=True)` loops `municipalities`; per record calls
    `store_raw(session=self._session, ...)` then `if run_rio: process_nascente_record(session=...)`
  - NO per-record try/except; NO SAVEPOINT; a single record exception propagates to sweep_uf
  - `quarantine_poison` NOT currently imported here — add it from `brave.core.quarantine`

From brave/lanes/destinos/desmembramento.py:
  - `DesmembramentoAgent.produce(uf)` has per-município try/except (lines ~169-187) for LLM failures
  - Inner loop (lines ~198-238) calls `store_raw(...)` per destino item — NO SAVEPOINT
  - `quarantine_poison` already imported from `brave.core.quarantine`

From brave/core/quarantine.py:
  - `quarantine_poison(session, nascente_id, task_name, error, payload)` does
    `session.add(PoisonQuarantine(...)) + session.flush()` — safe to call after `sp.rollback()`
    because sp.rollback() releases only the savepoint; outer transaction remains valid

From brave/api/routers/dlq.py (validate_destination, ~lines 155-198):
  - Current order: validate_and_promote_rio → refresh → [if mar: push dispatch] → write_audit → return
  - Bug: push dispatch fires before session.commit() (deps.py:163 commits AFTER handler returns)
  - cms.py WR-01 reference pattern (lines 326-349): write_audit → db.commit() → refresh → [if mar: push]
  - Fix order: validate_and_promote_rio → refresh → write_audit → db.commit() → refresh → [if mar: push]

From brave/api/routers/dlq.py (validate_batch, ~lines 222-278):
  - Current order per row: validate_and_promote_rio → refresh → [if mar: push] → write_audit → validated++
  - Same dispatch-before-commit bug; fix: per-row write_audit → db.commit() → refresh → [if mar: push]
  - WARNING: Two existing tests assert rollback-on-503 semantics (old pre-WR-01 behavior):
      tests/integration/test_destinos_lane.py:
        test_validate_returns_503_when_push_fails_under_real_externals (line ~278)
          OLD: reloaded.routing == "dlq", audit is None
          NEW (WR-01): reloaded.routing == "mar", audit is not None  ← must update
        test_validate_batch_returns_503_when_push_fails_under_real_externals (line ~348)
          OLD: both records routing == "dlq"
          NEW: rio_a.routing == "mar" (committed before failed dispatch), rio_b.routing == "dlq"  ← must update

From .claude/skills/reset-brave-db/scripts/reset_db.py:
  - Redis flush section (lines ~149-160): deletes keys matching `--redis-pattern` (default "brave:*")
  - Does NOT touch the Celery broker queue: Redis list key `celery` + kombu unacked keys
    (_kombu.binding.celery, unacked, unacked_index are the standard Kombu/Celery broker keys)
  - Add `--no-broker-purge` flag (argparse); after brave:* flush, scan+delete:
      keys matching "celery" (the task queue list)
      keys matching "_kombu*" (binding/unacked metadata)
    using `r.scan_iter` with those patterns + `r.delete(*keys)` — same pattern as brave:* flush
  - Gate: skip broker purge when --no-broker-purge OR --no-redis is set
  - Print deleted count + remaining (same as brave:* section)
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Wire ta_config in sweep_tripadvisor per-UF path (#1)</name>
  <files>brave/tasks/pipeline.py, tests/unit/tasks/test_sweep_tripadvisor.py</files>
  <behavior>
    - Test: when sweep_tripadvisor runs with run_real_externals=True, the TripAdvisorAtrativosIngest
      constructor is called with ta_config that is not None (a TripAdvisorConfig instance)
    - Test: when run_real_externals=False (offline), TripAdvisorAtrativosIngest is called with
      ta_config=None (fallback safe — ftx guard requires ta_config is not None)
  </behavior>
  <action>
    In brave/tasks/pipeline.py, find the `sweep_tripadvisor` function. Locate the line that reads
    `ta_config = TripAdvisorConfig()` (inside the `if app_config.run_real_externals:` block,
    currently ~line 995). Add `ta_config = None` ONE LINE BEFORE the `if app_config.run_real_externals:`
    block begins (so the variable is defined in the outer scope). The assignment inside the
    real-externals branch then overrides it to a `TripAdvisorConfig()` instance.

    Then find the per-UF `TripAdvisorAtrativosIngest(...)` constructor call in the
    `else:` branch of `if bulk_national:` (~lines 1098-1105). Add `ta_config=ta_config` as a
    keyword argument. The bulk_national branch (produce_paginated) does NOT need ta_config; leave
    it unchanged.

    In tests/unit/tasks/test_sweep_tripadvisor.py, add TWO new unit tests at the bottom of the
    file (after the existing class):

    class TestSweepTripadvisorTaConfig:
        def test_passes_ta_config_when_real_externals(monkeypatch): captures the kwargs passed to
          TripAdvisorAtrativosIngest (patch the class at
          "brave.lanes.tripadvisor.atrativos.TripAdvisorAtrativosIngest" with a recording factory
          that stores kwargs and returns an AsyncMock with .produce). Reuse the AppConfig mock
          pattern from _run_sweep_with_stub_client (mock_app_config.run_real_externals=True),
          patch TripAdvisorConfig at "brave.config.settings.TripAdvisorConfig" to return a sentinel,
          run sweep_tripadvisor.__wrapped__.__func__(mock_self, uf="BA"). Assert captured_kw["ta_config"]
          is the sentinel (not None).

        def test_passes_ta_config_none_when_offline(monkeypatch): same setup but
          mock_app_config.run_real_externals=False (offline branch → NullTripAdvisorClient). Assert
          captured_kw["ta_config"] is None.

    Both tests run 100% offline (no DB, no Redis, fakeredis not needed). Do NOT use the
    _run_sweep_with_stub_client helper (it patches TripAdvisorAtrativosIngest via the atrativos
    module; you need your own patch to capture kwargs). Follow the same monkeypatch pattern for
    _get_session, ScoreConfig, AppConfig, load_ibge_csv already in the file.
  </action>
  <verify>
    <automated>BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/unit/tasks/test_sweep_tripadvisor.py -x -q 2>&1 | tail -5</automated>
  </verify>
  <done>
    Both new tests pass. sweep_tripadvisor per-UF path constructor call includes ta_config=ta_config.
    Existing session-error tests unaffected.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Per-record SAVEPOINT isolation in sweep_uf producers (#2A)</name>
  <files>brave/lanes/destinos/mtur.py, brave/lanes/destinos/desmembramento.py, tests/integration/test_sweep_uf.py</files>
  <behavior>
    - Test: when process_nascente_record raises for exactly ONE record in a BA sweep, the
      remaining records are still persisted (SAVEPOINT rollback scoped to the bad record only)
    - Test: the failing record produces a PoisonQuarantine entry with task_name "brave.sweep_uf"
    - Test: the bad record is NOT present in RioRecord (its savepoint was rolled back)
  </behavior>
  <action>
    In brave/lanes/destinos/mtur.py:

    1. Add import at the top: `from brave.core.quarantine import quarantine_poison`

    2. In `MturSeedIngest.produce()`, wrap each iteration of the municipalities loop in a
       per-record SAVEPOINT. Replace the current unprotected:
         nascente = store_raw(session=self._session, ...)
         if run_rio: process_nascente_record(session=self._session, ...)
       with:
         sp = self._session.begin_nested()
         try:
             nascente = store_raw(session=self._session, ...)
             if run_rio:
                 process_nascente_record(session=self._session, nascente=nascente, config=self._config)
             sp.commit()
         except Exception as exc:
             sp.rollback()
             quarantine_poison(
                 session=self._session,
                 nascente_id=None,
                 task_name="brave.sweep_uf",
                 error=str(exc),
                 payload={"source_ref": source_ref, "uf": uf},
             )

       After sp.rollback(), quarantine_poison writes to the outer transaction (safe — the outer
       session transaction is intact; only the savepoint was rolled back). The quarantine row is
       committed when sweep_uf's session.commit() fires.

       NOTE: the `quarantine_poison` call uses `self._session` directly. Do not create a new
       session — the whole point is that quarantine rows accumulate in the outer transaction.

    In brave/lanes/destinos/desmembramento.py:

    Wrap each iteration of the `for destino in result.destinos:` inner loop (the `store_raw` call,
    lines ~198-238) in a per-item SAVEPOINT. The per-município LLM try/except (lines ~169-187) is
    unchanged — it already handles LLM failures at the right granularity. Only add SAVEPOINT around
    the `store_raw(...)` call inside the successful LLM path:
         sp = self._session.begin_nested()
         try:
             store_raw(session=self._session, ...)
             sp.commit()
         except Exception as exc:
             sp.rollback()
             quarantine_poison(
                 session=self._session,
                 nascente_id=None,
                 task_name="brave.desmembramento",
                 error=str(exc),
                 payload={"source_ref": source_ref},
             )

    In tests/integration/test_sweep_uf.py:

    Add ONE new integration test `test_sweep_uf_bad_record_doesnt_discard_good_ones` after the
    existing `test_sweep_uf_quarantines_poison` test. Use the existing `isolated_session` fixture
    and the `_NoDispose` class from the same file.

    Strategy: patch `brave.lanes.destinos.mtur.process_nascente_record` to raise RuntimeError on
    the FIRST call only (use a call_count list [0] in a closure; increment before checking). All
    subsequent calls delegate to the real function (import from brave.core.rio.routing before
    patching, store as `_real_fn`). Then run `pipeline.sweep_uf.run("BA")`. Assert:
      1. At least one RioRecord exists for BA (good records committed)
      2. At least one PoisonQuarantine row exists with task_name="brave.sweep_uf" (bad record quarantined)

    Mark @pytest.mark.integration. This test requires docker-compose Postgres (same as existing
    tests in this file). Import PoisonQuarantine from brave.core.models.
  </action>
  <verify>
    <automated>BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/unit/test_mtur_lane.py tests/unit/lanes/ -x -q 2>&1 | tail -5</automated>
  </verify>
  <done>
    Offline unit tests for mtur lane still pass. Integration test added (will run with BRAVE_DB_URL set).
    mtur.py and desmembramento.py both contain `begin_nested()` in their produce loops.
    The existing test_sweep_uf_quarantines_poison test still passes (whole-CSV-missing path unchanged).
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: commit-before-dispatch (WR-01) in dlq.py validate paths (#2B)</name>
  <files>brave/api/routers/dlq.py, tests/integration/test_destinos_lane.py</files>
  <behavior>
    - New contract (WR-01): after PATCH /api/v1/dlq/{id}/validate with run_real_externals=True and
      broker-down, response is 503 AND record is in "mar" routing (promotion committed before dispatch)
    - New contract (WR-01 batch): after POST /api/v1/dlq/validate-batch with broker-down, first
      committed record stays "mar" (per-row commit), subsequent unprocessed record stays "dlq"
    - Offline (run_real_externals=False): broker-down push is still swallowed, 202, record in "mar"
  </behavior>
  <action>
    In brave/api/routers/dlq.py, apply WR-01 to BOTH validate paths:

    SINGLE VALIDATE (validate_destination function, ~line 155):
    Reorder to: validate_and_promote_rio(db, rio) → db.refresh(rio) → write_audit(...) →
    db.commit() [WR-01] → db.refresh(rio) → [if routing=="mar": push dispatch] → return.

    The write_audit call moves BEFORE db.commit() so audit is committed atomically with the
    promotion (matching cms.py lines 326-342). Add a comment: "# WR-01: commit audit + promotion
    BEFORE dispatch — mirrors cms.py:342. Worker's own session must see the committed record."

    After db.commit() + db.refresh(rio), the push dispatch block stays structurally identical
    (try/except + run_real_externals guard + HTTPException 503) but fires only after the commit.
    The broker-down 503 path: promotion is already committed, so the 503 tells the steward to
    retry the push, not re-do the promotion.

    BATCH VALIDATE (validate_batch function, ~line 222):
    Same pattern per-row inside the for loop: validate_and_promote_rio(db, rio) → db.refresh(rio) →
    write_audit(...) → db.commit() [WR-01] → db.refresh(rio) → [if routing=="mar": push dispatch] →
    validated++.

    Per-row commit means a later-row dispatch failure (503) cannot roll back already-committed rows.
    Add comment: "# WR-01 per-row: commit before dispatch; a later dispatch failure cannot roll back
    this row. Semantics: partial batch on broker-down, retryable (idempotent validate)."

    In tests/integration/test_destinos_lane.py, UPDATE two existing tests to match WR-01 semantics:

    test_validate_returns_503_when_push_fails_under_real_externals (~line 278):
      Change assertion `reloaded.routing == "dlq"` to `reloaded.routing == "mar"`.
      Change `assert audit is None` to `assert audit is not None`.
      Update the docstring: "Promotion is committed (WR-01) before dispatch. A broker-down push
      returns 503 so the steward knows to retry the push, but the record IS in Mar."

    test_validate_batch_returns_503_when_push_fails_under_real_externals (~line 348):
      The test creates rio_a and rio_b for "PE". After per-row commit, rio_a is committed to "mar"
      before dispatch fails; rio_b is never processed. Change assertions to:
        assert r.status_code == 503  (unchanged)
        db_session.expire_all()
        assert db_session.get(RioRecord, rio_a.id).routing == "mar"   (first row committed)
        assert db_session.get(RioRecord, rio_b.id).routing == "dlq"   (second row not reached)
      Update docstring: "WR-01 per-row commit: first record is committed to Mar before dispatch
      fails; second record is unprocessed. Batch is partially promoted and retryable."

    Do NOT add new tests for offline paths — test_validate_swallows_push_failure_offline and
    test_validate_batch_returns_202 are unaffected and must remain green.
  </action>
  <verify>
    <automated>BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/integration/test_destinos_lane.py -x -q -k "not notebooklm" 2>&1 | tail -10</automated>
  </verify>
  <done>
    dlq.py both validate paths have db.commit() before push dispatch. The two updated tests assert
    WR-01 semantics (record in "mar" on 503, not rolled back). All other dlq tests pass.
    Offline push-failure tests (202 paths) unaffected.
  </done>
</task>

<task type="auto">
  <name>Task 4: reset-brave-db Celery broker queue purge (#skill)</name>
  <files>.claude/skills/reset-brave-db/scripts/reset_db.py, .claude/skills/reset-brave-db/SKILL.md</files>
  <action>
    In .claude/skills/reset-brave-db/scripts/reset_db.py:

    1. Add `--no-broker-purge` argument to the argparse block:
         ap.add_argument("--no-broker-purge", action="store_true",
                         help="skip Celery broker queue purge (Postgres + brave:* flush still run)")

    2. After the Redis flush section (the block ending with the "Redis flush" print, ~line 160),
       add a Celery broker purge section. Gate: skip when `args.no_redis` OR `args.no_broker_purge`.

    Broker purge implementation (within the same redis client `r` from the flush section):
       - Scan+delete keys matching "celery" (the main Celery task queue list key)
       - Scan+delete keys matching "_kombu*" (Kombu binding/unacked metadata)
       Pattern: use `r.scan_iter(match="celery", count=100)` + `r.scan_iter(match="_kombu*", count=100)`
       for each: keys = list(scan_iter); deleted = r.delete(*keys) if keys else 0
       Print: "Celery broker purge (celery + _kombu*): {n_celery} task(s), {n_kombu} kombu key(s) deleted."

       Safety: only deletes "celery" exact key and "_kombu*" prefix — never FLUSHALL, never brave:*
       (already handled above), never Postgres. No data is lost except pending Celery tasks.

    3. Print a trailing note only when broker purge ran:
       "  Stale queued tasks cleared — restart workers to begin fresh."

    In .claude/skills/reset-brave-db/SKILL.md:

    Update the "How to run" section to document the new behavior: by DEFAULT, reset_db.py now
    also purges the Celery broker queue (celery list + _kombu* keys). Add:
    - A note under the header: "By default, the broker queue (pending Celery tasks) is also
      purged to prevent stale tasks from re-firing after a reset."
    - New variant in the "Useful variants" block:
        # Skip broker purge (keep queued tasks — rarely needed):
        .venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes --no-broker-purge
    - Mention the safety scope: "only the Celery task queue list and Kombu metadata keys are
      deleted — never FLUSHALL, never the brave:* engine/session keys (those are the Redis flush
      step above)."

    Add to the "After a reset — cold-start note" section: "Stale queued tasks (e.g., old
    push_destination referencing reset-away rio_ids) are cleared by the broker purge step.
    If workers were running, restart them after reset so they pick up fresh tasks only."
  </action>
  <verify>
    <automated>BRAVE_USE_FAKEREDIS=1 .venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --help 2>&1 | grep -c "no-broker-purge"</automated>
  </verify>
  <done>
    --help shows --no-broker-purge flag. Script prints "Celery broker purge" section when run
    without --no-broker-purge. SKILL.md documents the new default behavior and --no-broker-purge variant.
    Existing --no-redis and --yes flags are unchanged.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Celery task → PostgreSQL session | Each task opens its own session; dispatch-before-commit race crosses this boundary (T3 fix) |
| FastAPI handler → Celery broker | HTTPException 503 must not leave a committed-Mar record with no push retry signal |
| reset-brave-db CLI → Redis broker | Purge must be scoped to broker keys only, never FLUSHALL |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-pfr-01 | Tampering | mtur.py SAVEPOINT quarantine — bad-record payload logged to PoisonQuarantine | mitigate | payload dict contains only source_ref (not user content); no PII; already existing quarantine pattern |
| T-pfr-02 | Denial | sweep_uf: per-record quarantine on ALL records could fill PoisonQuarantine silently | accept | existing monitor endpoint surfaces PoisonQuarantine count; low probability (Mtur CSV is static) |
| T-pfr-03 | Elevation | reset_db.py broker purge: deletes all pending Celery tasks on the same Redis instance | mitigate | scoped to "celery" list + "_kombu*" prefix; --no-broker-purge escape hatch; never FLUSHALL |
| T-pfr-SC | Tampering | No new npm/pip/cargo installs in this plan | accept | no package installs; no legitimacy audit needed |
</threat_model>

<verification>
Full offline suite must remain green after all 4 tasks complete:

```
BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest --tb=short -q 2>&1 | tail -5
```

Expected: ~892+ tests passing, 0 failures.

Key per-fix checks:
- T1: `grep -n "ta_config=ta_config" brave/tasks/pipeline.py` returns at least 1 line in the per-UF constructor block
- T2: `grep -n "begin_nested" brave/lanes/destinos/mtur.py brave/lanes/destinos/desmembramento.py` returns lines in each file
- T3: `grep -n "db\.commit" brave/api/routers/dlq.py` returns at least 2 lines (single + batch), each appearing BEFORE the nearest `push_destination_task.delay` in reading order
- T4: `.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --help | grep no-broker-purge` exits 0

End-to-end acceptance (operator-run, post-execution):
1. Mtur seed UF → persists >0 RioRecords; single bad record quarantined without discarding the rest
2. TA atrativos sweep (run_real_externals=True) → atrativos call fetch_attraction_geo → geo+UF resolved without Nominatim fallback
3. dlq/validate with router='mar' → push dispatched → worker finds RioRecord in its own session (no "RioRecord not found")
4. reset-brave-db --yes → output includes "Celery broker purge" line with deleted count
</verification>

<success_criteria>
- brave/tasks/pipeline.py: ta_config is initialized to None before the if run_real_externals block and passed as ta_config=ta_config to the per-UF TripAdvisorAtrativosIngest constructor
- brave/lanes/destinos/mtur.py: produce() loop wraps each record in session.begin_nested() + per-record quarantine on except
- brave/lanes/destinos/desmembramento.py: inner store_raw loop wraps each destino item in session.begin_nested() + per-item quarantine on except
- brave/api/routers/dlq.py: both validate_destination and validate_batch call db.commit() before push_destination_task.delay(); two existing tests updated to assert WR-01 semantics
- reset_db.py: --no-broker-purge flag added; broker purge section scoped to "celery" + "_kombu*" keys; SKILL.md updated
- Full offline suite: BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest passes with 0 failures
</success_criteria>

<output>
Create `.planning/quick/260630-pfr-pipeline-robustness-fixes-wire-ta-config/260630-pfr-SUMMARY.md` when done
</output>
