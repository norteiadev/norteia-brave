# Phase 6: Real-Externals Enablement (RealLLMClient + live 24/7 collection) - Context

**Gathered:** 2026-06-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Make the already-built 24/7 auto-collection (Phase 5 `brave.sweep_uf` + 27-UF redbeat fan-out + Atrativos FSM auto-advance) actually run on **real external data** by implementing the **single missing client**: `brave/clients/llm.py` / `RealLLMClient`.

Today `brave/tasks/pipeline.py` selects real vs fake clients by `if app_config.run_real_externals:`. Every other real client exists (`RealPlacesClient`, `RealApifyClient`, `TwilioWhatsAppClient`), but the four LLM call sites (pipeline.py:667/800/1237/1430) do `from brave.clients.llm import RealLLMClient  # type: ignore[import]` against a module **that was never created** → `ModuleNotFoundError` the instant `run_real_externals=True`. This blocks every LLM-bearing lane in real mode: DesmembramentoAgent (origem=40 sub-destinos), DiscoveryAgent (DeepSeek map of Places results → `AtrativoResult`), SignalAgent extraction, and the WhatsApp conversation extract/generate.

**This phase delivers:** a working `RealLLMClient` implementing `LLMClientProtocol` (`extract` + `generate`) with cost-guard + `llm_generations` logging on the real path, plus the fix for the `BRAVE_RUN_REAL_EXTERNALS` docstring footgun (real toggle is `RUN_REAL_EXTERNALS`). After this, `RUN_REAL_EXTERNALS=true` + `BRAVE_LLM_OPENROUTER_API_KEY` (+ `BRAVE_PLACES_API_KEY` for Atrativos) runs the existing sweep on real data end-to-end up to the human gates.

**This phase does NOT:** add new lanes, change orchestration (Phase 5 owns sweep/beat/FSM), or enable automatic WhatsApp send (stays human-gated, no auto-outreach).
</domain>

<decisions>
## Implementation Decisions

### RealLLMClient structure
- **D-01:** One module `brave/clients/llm.py`, one class `RealLLMClient` implementing `LLMClientProtocol` (both `extract` and `generate`). Constructor MUST stay compatible with the existing four call sites: `RealLLMClient(config=app_config.llm)` (an `LLMConfig`). Additional optional deps (see D-05) are keyword-only with safe defaults so those call sites keep working unchanged and can opt into tracking.
- **D-02:** Mirror the established real-client pattern of `RealPlacesClient`/`RealApifyClient`: hard `run_real_externals` guard in `__init__` (raise `RuntimeError` when `AppConfig().run_real_externals` is False — prevents accidental real calls in CI/default suite), `tenacity` retry (exponential backoff on 429/5xx/connection errors), `structlog` logger. Module docstring + usage example in the same shape as `places.py`.

### extract() transport (DeepSeek / structured)
- **D-03:** `extract(prompt, schema, mode="tools")` → `instructor`-wrapped **OpenAI SDK** (`AsyncOpenAI`) pointed at `config.openrouter_base_url` with `config.openrouter_api_key`, using **Mode.Tools** (per CLAUDE.md LLM decision + instructor DeepSeek integration). Model = `config.deepseek_primary_slug`; on failure walk `config.deepseek_fallback_slugs` in order. instructor enforces the 2nd-layer Pydantic validation/retry against `schema`.
- **D-04:** Enforce `provider.data_collection = config.provider_data_collection` (= `"deny"`) in **every** OpenRouter request body (extra_body `provider` block). This is a hard compliance requirement ("paid ≠ won't train") — assert it in a unit test.

### generate() transport (Sonnet / conversation)
- **D-05a:** `generate(messages, model="claude-sonnet-4-5")` → **native Anthropic SDK** (`AsyncAnthropic`) with `config.anthropic_api_key`, NOT via OpenRouter (per the locked LLM split — conversation quality + direct quota/streaming). Returns the text string. Used only by the WhatsApp conversation path (off the live-collection critical path), but implemented for protocol completeness.

### Cost guard + llm_generations logging (protocol mandate)
- **D-05:** `LLMClientProtocol` states "every call must log to `llm_generations` and check the USD cost guard" (OBS-01, D-20). Honor this on the real path using the **existing** helpers — `cost_guard.pre_dispatch_check` (before dispatch) and `record_spend` + an `LLMGeneration` row (after) — with **real** usage parsed from the provider response (OpenRouter `usage` incl. cost via `usage: {include: true}`; Anthropic `response.usage`). Wiring: `RealLLMClient` accepts optional keyword `redis_client` / `session` (+ `lane`) deps; when present it self-tracks, when absent it is a pure transport (so simple construction and the Fake stay zero-cost). The four pipeline call sites pass the deps they already hold. Do **not** re-architect the lane agents' call signatures.

### Toggle footgun fix
- **D-06:** Fix the wrong env-var name in docstrings/error strings of `brave/clients/apify.py`, `brave/clients/whatsapp.py`, `brave/clients/places.py`: `BRAVE_RUN_EXTERNALS`/`BRAVE_RUN_REAL_EXTERNALS` → **`RUN_REAL_EXTERNALS`** (the real toggle: `AppConfig` field with `env_prefix=""`, so NO `BRAVE_` prefix). Following the current docstrings silently yields fakes — operator footgun. Doc/string-only; no behavior change.

### Offline test strategy
- **D-07:** Suite stays **100% offline by default** (TEST constraint). New tests: (1) guard test — `RealLLMClient.__init__` raises when `run_real_externals=False` (mirrors the RealPlacesClient guard test); (2) deny-enforcement test — mock the OpenAI/instructor client and assert `provider.data_collection="deny"` is in the request body; (3) fallback test — primary slug fails → next slug tried; (4) cost-guard test — a real-path call invokes `pre_dispatch_check` and writes one `LLMGeneration` row with non-zero usage. Real network verified only by an **opt-in** smoke path (`RUN_REAL_EXTERNALS=true` + key), `pytest.mark.skipif` when the key is absent so CI stays keyless.

### Claude's Discretion
- Exact instructor wiring (`from_openai` vs `from_provider`), where the deny-block is injected (extra_body vs default_headers), tenacity predicate reuse vs new, token/cost extraction helper placement, test fixture layout, commit granularity, and whether `generate()` token logging mirrors `extract()` exactly.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Client boundary + the gap
- `brave/clients/base.py` §`LLMClientProtocol` (lines 24–72) — the contract `RealLLMClient` MUST satisfy (`extract` + `generate` signatures; "every call logs + checks cost guard").
- `brave/tasks/pipeline.py` lines 666–671, 799–804, 1236–1241, 1429–1434 — the four real-vs-fake selection sites; the phantom `from brave.clients.llm import RealLLMClient` imports to be made real.
- `tests/fakes/fake_llm.py` — `FakeLLMClient` (exact method shapes + defaults to mirror; structural protocol check pattern).

### Mirror pattern (existing real clients)
- `brave/clients/places.py` — `RealPlacesClient`: run_real_externals guard, tenacity retry predicate, structlog, module-docstring shape to copy.
- `brave/clients/apify.py` — `RealApifyClient`: same guard/retry pattern (and a footgun docstring to fix, D-06).
- `brave/clients/whatsapp.py` — `TwilioWhatsAppClient`: real-path guard (and footgun docstrings to fix, D-06).

### Cost guard + observability
- `brave/observability/cost_guard.py` — `pre_dispatch_check` / `record_spend` (D-20 enforcing guard helpers).
- `brave/observability/llm_tracker.py` — `LLMTracker.track_and_call` (existing tracking shape + `LLMGeneration` fields; reference for self-tracking).
- `brave/core/models.py` §`LLMGeneration` — the row to write (lane, model_slug, tokens, usd_cost; no prompt content — PII, T-02-04).

### Config + provider decision
- `brave/config/settings.py` §`LLMConfig` (prefix `BRAVE_LLM_`) — `openrouter_base_url`, `openrouter_api_key`, `deepseek_primary_slug`, `deepseek_fallback_slugs`, `provider_data_collection`, `usd_daily_budget`, `anthropic_api_key`; §`AppConfig` — `run_real_externals` (env `RUN_REAL_EXTERNALS`, no prefix).
- `CLAUDE.md` "LLM provider decision" table — DeepSeek-via-OpenRouter + `instructor` Mode.Tools, `:nitro` for backend, `provider.data_collection: "deny"`, Sonnet via native Anthropic SDK. The locked split this client implements.

### Consumers (call `extract`/`generate`)
- `brave/lanes/destinos/desmembramento.py` (line 170), `brave/lanes/atrativos/discovery_agent.py` (line 268), `brave/lanes/atrativos/whatsapp_agent.py` (lines 447, 540) — usage shapes the client must serve.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `RealPlacesClient`/`RealApifyClient` guard+tenacity+structlog template — copy structure for `RealLLMClient`.
- `cost_guard.pre_dispatch_check` / `record_spend` and `LLMGeneration` model — reuse directly for D-05; do not reinvent.
- `LLMConfig` already holds every needed knob (slugs, base_url, keys, deny flag, budget) — no settings changes needed.

### Established Patterns
- Structural typing only (`Protocol`, no isinstance) — `RealLLMClient` satisfies `LLMClientProtocol` structurally; add a compile-time `_check_protocol_compliance` like the fake.
- Real clients fail-closed on `run_real_externals=False` — same guard here.
- `llm_generations` logs slug+tokens+usd only, never prompt content (T-02-04) — preserve.

### Integration Points
- Four pipeline call sites construct `RealLLMClient(config=app_config.llm)` — keep that signature; pass optional redis/session there for tracking.
- Lane agents call `llm_client.extract(...)` / `.generate(...)` directly (no tracker today) — tracking moves into the real client so call sites stay unchanged.
</code_context>

<specifics>
## Specific Ideas

- Acceptance bar for the phase: with `RUN_REAL_EXTERNALS=true` + `BRAVE_LLM_OPENROUTER_API_KEY` (+ `BRAVE_PLACES_API_KEY`), `brave.cli sweep <UF>` runs the real Destinos+Atrativos sweep to the human gates with zero `ModuleNotFoundError`, real `llm_generations` rows written, cost guard enforced — verified by the offline test matrix (mocked) plus a key-gated opt-in smoke test.
- WhatsApp send stays OFF (human gate, no auto-outreach) — only `extract`/`generate` transport is implemented, not automated messaging.
</specifics>

<deferred>
## Deferred Ideas

- `:nitro` throughput-variant slug selection for batch extraction — optional tuning, can land with this client but not required for "it runs real."
- Real per-call token/cost backfill into the dashboard Cost view beyond what `llm_generations` already feeds — future observability polish.
- Migrating lane agents to a uniform `LLMTracker` wrapper (vs in-client tracking) — only if the in-client approach proves awkward.
- Anthropic streaming for WhatsApp conversation — future, conversation path not on the live-collection critical path.

None of these block the phase goal.
</deferred>

---

*Phase: 6-Real-Externals Enablement (RealLLMClient + live 24/7 collection)*
*Context gathered: 2026-06-17*
