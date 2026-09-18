---
phase: quick-260918-ohm
plan: 01
subsystem: brave-core, brave-clients, brave-lanes-atrativos, brave-tasks, dashboard-painel
tags: [provider-balance-wall, engine-pause, cost-guard, painel-topbar]
requires: []
provides:
  - ProviderBalanceError + raise_if_balance_wall (brave/shared/exceptions.py)
  - engine.pause_with_reason / get_pause_reason (brave/core/engine.py)
  - EngineStatus.pause_reason (dashboard/lib/engine-api.ts)
affects:
  - brave/clients/{parallel,tavily,llm,places}.py
  - brave/lanes/atrativos/{copywriter,places_enrichment}.py
  - brave/tasks/pipeline.py (describe_uf, enrich_places_task, sweep_tripadvisor)
  - brave/domains/tripadvisor/atrativos.py
  - dashboard/components/painel/PainelTopbar.tsx
tech-stack:
  added: []
  patterns:
    - "Shared classifier (raise_if_balance_wall) instead of per-client billing-wall detection"
    - "Reasoned pause (pause_with_reason) as a distinct Redis payload from plain PAUSADO"
key-files:
  created:
    - tests/unit/test_provider_balance_error.py
    - tests/unit/test_engine_pause_reason.py
    - tests/unit/tasks/test_describe_uf_balance_pause.py
  modified:
    - brave/shared/exceptions.py
    - brave/clients/parallel.py
    - brave/clients/tavily.py
    - brave/clients/llm.py
    - brave/clients/places.py
    - brave/lanes/atrativos/copywriter.py
    - brave/lanes/atrativos/places_enrichment.py
    - brave/core/engine.py
    - brave/tasks/pipeline.py
    - brave/domains/tripadvisor/atrativos.py
    - brave/config/settings.py
    - dashboard/lib/engine-api.ts
    - dashboard/components/painel/PainelTopbar.tsx
    - dashboard/mocks/handlers/engine.ts
    - dashboard/components/painel/__tests__/PainelTopbar.test.tsx
    - tests/unit/lanes/test_copy_batch.py
decisions:
  - "Google Places ResourceExhausted stays retryable in _is_retryable (orchestrator amendment) — it covers both a per-minute rate burst and the daily quota wall, indistinguishable by exception type; only classified as ProviderBalanceError after tenacity's retries are exhausted, or immediately on a billing-flavored 403 PermissionDenied."
  - "LLMConfig.usd_daily_budget default raised 10.0 -> 50.0 per plan; the 2 test_copy_batch.py assertions pinned to the bare default were repointed to an explicit LLMConfig(usd_daily_budget=10.0) since their own arithmetic (not this feature) depends on that number."
metrics:
  duration: ~70min
  completed: 2026-09-18
---

# Quick Task 260918-ohm: Pause motor on provider balance wall Summary

Pauses the Brave motor (mode=PAUSADO, with a visible reason) whenever a paid
provider (Parallel/Tavily/OpenRouter/Anthropic/Gemini/Google Places) reports a
billing wall, instead of letting the error be silently absorbed by the existing
degrade-to-floor exception handling — closing the risk of burning
`descricao_attempts` or stamping `google_enriched` on a record that never got a
real attempt. The internal daily cost-guard trip now pauses with a reason
(`daily_budget`) instead of silently completing the run. The Painel dashboard
shows a red banner with a Continuar button that resumes the same paused action
after a manual confirm.

## What Was Built

**Task 1 — `ProviderBalanceError` + client classification + lane propagation**
(commit `422ed1a`)

- `brave/shared/exceptions.py`: `ProviderBalanceError(BraveError)` carrying
  `provider: str`, plus the shared `raise_if_balance_wall(provider, *,
  status_code=None, message="")` classifier (402/432/433 status codes, or a
  "credit balance is too low" / "insufficient_quota" / "billing" message
  marker).
- Wired into every paid client call site:
  - `brave/clients/tavily.py` and `brave/clients/parallel.py`: `_post` now
    classifies `httpx.HTTPStatusError` before re-raising.
  - `brave/clients/llm.py`: `extract()`'s slug-fallback loop (OpenRouter 402
    via `openai.APIStatusError`), `_generate_openrouter` (new try/except
    around `_openrouter_completion`), a new `_anthropic_create()` wrapper
    covering **both** the initial `messages.create` call and the
    `pause_turn` resume loop, and `_generate_gemini`'s standard-tier call.
  - `brave/clients/places.py`: `text_search`/`place_details` classify in
    their existing `except Exception as exc:` blocks via a new
    `_raise_if_places_balance_wall(exc)` helper.
    **Orchestrator amendment applied**: `ResourceExhausted` was **kept**
    retryable in `_is_retryable` (it also covers a per-minute rate burst) —
    it is classified as a balance wall only once tenacity's retries are
    exhausted and it's still the exception leaving the client method. A 403
    `PermissionDenied` whose message mentions "billing" is classified
    immediately (no retry would ever fix it).
- `brave/lanes/atrativos/copywriter.py` (`write_cascade` x2, `write` x1) and
  `brave/lanes/atrativos/places_enrichment.py` (`locate`,
  `write_description`, the inline `run()` Places block) each gained
  `except ProviderBalanceError: raise` immediately before their existing
  `except CostGuardError:` / broad `except Exception:` — the error now
  escapes uncaught instead of degrading to the TA floor or the
  `CostGuardError` no-spend tuple.

**Task 2 — Engine pause-with-reason + pipeline task halts + budget default**
(commit `c2bf7e2`)

- `brave/core/engine.py`: `_PAUSE_REASON_KEY`, `pause_with_reason(redis,
  reason, provider=None, *, action=None)` (writes a JSON payload — reason,
  provider, action, ISO `at` — then `set_mode(PAUSADO)`), `get_pause_reason`.
  `set_mode(LIGADO)` now also deletes `_PAUSE_REASON_KEY` (covers both resume
  paths — POST `/engine/start` and POST `/engine/mode LIGADO` — with no
  separate clear-on-resume code). `get_status` exposes `pause_reason`.
  `maybe_complete` now refuses to complete a run while a pause reason is set
  (the DESLIGADO/"synced" side effects must never fire on a reasoned pause).
- `brave/tasks/pipeline.py`: 4 sites wired.
  - `_describe_chunk`'s inner `_fetch` re-raises `ProviderBalanceError`
    before the catch-all, letting it escape `asyncio.gather`.
  - `describe_uf` catches it around `asyncio.run(_describe_chunk(...))`,
    calls `pause_with_reason(rc, "provider_balance", exc.provider,
    action="describe")`, and returns (no self-chain, no retry; the `finally`
    still runs `_producer_finally_lifecycle` exactly once since `chained`
    stays `False`).
  - `describe_uf`'s internal `_stop` cost-guard branch now also calls
    `pause_with_reason(rc, "daily_budget", action="describe")` when
    `CostGuardError` trips, instead of silently halting the chunk.
  - `enrich_places_task` gained a new `except ProviderBalanceError as exc:`
    clause (between `PermanentError` and the generic retry/quarantine
    `except Exception:`) that pauses with a reason — no retry, no
    quarantine.
  - `sweep_tripadvisor`'s TA per-UF producer gained a sibling
    `except ProviderBalanceError as exc:` next to the existing R1
    `SessionMissingError/SessionExpiredError` block — pauses via
    `pause_with_reason` (PAUSADO with a reason), **not** R1's hard
    `DESLIGADO`.
- `brave/domains/tripadvisor/atrativos.py`: the per-attraction loop's
  `except Exception as exc:` (quarantine path) gained a preceding
  `except ProviderBalanceError: raise` so the error escapes `produce()`
  entirely instead of being quarantined as a bad record.
- `brave/config/settings.py`: `LLMConfig.usd_daily_budget` default `10.0` ->
  `50.0`.

**Task 3 — Dashboard pause notice + Continuar resume flow** (commit `04dfb2c`)

- `dashboard/lib/engine-api.ts`: `EngineStatus.pause_reason: { reason:
  string; provider: string | null; action: string | null; at: string } |
  null`.
- `dashboard/components/painel/PainelTopbar.tsx`: a red banner
  (`var(--status-dlq)`) renders whenever `data.pause_reason` is non-null,
  with a PT-BR label (`pauseReasonLabel`: "Motor pausado: sem saldo
  (tavily)" for `provider_balance`, "Motor pausado: orçamento diário
  atingido" for `daily_budget`) and a Continuar button. Continuar runs a
  native `window.confirm("Saldo recarregado?")`; on confirm, rebuilds the
  start body from the server-polled `pause_reason.action` ("describe" ->
  `{action:"describe"}"`, "sweep"/other -> `{action:"sweep", depth:
  data.depth ?? "nascente_rio"}`) — never trusts client-held depth state.
  Cancel leaves the banner as-is (no mutation fired).
- `dashboard/mocks/handlers/engine.ts`: `engineStatus()` fixture defaults
  `pause_reason: null`.

**Post-verification fix** (commit `7aef558`)

Running the full offline suite surfaced 2 pre-existing tests in
`tests/unit/lanes/test_copy_batch.py` that hardcoded arithmetic against
`LLMConfig()`'s bare `usd_daily_budget` default (the docstring literally says
"$10 default"). Pinned both to `LLMConfig(usd_daily_budget=10.0)` — their own
budget-shrink math is unrelated to this task, only the implicit default
shifted under them.

## Test Commands Run

```
env -u RUN_REAL_EXTERNALS BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/unit -q
```
Result: **exit 0, 0 failures** (full offline unit suite; RUN_REAL_EXTERNALS
unset, no real external calls).

```
cd dashboard && bun run test
```
Result: **27 test files, 222/222 tests passed** (incl. 39 in
`PainelTopbar.test.tsx`, up from 33 — 6 new pause-banner/Continuar tests).

Targeted task-level runs during execution (all passed, 0 failures):
- `tests/unit/test_provider_balance_error.py` (new, 8 tests)
- `tests/unit/test_engine_pause_reason.py` (new, 3 tests)
- `tests/unit/tasks/test_describe_uf_balance_pause.py` (new, 2 tests)
- `tests/unit/clients/test_parallel_client.py`,
  `tests/unit/clients/test_real_llm_client.py`,
  `tests/unit/clients/test_real_places_client.py`, `tests/unit/lanes` (487
  tests, Task 1 targeted set)
- `tests/unit/tasks/test_describe_uf.py`, `tests/unit/test_engine_state.py`,
  `tests/unit/test_engine_mode_persist.py`, `tests/unit/tasks/test_sweep_tripadvisor.py`,
  `tests/unit/tasks/test_engine_sweep_mode.py`, `tests/unit/api` (Task 2
  regression set)

No pre-existing failures unrelated to this change remained after the
`test_copy_batch.py` budget-default fix.

## Deviations from Plan

### Orchestrator amendment (applied, not a deviation from instructions)

Google Places `ResourceExhausted` was kept retryable in `_is_retryable`
(the plan's original text said to remove it) per explicit orchestrator
amendment: it covers both a per-minute rate burst and the daily quota/billing
wall and the two are not distinguishable by exception type. Classification to
`ProviderBalanceError` happens only after tenacity's retries are exhausted
(the exception still leaving `text_search`/`place_details`), or immediately
for a 403 `PermissionDenied` whose message mentions billing. Task 1's
`<behavior>` test list was adjusted accordingly (no test asserts
`ResourceExhausted` is non-retryable).

### Auto-fixed Issues

**1. [Rule 1 - Bug] `test_copy_batch.py` budget-shrink tests pinned to the bare `LLMConfig()` default**
- **Found during:** Full offline suite verification, post-Task-3
- **Issue:** `test_batch_is_downsized_to_the_remaining_budget_instead_of_blocking`
  and `test_submit_skips_the_tick_when_the_budget_cannot_pay_for_one_description`
  constructed a bare `LLMConfig()` and asserted exact-dollar arithmetic against
  the (previously) $10.00 default. Raising the default to $50.00 (this task's
  own change) broke both — unrelated to the billing-wall feature itself.
- **Fix:** Pinned both call sites to `LLMConfig(usd_daily_budget=10.0)`,
  preserving the tests' original $10-budget arithmetic exactly.
- **Files modified:** `tests/unit/lanes/test_copy_batch.py`
- **Commit:** `7aef558`

**2. [Rule 3 - Blocking] Test-authoring bug: `describe_uf.run` captured after `describe_uf` was monkeypatched**
- **Found during:** Writing `tests/unit/tasks/test_describe_uf_balance_pause.py`
- **Issue:** First draft called `pipeline.describe_uf.run` for the real
  function AFTER `monkeypatch.setattr(pipeline, "describe_uf", chain)` had
  already replaced the module attribute with a `MagicMock` — so `.run` was a
  Mock's auto-attribute, silently no-op-ing the whole test.
- **Fix:** Reordered to capture `run = pipeline.describe_uf.run` BEFORE the
  monkeypatch swap (mirrors the existing pattern in `test_describe_uf.py`'s
  harness fixture).
- **Files modified:** `tests/unit/tasks/test_describe_uf_balance_pause.py`
  (test-authoring fix during the same commit, not a separate commit)

Lint: ruff flagged 2 pre-existing issues in `brave/tasks/pipeline.py`
(`E402`/`F841` at unrelated lines) and 2 in `brave/clients/{llm,places}.py`
(`SIM103`/`UP037`/`UP017`) that predate this task and are out of scope per the
scope-boundary rule — left untouched, not fixed.

## Threat Flags

None — the `<threat_model>` in the plan already covered the new surface
(pause-reason payload never leaks raw provider error text; Continuar echoes
server-polled depth/source, never client-held state).

## Known Stubs

None.

## Self-Check: PASSED

- `brave/shared/exceptions.py` — `class ProviderBalanceError` present: FOUND
- `brave/core/engine.py` — `pause_with_reason` present: FOUND
- `dashboard/lib/engine-api.ts` — `pause_reason` present: FOUND
- `dashboard/components/painel/PainelTopbar.tsx` — `pause_reason` present: FOUND
- Commits `422ed1a`, `c2bf7e2`, `04dfb2c`, `7aef558` all present in `git log --oneline`: FOUND

## Orchestrator post-review fix (ff066e2)

`maybe_complete` as planned returned False while a pause reason was set, leaving engine
state RUNNING forever — Continuar → `/engine/start` would 409 ("already running"). Fixed:
the run ends (idle, enabled off, runs_history finalized) and only the DESLIGADO mode flip is
skipped. Test renamed `test_maybe_complete_ends_run_but_keeps_reasoned_pause`; it now also
asserts `start_run` succeeds after the pause. Full `tests/unit` re-run: exit 0.
