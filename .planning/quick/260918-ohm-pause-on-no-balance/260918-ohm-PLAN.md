---
phase: quick-260918-ohm
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
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
  - brave/api/routers/engine.py
  - brave/config/settings.py
  - dashboard/lib/engine-api.ts
  - dashboard/components/painel/PainelTopbar.tsx
  - tests/unit/test_provider_balance_error.py
  - tests/unit/tasks/test_describe_uf_balance_pause.py
  - tests/unit/test_engine_pause_reason.py
  - dashboard/components/painel/__tests__/PainelTopbar.test.tsx
autonomous: true
requirements: [OHM-01, OHM-02, OHM-03, OHM-04, OHM-05, OHM-06]

must_haves:
  truths:
    - "A billing-wall response from any paid provider (Parallel/Tavily/OpenRouter/Anthropic/Gemini/Places) pauses the motor with mode=PAUSADO and a visible reason, instead of being swallowed as a normal failure"
    - "No descricao_attempts is burned and no google_enriched stamp is written when a provider balance wall interrupts an atrativo description or Places enrichment"
    - "The Painel dashboard shows a red notice with the pause reason and a Continuar button that, after a confirm step, resumes the same action"
    - "An internal daily cost-guard trip pauses the motor with reason=daily_budget instead of silently ending the run as completed"
  artifacts:
    - path: "brave/shared/exceptions.py"
      provides: "ProviderBalanceError(BraveError) carrying provider: str, plus the shared raise_if_balance_wall(provider, status_code=None, message='') classifier"
      contains: "class ProviderBalanceError"
    - path: "brave/core/engine.py"
      provides: "pause_with_reason(redis, reason, provider=None, action=None) writing brave:engine:pause_reason JSON + set_mode(PAUSADO); get_status exposes pause_reason; set_mode(LIGADO) clears it; maybe_complete refuses to complete a reasoned pause"
      contains: "pause_with_reason"
    - path: "dashboard/components/painel/PainelTopbar.tsx"
      provides: "Red pause notice + Continuar button with a confirm step, calling /engine/start with the paused action"
      contains: "pause_reason"
  key_links:
    - from: "brave/clients/*.py billing-wall responses"
      to: "brave/shared/exceptions.py ProviderBalanceError"
      via: "raise_if_balance_wall(provider, status_code=..., message=...)"
      pattern: "raise_if_balance_wall"
    - from: "brave/tasks/pipeline.py (describe_uf, enrich_places_task, TA R1 block)"
      to: "brave/core/engine.py pause_with_reason"
      via: "except ProviderBalanceError as exc: pause_with_reason(rc, provider_balance, exc.provider, action=...)"
      pattern: "pause_with_reason"
    - from: "dashboard PainelTopbar Continuar"
      to: "POST /api/v1/engine/start"
      via: "window.confirm then startEngine(...) with the persisted action"
      pattern: "pause_reason"
---

<objective>
Pausar o motor Brave (mode=PAUSADO), com motivo visível, sempre que um provedor pago
(Parallel, Tavily, OpenRouter, Anthropic, Gemini, Google Places) responder "sem saldo/quota"
— hoje esses erros são engolidos pelos `except Exception` de degradação (TA floor) e o motor
segue rodando gastando tentativas e enriquecimentos que nunca vão colar. O mesmo vale para o
cost guard interno diário: hoje ele termina o run silenciosamente como "completo"; deve pausar
com motivo em vez disso. O operador confirma a recarga pelo Painel e clica Continuar para
retomar a mesma ação (sweep|describe) de onde parou.

Purpose: Nenhum atrativo deve perder uma descricao_attempts nem ganhar google_enriched=True
por causa de uma parede de saldo — isso esgotaria o orçamento de tentativas do registro sem
nunca ter tentado de verdade. E o operador precisa de um sinal humano legível no Painel, não
um log silencioso.

Output: ProviderBalanceError tipado + classificador compartilhado, propagação através das
lanes (copywriter, places_enrichment), pausa com motivo no engine + tasks + API, e a UI de
retomada no Painel.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md

Key interfaces the executor needs — extracted to avoid codebase exploration.

From brave/shared/exceptions.py (current hierarchy — BraveError base, all others already
subclass it: TransientError, PermanentError, ComplianceError, CostGuardError, SourceError):
Add ProviderBalanceError(BraveError) with a required provider: str attribute:

    class ProviderBalanceError(BraveError):
        def __init__(self, provider: str, message: str = "") -> None:
            self.provider = provider
            super().__init__(message or f"{provider}: sem saldo/quota")

Add ONE shared classifier next to it (used by every client instead of per-client copies):

    _BALANCE_STATUS_CODES = frozenset({402, 432, 433})
    _BALANCE_MESSAGE_MARKERS = ("credit balance is too low", "insufficient_quota", "billing")

    def raise_if_balance_wall(provider: str, *, status_code: int | None = None, message: str = "") -> None:
        # status_code in _BALANCE_STATUS_CODES OR any marker in message.lower() -> raise ProviderBalanceError(provider)
        # else: return (no-op — caller's normal error handling continues)

Signals confirmed per client (read from source, 260918):
  - brave/clients/parallel.py _post (:109-118): raw httpx.HTTPStatusError from r.raise_for_status()
    (no per-client _is_retryable — it imports Tavily's, parallel.py:26). Wrap _post's call site
    (or _post itself) to catch httpx.HTTPStatusError and call
    raise_if_balance_wall("parallel", status_code=exc.response.status_code) before letting the
    original exception continue propagating (re-raise the original if not a balance wall — do not
    swallow retryable 429/5xx).
  - brave/clients/tavily.py _post (:114-127) — SAME pattern; _is_retryable (:64) already treats
    432/433 as non-retryable but does not raise a typed error. Same wrap: httpx.HTTPStatusError ->
    raise_if_balance_wall("tavily", status_code=...).
  - brave/clients/llm.py extract() (:262-320, DeepSeek/OpenRouter via instructor) and
    _generate_openrouter (:493-559): OpenRouter 402 surfaces as openai.APIStatusError (no specific
    subclass for 402 in the imported set at :33-42 — BadRequestError=400, PermissionDeniedError=403,
    RateLimitError=429, InternalServerError=5xx). Import APIStatusError from openai and, at the
    _call_slug/_openrouter_completion call sites, catch it and call
    raise_if_balance_wall("openrouter", status_code=getattr(exc, "status_code", None)).
  - brave/clients/llm.py Anthropic path in generate() (:358-482, self._anthropic_client.messages.create
    called twice — the initial call and the pause_turn resume loop): wrap both call sites (or factor
    a small _anthropic_create(**kwargs) helper mirroring _openrouter_completion) to catch
    anthropic.APIStatusError / anthropic.BadRequestError and call
    raise_if_balance_wall("anthropic", status_code=getattr(exc, "status_code", None), message=str(exc))
    — this is the "credit balance is too low" 400 case, caught by the message marker.
  - brave/clients/llm.py _generate_gemini (:585-693): the _gemini_post/_gemini_post_standard calls
    raise httpx.HTTPStatusError on non-2xx. Wrap the standard-tier call (the Flex path already falls
    back to standard on its own failure list) to catch it and call
    raise_if_balance_wall("gemini", status_code=exc.response.status_code, message=str(exc)) before
    re-raising anything that isn't a balance wall.
  - brave/clients/places.py text_search (:279-341) and place_details (:349-...): both already wrap
    their SDK call in try: ... except Exception as exc: logger.error(...); raise. In that except
    block, BEFORE the bare raise, classify: if "ResourceExhausted" in type(exc).__name__, or
    ("PermissionDenied" in type(exc).__name__ and "billing" in str(exc).lower()), call
    raise_if_balance_wall("google_places", message=str(exc)) — this raises ProviderBalanceError
    instead of the original. Otherwise raise unchanged.
    ALSO: in _is_retryable (:180-210), remove "ResourceExhausted" from the retryable branch (quota
    exhaustion must not retry) — leave TooManyRequests (plain rate limit) retryable, unchanged.

From brave/lanes/atrativos/copywriter.py (both already `except CostGuardError: raise` before a
degrading `except Exception`):
  - write_cascade (:248-317): TWO try/except blocks (search call, then LLM generate call). Add
    `except ProviderBalanceError: raise` immediately before EACH `except Exception:` (same position
    as the existing `except CostGuardError: raise`).
  - write (:319-352): ONE try/except (LLM generate call). Same addition.

From brave/lanes/atrativos/places_enrichment.py:
  - locate (:288-291, wraps self._places_client.text_search): currently a bare
    `except Exception: ... return None`. Add `except ProviderBalanceError: raise` before it.
  - write_description (:327-348): currently `except CostGuardError: return None, None, True`
    (no-spend signal). Add `except ProviderBalanceError: raise` — this must propagate, NOT return
    the no-spend tuple (a balance wall must halt the caller, not be read as "no attempt, keep going").
  - run (:429-449, inline Places match + details call): currently `except Exception: ... details = {}`.
    Add `except ProviderBalanceError: raise` before it.

From brave/core/engine.py (Redis-backed, pure state, no dispatch — module docstring :1-22):
Existing keys follow the brave:engine:* naming (`_MODE_KEY`, `_STATE_KEY`, etc). Add:

    _PAUSE_REASON_KEY = "brave:engine:pause_reason"

    def pause_with_reason(redis, reason: str, provider: str | None = None, action: str | None = None) -> None:
        # json.dumps({"reason": reason, "provider": provider, "action": action, "at": now-iso}) -> redis.set(_PAUSE_REASON_KEY, ...)
        # then set_mode(redis, PAUSADO)

Add `import json` at module top (currently only `from typing import Any`, :27-29) — stdlib, no
new dependency.
In set_mode() (:325-372): when `mode == LIGADO`, also `redis.delete(_PAUSE_REASON_KEY)` (mirrors
the existing `if mode == DESLIGADO:` side-effect block just above). This is the ONE place that
covers both resume paths (POST /engine/start already calls set_mode(LIGADO); POST /engine/mode
LIGADO calls it directly) — no separate clear-on-resume code needed anywhere else.
In get_status() (:428-464): add a "pause_reason" key to the returned dict — parse the JSON at
_PAUSE_REASON_KEY (None if absent/corrupt).
In maybe_complete() (:249-272): add a guard right after the existing
`if get_inflight(redis) > 0 or not is_dispatch_done(redis): return False` line —
`if redis.get(_PAUSE_REASON_KEY) is not None: return False` — a reasoned pause is not a completed
run; the DESLIGADO/"synced" side effects must never fire while a reason is set.

From brave/tasks/pipeline.py:
Add a module-level import: `from brave.shared.exceptions import ProviderBalanceError` (near the
existing `from brave.core.rio.routing import ...` block, :41-46) — used by 3 sites below.
  - _describe_chunk's inner _fetch (around :1750, the `except Exception as exc: fetched[rio_id] = exc`
    line): add `except ProviderBalanceError: raise` immediately before it, mirroring the existing
    `except SoftTimeLimitExceeded: raise` right above it — this lets it escape asyncio.gather.
  - describe_uf (:1816-1946): the `cut = asyncio.run(_describe_chunk(...))` call is already inside a
    `try: ... except SoftTimeLimitExceeded: ...` block. Add a sibling
    `except ProviderBalanceError as exc:` that calls
    `collection_engine.pause_with_reason(rc, "provider_balance", exc.provider, action="describe")`
    and `return` (the outer `finally` still runs `_producer_finally_lifecycle()` since `chained`
    stays False — no chain, no retry, matches "halt the chunk/chain").
  - describe_uf's cost-guard branch (describe_uf_cost_guard, :1885-1892, currently sets
    `halted = True` and lets the chunk cursor advance as if finishing normally): after it sets
    `halted = True`, also call `collection_engine.pause_with_reason(rc, "daily_budget", action="describe")`
    so the run is visibly paused instead of silently draining to "no more chunks, done".
  - enrich_places_task (:1642-1707): currently `except PermanentError: ...quarantine...` then
    `except Exception: ...retry...max_retries...quarantine`. Add a new `except ProviderBalanceError as exc:`
    clause BETWEEN those two (before the generic `except Exception`) that does `session.rollback()`
    then builds a redis client the same way describe_uf does
    (`import redis as _redis_lib; rc = _redis_lib.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))`)
    and calls `collection_engine.pause_with_reason(rc, "provider_balance", exc.provider, action="describe")`,
    then `return` — NO retry, NO quarantine.
  - The TA per-UF producer call site (:1220-1262, the
    `except (SessionMissingError, SessionExpiredError) as exc:` R1 block) already has `_prod_rc`
    defined just above (:1209-1211, `_prod_redis_lib.from_url(...)`). Add a sibling
    `except ProviderBalanceError as exc:` clause (same level as the R1 except) that calls
    `collection_engine.pause_with_reason(_prod_rc, "provider_balance", exc.provider, action="sweep")`
    and `return` — no retry, no quarantine, mirroring R1's shape but WITHOUT the
    `set_mode(DESLIGADO)` (this is PAUSADO via pause_with_reason, not a hard off).

From brave/domains/tripadvisor/atrativos.py:
Add module-level import: `from brave.shared.exceptions import ProviderBalanceError` (near the
existing `from brave.shared.destino import ensure_destino` line, :71-72).
  - _ingest_one's inline enrichment call (:821-824, `await self._places_agent.run(rio)`) needs no
    change itself — it already lets exceptions propagate.
  - The outer per-attraction loop (:251-289, `except Exception as exc: # noqa: BLE001` that
    quarantines poison): add `except ProviderBalanceError: raise` immediately before it, so a
    balance wall escapes produce() entirely instead of being quarantined as a bad record — it is
    then caught by pipeline.py's R1-sibling block above.

From brave/api/routers/engine.py (:119-133 /status, :187-395 /start, :430-470 /mode):
No new endpoint needed — get_status()'s new pause_reason field is already returned as-is by the
existing engine_status handler (`status = collection_engine.get_status(redis, session=db)`).
/start and /mode already route through `collection_engine.set_mode(..., session=db)` — the
LIGADO-clears-pause_reason change in engine.py covers both without touching this router.

From brave/config/settings.py (:74): change `usd_daily_budget: float = 10.0` to `= 50.0` on
LLMConfig. All existing tests that assert budget behavior construct `LLMConfig(usd_daily_budget=...)`
explicitly (grepped 260918 — test_real_llm_client.py, test_describe_uf.py, test_copy_batch.py,
test_cost_guard.py all pass an explicit value), so this default change does not require touching
those tests — grep for any bare `LLMConfig()` assertion against `10.0` before finishing (none found
in this repo as of 260918) and fix if one turns up.

From dashboard/lib/engine-api.ts:
EngineStatus interface (:64-98) already has `mode: EngineMode`. Add, right after the mode field,
mirroring the existing JSDoc style on that interface:

    pause_reason: { reason: string; provider: string | null; action: string | null; at: string } | null;

From dashboard/components/painel/PainelTopbar.tsx (629 lines):
Existing status-pill pattern to reuse (TA session pill, :90-117): sessionColor/sessionLabel helper
functions keyed on a status object, rendered with `style={{ color: "var(--status-dlq)" }}` and a
dot (see :387-411 for the pill JSX + var(--status-dlq) usage). No custom confirm-modal component
exists in this file (grepped 260918: no window.confirm or modal-confirm helper) — use the native
`window.confirm("Saldo recarregado?")` for the confirm step (native platform feature, no new
dependency).
The start mutation (:150-172) already accepts `{action: "sweep", depth, ufs?, maxPerUf?}` or
`{action: "describe", ufs?, maxPerUf?}`. For Continuar, read `data?.pause_reason?.action` (from the
polled EngineStatus) and `data?.depth ?? "nascente_rio"` / current `source` (already in scope,
:186) to rebuild the same start body — call `start.mutate({...})` after the confirm.
Add a red banner (reuse var(--status-dlq) styling from the TA needs_bootstrap pill, :115) shown
whenever `data?.pause_reason` is non-null, with the reason/provider text and a "Continuar" button.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: ProviderBalanceError + client classification + lane propagation</name>
  <files>brave/shared/exceptions.py, brave/clients/parallel.py, brave/clients/tavily.py, brave/clients/llm.py, brave/clients/places.py, brave/lanes/atrativos/copywriter.py, brave/lanes/atrativos/places_enrichment.py, tests/unit/test_provider_balance_error.py</files>
  <behavior>
    - Test: raise_if_balance_wall("tavily", status_code=432) raises ProviderBalanceError with .provider == "tavily"
    - Test: raise_if_balance_wall("anthropic", message="Your credit balance is too low") raises ProviderBalanceError
    - Test: raise_if_balance_wall("parallel", status_code=429) does NOT raise (rate limit, not balance)
    - Test: RealTavilyClient's/RealParallelClient's _post raises ProviderBalanceError (not httpx.HTTPStatusError) on a 432/402 response (respx-mocked)
    - Test: TourismCopywriter.write() re-raises ProviderBalanceError (does not degrade to None) when the LLM client raises it
    - Test: PlacesEnrichmentAgent.write_description() re-raises ProviderBalanceError (not the (None, None, True) no-spend tuple)
  </behavior>
  <action>
    Implement exactly the ProviderBalanceError class and the shared raise_if_balance_wall
    classifier described in this plan's context section, in brave/shared/exceptions.py next to the
    other BraveError subclasses (update the module docstring's hierarchy diagram too).

    Wire the classifier into every paid client call site listed under "Signals confirmed per
    client" above: parallel.py _post, tavily.py _post, llm.py's OpenRouter/Anthropic/Gemini paths
    (extract, _generate_openrouter, generate's Anthropic call sites, _generate_gemini), and
    places.py's text_search/place_details except blocks (plus the _is_retryable ResourceExhausted
    removal). Each site classifies BEFORE re-raising — a non-balance error must continue exactly as
    it does today (existing retry/quarantine/degrade behavior for every OTHER error class must not
    change).

    Then add `except ProviderBalanceError: raise` at the four lane call sites listed above
    (copywriter.py write_cascade x2, write x1; places_enrichment.py locate, write_description,
    run) — each placed immediately before the existing broad
    `except Exception:`/`except CostGuardError:` block, so it escapes before the degrade-to-floor
    logic runs. Use fakes/stubs (not real network) for the copywriter/places_enrichment tests —
    follow the existing FakeLLMClient/FakePlacesClient patterns already used in this test suite
    (grep tests/unit/lanes/atrativos for existing fixtures before writing new ones).
  </action>
  <verify>
    <automated>BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/unit/test_provider_balance_error.py tests/unit/clients/test_parallel_client.py tests/unit/clients/test_real_llm_client.py tests/unit/clients/test_real_places_client.py tests/unit/lanes -x -q 2>&1 | tail -15</automated>
  </verify>
  <done>
    ProviderBalanceError + raise_if_balance_wall exist in brave/shared/exceptions.py. Every paid
    client raises ProviderBalanceError (not the raw SDK/httpx exception) on a billing-wall
    response, while retryable errors (429/5xx/timeout) are unaffected. copywriter.py and
    places_enrichment.py let ProviderBalanceError propagate uncaught instead of degrading to
    None/floor or the CostGuardError no-spend tuple. All new + existing targeted tests pass.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Engine pause-with-reason + pipeline task halts + API exposure + budget default</name>
  <files>brave/core/engine.py, brave/tasks/pipeline.py, brave/domains/tripadvisor/atrativos.py, brave/config/settings.py, tests/unit/test_engine_pause_reason.py, tests/unit/tasks/test_describe_uf_balance_pause.py</files>
  <behavior>
    - Test: engine.pause_with_reason(redis, "provider_balance", "tavily", action="describe") sets mode PAUSADO and get_status(redis)["pause_reason"] == {"reason": "provider_balance", "provider": "tavily", "action": "describe", "at": an ISO datetime string}
    - Test: engine.set_mode(redis, LIGADO) clears pause_reason back to None
    - Test: engine.maybe_complete(redis) returns False (and does not flip mode) when a pause_reason is set, even with inflight==0 and dispatch_done
    - Test: describe_uf halts (no self-chain, no exception escapes the task) and calls pause_with_reason when the copywriter/search raises ProviderBalanceError mid-chunk
    - Test: enrich_places_task calls pause_with_reason (no retry, no PoisonQuarantine row) when the agent raises ProviderBalanceError
  </behavior>
  <action>
    In brave/core/engine.py: add `_PAUSE_REASON_KEY`, `pause_with_reason()`, the LIGADO-clears
    branch in `set_mode()`, the `pause_reason` field in `get_status()`, and the reasoned-pause
    guard in `maybe_complete()` — exactly as specified in this plan's context section. Add
    `import json` at the top of the file (module-level, not lazy — stdlib).

    In brave/tasks/pipeline.py: add the module-level `ProviderBalanceError` import, then wire the
    four call sites from the context section (`_fetch` re-raise, `describe_uf`'s
    `except ProviderBalanceError` around `asyncio.run(_describe_chunk(...))`, the cost-guard branch
    calling `pause_with_reason(rc, "daily_budget", ...)`, `enrich_places_task`'s new except clause,
    and the TA per-UF producer's sibling except next to the R1 block).

    In brave/domains/tripadvisor/atrativos.py: add the module-level import and the
    `except ProviderBalanceError: raise` in the per-attraction loop, as specified.

    In brave/config/settings.py: change `usd_daily_budget` default from `10.0` to `50.0` on
    LLMConfig. Grep the test suite for any assertion pinned to the bare 10.0 default and update it
    if found (none expected per the context section's grep).
  </action>
  <verify>
    <automated>BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/unit/test_engine_pause_reason.py tests/unit/test_engine_state.py tests/unit/test_engine_mode_persist.py tests/unit/tasks/test_describe_uf_balance_pause.py tests/unit/tasks/test_describe_uf.py -x -q 2>&1 | tail -15</automated>
  </verify>
  <done>
    engine.py exposes pause_with_reason/pause_reason and a reasoned pause survives maybe_complete.
    describe_uf, enrich_places_task, and the TA per-UF producer all halt (no retry/quarantine/chain)
    and call pause_with_reason on ProviderBalanceError. The internal cost-guard trip in describe_uf
    now also pauses with reason=daily_budget. LLMConfig.usd_daily_budget default is 50.0. All new +
    existing targeted tests pass.
  </done>
</task>

<task type="auto">
  <name>Task 3: Dashboard pause notice + Continuar resume flow</name>
  <files>dashboard/lib/engine-api.ts, dashboard/components/painel/PainelTopbar.tsx, dashboard/components/painel/__tests__/PainelTopbar.test.tsx</files>
  <action>
    In dashboard/lib/engine-api.ts: add the `pause_reason` field to the `EngineStatus` interface as
    specified in this plan's context section.

    In dashboard/components/painel/PainelTopbar.tsx: render a red banner (reuse
    `var(--status-dlq)`, mirroring the existing TA needs_bootstrap pill styling) whenever
    `data?.pause_reason` is non-null, showing the reason and provider (PT-BR label — e.g.
    "Motor pausado: sem saldo (tavily)" for reason=="provider_balance", "Motor pausado: orçamento
    diário atingido" for reason=="daily_budget"). Add a "Continuar" button next to the banner that,
    on click, runs `window.confirm("Saldo recarregado?")`; on confirm, calls the existing `start`
    mutation reconstructed from `data.pause_reason.action` ("describe" → `{action: "describe"}`;
    "sweep"/anything else → `{action: "sweep", depth: data.depth ?? "nascente_rio"}`, using the
    `source` already in scope). On cancel, do nothing (banner stays).
  </action>
  <verify>
    <automated>cd dashboard && bun run test -- PainelTopbar 2>&1 | tail -20</automated>
  </verify>
  <done>
    EngineStatus carries pause_reason. PainelTopbar shows a red pause banner with the
    reason/provider and a Continuar button that confirms then calls startEngine with the paused
    action. New/updated PainelTopbar tests pass.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Brave client → external provider (Parallel/Tavily/OpenRouter/Anthropic/Gemini/Places) | Billing-wall responses (402/432/433, "credit balance too low", ResourceExhausted) cross this boundary and must not be misread as ordinary transient failures |
| Painel operator → POST /engine/start (Continuar) | Resuming after a pause is a mutation gated by require_steward_or_bearer (unchanged) |
| Redis pause_reason key → dashboard display | JSON stored server-side, rendered as-is in the banner — provider/reason strings are our own enum values, never raw provider error text (no leaked API-key fragments) |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-ohm-01 | Information Disclosure | pause_with_reason JSON payload | mitigate | only `reason` (our own enum: provider_balance/daily_budget), `provider` (our own slug string), `action`, `at` are stored — never `str(exc)` from the provider, so no cookie/token/key fragment can leak into Redis or the dashboard |
| T-ohm-02 | Denial of Service | A misclassified transient error (e.g. a 5xx momentarily returning as 402 from a flaky proxy) pauses the whole motor | accept | low probability (status-code allowlist is narrow: 402/432/433 + explicit message markers); operator sees the reason immediately and can Continuar after checking, cost is one manual confirm, not silent data loss |
| T-ohm-03 | Tampering | Continuar resume reconstructs the start body from `data.depth`/`source` polled from the server, not client-held state | mitigate | resume never lets the browser choose an arbitrary depth/source — it echoes the server's own last-known values, same trust level as every other read in this component |
| T-ohm-SC | Tampering | No new npm/pip/cargo installs in this plan | accept | uses stdlib json + already-installed openai/anthropic/httpx/google-maps-places exception types; no package legitimacy audit needed |
</threat_model>

<verification>
Full offline suite must remain green after all 3 tasks complete:

```
BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/unit -q 2>&1 | tail -10
cd dashboard && bun run test 2>&1 | tail -15
```

Do NOT run tests/integration (wipes the local DB per project convention).

Key per-task checks:
- T1: `grep -n "class ProviderBalanceError" brave/shared/exceptions.py` and
  `grep -rn "raise_if_balance_wall" brave/clients | wc -l` returns at least 6 (parallel, tavily,
  llm x3-4, places x2)
- T2: `grep -n "pause_with_reason" brave/core/engine.py brave/tasks/pipeline.py
  brave/domains/tripadvisor/atrativos.py` returns lines in each file;
  `grep -n "usd_daily_budget: float = 50.0" brave/config/settings.py` returns 1 line
- T3: `grep -n "pause_reason" dashboard/lib/engine-api.ts
  dashboard/components/painel/PainelTopbar.tsx` returns lines in both files

End-to-end acceptance (operator-run, post-execution):
1. With RUN_REAL_EXTERNALS unset, simulate a ProviderBalanceError in a unit test path — confirm
   the motor lands in PAUSADO with a pause_reason and the Painel topbar shows the red banner.
2. Click Continuar, confirm the dialog, verify /engine/start is called with the same action the
   run was in when it paused, and pause_reason clears once mode returns to LIGADO.
</verification>

<success_criteria>
- brave/shared/exceptions.py: ProviderBalanceError + raise_if_balance_wall exist and are used by
  every paid client instead of per-client duplicated classification
- brave/lanes/atrativos/copywriter.py and places_enrichment.py let ProviderBalanceError propagate
  uncaught — no descricao_attempts increment, no google_enriched stamp on a balance wall
- brave/core/engine.py exposes pause_with_reason/pause_reason; PAUSADO-with-reason survives
  maybe_complete and clears only on set_mode(LIGADO)
- brave/tasks/pipeline.py's describe_uf, enrich_places_task, and the TA per-UF producer all halt
  (no retry/quarantine/self-chain) and call pause_with_reason on ProviderBalanceError; the internal
  daily cost-guard trip also pauses with reason=daily_budget instead of silently completing
- brave/config/settings.py: LLMConfig.usd_daily_budget default is 50.0
- dashboard: EngineStatus carries pause_reason; PainelTopbar shows a red pause banner + Continuar
  (confirm then resume with the paused action)
- Full offline suite (pytest unit + dashboard vitest) passes with 0 failures
</success_criteria>

<output>
Create `.planning/quick/260918-ohm-pause-on-no-balance/260918-ohm-SUMMARY.md` when done
</output>
