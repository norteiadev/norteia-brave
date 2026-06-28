---
phase: 260628-jvk
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - brave/core/engine.py
  - brave/api/routers/engine.py
  - tests/unit/test_engine_state.py
  - tests/unit/api/test_engine_latch.py
  - dashboard/lib/engine-api.ts
  - dashboard/mocks/handlers/engine.ts
  - dashboard/components/painel/PainelTopbar.tsx
  - dashboard/components/engine/EngineControl.tsx
  - dashboard/components/painel/__tests__/PainelTopbar.test.tsx
  - dashboard/components/engine/__tests__/EngineControl.test.tsx
autonomous: true
requirements: [engine-toggle-persistence]

must_haves:
  truths:
    - "Motor switch stays ON after page refresh when the operator started the engine"
    - "Motor switch turns OFF only when the operator explicitly clicks stop"
    - "POST /stop clears the enabled latch even when dispatch state is already idle"
    - "GET /status always returns an `enabled` boolean field"
    - "EngineControl shows the stop button whenever enabled=true (including when state=idle)"
  artifacts:
    - path: brave/core/engine.py
      provides: "_ENABLED_KEY, set_enabled, is_enabled; get_status includes enabled; start_run sets enabled=True; mark_idle does NOT clear enabled"
    - path: brave/api/routers/engine.py
      provides: "/stop always calls set_enabled(redis, False) before returning"
    - path: dashboard/lib/engine-api.ts
      provides: "EngineStatus.enabled: boolean"
    - path: dashboard/components/painel/PainelTopbar.tsx
      provides: "motorOn = data?.enabled ?? (state !== 'idle'); onToggleMotor branches on motorOn not state"
    - path: dashboard/components/engine/EngineControl.tsx
      provides: "start/stop button branch keyed off enabled not state === 'idle'"
  key_links:
    - from: brave/core/engine.py
      to: brave/api/routers/engine.py
      via: "set_enabled called in start_run (implicit via router) and explicitly in engine_stop"
      pattern: "set_enabled"
    - from: dashboard/lib/engine-api.ts
      to: dashboard/components/painel/PainelTopbar.tsx
      via: "EngineStatus.enabled consumed as motorOn"
      pattern: "data\\.enabled"
---

<objective>
Fix the engine toggle persistence bug: the dashboard motor switch reverts to OFF on page
refresh because `motorOn` is derived from the transient `state` field, which returns to
`idle` the moment `engine_sweep_run` finishes dispatching all UFs (even though workers
continue processing). The fix adds a permanent operator-intent latch (`enabled`) in Redis
that is set on /start, never cleared by `mark_idle`, and only cleared on /stop.

Purpose: The operator's "turn the engine on/off" intent must survive the dispatch-lifecycle
idle transition. `enabled` is the latch; `state` remains the dispatch-lifecycle signal.

Output: Backend latch in engine.py + router fix + backend unit tests; dashboard EngineStatus
type extended; PainelTopbar and EngineControl toggle driven from `enabled`; dashboard tests
updated and new assertions added.
</objective>

<execution_context>
@/Users/leandro/.claude/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
@.planning/STATE.md
@.planning/ROADMAP.md
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Backend — add enabled latch to engine.py, fix /stop router, add unit tests</name>
  <files>
    brave/core/engine.py,
    brave/api/routers/engine.py,
    tests/unit/test_engine_state.py,
    tests/unit/api/test_engine_latch.py
  </files>
  <behavior>
    - is_enabled returns False on fresh redis (absent key)
    - set_enabled(redis, True) then is_enabled returns True
    - set_enabled(redis, False) then is_enabled returns False
    - start_run sets enabled=True (verify via is_enabled after start_run)
    - mark_idle does NOT clear enabled (is_enabled True after start_run → mark_idle)
    - get_status includes key "enabled" with the correct boolean
    - POST /stop when engine is idle still returns 202 and clears enabled to False
    - POST /stop when engine is running clears enabled=False AND returns 202 "stopping"
    - POST /start sets enabled=True (verifiable via GET /status)
  </behavior>
  <action>
    **brave/core/engine.py** — add below the existing _SOURCE_KEY constant:

    Add `_ENABLED_KEY = "brave:engine:enabled"`.

    Add function `set_enabled(redis: Any, enabled: bool) -> None` that writes
    `redis.set(_ENABLED_KEY, "1" if enabled else "0")`.

    Add function `is_enabled(redis: Any) -> bool` that reads the key; absent or any
    non-"1" value → False; value == "1" → True.

    In `start_run`: after `redis.set(_STATE_KEY, RUNNING)` add
    `redis.set(_ENABLED_KEY, "1")`. Do NOT add any enabled call to `mark_idle` —
    the latch is independent of the dispatch lifecycle.

    In `get_status`: add `"enabled": is_enabled(redis)` to the returned dict
    (alongside the existing state/current_uf/... fields).

    **brave/api/routers/engine.py** — update `engine_stop`:

    The current logic returns early with "noop" when `request_stop` returns False
    (engine already idle). After the fix: always call
    `collection_engine.set_enabled(redis, False)` regardless of whether
    `request_stop` succeeded. Keep the conditional return for the status text:
    - `request_stop` returned True → was running, return `{"status": "stopping"}`
    - `request_stop` returned False → was idle, return
      `{"status": "noop", "detail": "engine is not running"}` (body unchanged so
      existing dashboard callers are unaffected — the latch clearing is the real
      side effect)

    **tests/unit/test_engine_state.py** — add at the end a section titled
    `# --- Enabled latch (operator intent) ---` with tests matching the behavior
    block above (is_enabled defaults False, set_enabled round-trip, start_run
    sets enabled, mark_idle does NOT clear it, get_status carries it).

    **tests/unit/api/test_engine_latch.py** — NEW file, mirrors the setup
    pattern from tests/unit/api/test_engine_source.py (fakeredis, monkeypatched
    engine_sweep_run.delay, MagicMock DB override). Add:
    - `test_stop_when_idle_clears_enabled`: POST /start, force state back to IDLE
      via `collection_engine.mark_idle(rc)`, POST /stop → assert 202, assert
      `collection_engine.is_enabled(rc)` is False.
    - `test_stop_when_running_clears_enabled`: POST /start, state is RUNNING,
      POST /stop → assert 202 "stopping", assert `is_enabled(rc)` is False.
    - `test_start_sets_enabled`: POST /start with valid depth → assert 202,
      assert `is_enabled(rc)` is True.
    - `test_status_includes_enabled_field`: POST /start, GET /status → body
      contains key "enabled" with value True.
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest tests/unit/test_engine_state.py tests/unit/api/test_engine_latch.py -x -q</automated>
  </verify>
  <done>
    All new and existing engine state tests pass. `get_status` returns `enabled`.
    `mark_idle` does not affect `enabled`. `/stop` clears the latch even when idle.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Dashboard — wire enabled to toggle, update MSW handler, update tests</name>
  <files>
    dashboard/lib/engine-api.ts,
    dashboard/mocks/handlers/engine.ts,
    dashboard/components/painel/PainelTopbar.tsx,
    dashboard/components/engine/EngineControl.tsx,
    dashboard/components/painel/__tests__/PainelTopbar.test.tsx,
    dashboard/components/engine/__tests__/EngineControl.test.tsx
  </files>
  <behavior>
    - PainelTopbar: motorOn = data?.enabled ?? (state !== "idle"); switch aria-checked reflects enabled
    - PainelTopbar: when enabled=true and state=idle, motor label shows "Ligado" (not "Desligado")
    - PainelTopbar: onToggleMotor branches on motorOn (enabled) not on state === "idle"
    - PainelTopbar: enabled=true, state=idle — clicking toggle fires POST /stop (not depth menu)
    - EngineControl: start/stop button branch keyed off enabled (not state === "idle")
    - EngineControl: enabled=true state=idle → stop button visible, not depth selectors
    - Existing tests continue to pass (with enabled field added to status overrides)
    - New test: enabled=true state=idle → switch ON and stop fires
    - New test: enabled=false state=idle → switch OFF and depth menu opens
  </behavior>
  <action>
    **dashboard/lib/engine-api.ts** — in the `EngineStatus` interface add field
    `enabled: boolean` after the existing `source` field.

    **dashboard/mocks/handlers/engine.ts** — in the `engineStatus()` factory,
    add `enabled: false` to the default status object so all existing tests that
    call `engineStatus()` with no overrides get a valid shape. Tests that
    represent running/enabled state must pass `enabled: true` in their overrides.

    **dashboard/components/painel/PainelTopbar.tsx** — change line:
    `const motorOn = state !== "idle";`
    to:
    `const motorOn = data?.enabled ?? (state !== "idle");`

    Change `onToggleMotor` to branch on `motorOn` instead of `state === "idle"`:
    - `if (motorOn)` → call `stop.mutate()` (previously `else` branch)
    - `else` → open depth menu (previously `if (state === "idle")` branch)

    Update the motor label text in the JSX: replace the bare `STATE_LABEL[state]`
    expression with a derived label so that `enabled=true + state=idle` renders
    "Ligado" rather than "Desligado". Define inside the component:
    `const motorLabel = motorOn ? (state === "stopping" ? "Parando…" : "Ligado") : "Desligado";`
    and use `motorLabel` in the `data-testid="painel-motor-state"` span instead of
    `STATE_LABEL[state]`.

    **dashboard/components/engine/EngineControl.tsx** — derive enabled from status:
    `const enabled = data?.enabled ?? false;`
    Change the JSX branch condition from `state === "idle"` to `!enabled` (the outer
    ternary that renders depth selectors vs the stop button). The stop button's own
    disabled/text logic (`state === "stopping"`) is unchanged — it still correctly
    reflects dispatch state for the button label and disabled state.

    **dashboard/mocks/handlers/engine.ts** — no additional change beyond the
    `enabled: false` default above. Tests that use `engineStatus({ state: "running" })`
    should add `enabled: true` to their overrides where the test exercises the toggle.

    **dashboard/components/painel/__tests__/PainelTopbar.test.tsx** — update:
    - The test "motor switch reflects running state (aria-checked=true) and toggling
      calls stop": change `engineStatus({ state: "running" })` to
      `engineStatus({ state: "running", enabled: true })`.
    - The test "idle toggle opens the depth menu": keep `enabled: false` (default) —
      no change needed there since default is false.
    - Add NEW test: "enabled=true with state=idle keeps switch ON and stop fires":
      use `engineStatus({ state: "idle", enabled: true })`, assert
      `aria-checked="true"`, click switch, assert POST /stop is called (not depth menu).
    - Add NEW test: "enabled=false with state=idle toggle opens depth menu": use
      `engineStatus({ state: "idle", enabled: false })`, click switch, assert
      `painel-depth-menu` appears (no stop call).

    **dashboard/components/engine/__tests__/EngineControl.test.tsx** — update:
    - In `buildStatus`, add `enabled: false` to the default shape.
    - In the "running state shows ... Parar motor" test: add `enabled: true` to the
      engineStatus override.
    - In the "start → posts and refetches status (now running)" test: the post-start
      `buildStatus({ state: "running", ... })` override should include `enabled: true`.
    - In the "sends the selected depth..." test: same pattern.
    - Add NEW test: "enabled=true state=idle shows stop button (not start controls)":
      use `engineStatus({ state: "idle", enabled: true })`, assert
      `engine-stop` is present and `engine-start` is absent.
  </action>
  <verify>
    <automated>cd dashboard && bun run test --reporter=verbose 2>&1 | tail -30</automated>
  </verify>
  <done>
    All existing dashboard tests pass. New assertions: enabled=true+state=idle
    renders stop button in EngineControl; PainelTopbar switch stays ON when
    enabled=true regardless of state; POST /stop fires when toggle clicked
    in enabled=true+state=idle scenario.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| operator→/stop | Operator calls /stop when engine is idle to clear enabled latch; no auth change |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-jvk-01 | Tampering | brave:engine:enabled Redis key | accept | Same trust boundary as existing state key; Redis is internal-only behind the API auth layer |
| T-jvk-02 | Spoofing | POST /stop by unauthenticated caller | accept | Already protected by require_steward_or_bearer (unchanged) |
</threat_model>

<verification>
Backend: `.venv/bin/python -m pytest tests/unit/test_engine_state.py tests/unit/api/test_engine_latch.py -x -q`
Dashboard: `cd dashboard && bun run test`
Full suite smoke: `.venv/bin/python -m pytest tests/unit/ -x -q --ignore=tests/unit/api/test_engine_source.py` (existing tests must not regress)
</verification>

<success_criteria>
- `get_status(redis)["enabled"]` is False on fresh Redis, True after start_run, True after mark_idle
- `engine_stop` clears enabled even when called on an idle engine
- Dashboard `motorOn` persists across poll cycles when `enabled=true` regardless of `state`
- PainelTopbar shows "Ligado" when `enabled=true` and `state=idle`
- EngineControl shows stop button (not depth selectors) when `enabled=true` and `state=idle`
- All existing tests pass without modification (except adding `enabled` field to test status overrides)
</success_criteria>

<output>
Create `.planning/quick/260628-jvk-fix-engine-toggle-persistence-add-operat/260628-jvk-SUMMARY.md` when done
</output>
