# Phase 6: Real-Externals Enablement (RealLLMClient + live 24/7 collection) - Research

**Researched:** 2026-06-17
**Domain:** Python async LLM client implementation — instructor + OpenRouter/DeepSeek + native Anthropic SDK
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** One module `brave/clients/llm.py`, one class `RealLLMClient`. Constructor: `RealLLMClient(config=app_config.llm)` (an `LLMConfig`). Additional optional keyword deps for tracking; four existing call sites stay unchanged.
- **D-02:** Mirror `RealPlacesClient`/`RealApifyClient` pattern: hard `run_real_externals` guard in `__init__`, tenacity retry (exponential backoff on 429/5xx/connection), structlog, module docstring shape.
- **D-03:** `extract()` → instructor-wrapped `AsyncOpenAI` at `config.openrouter_base_url` with `config.openrouter_api_key`, using `Mode.TOOLS`. Model = `config.deepseek_primary_slug`; fallback walks `config.deepseek_fallback_slugs`. instructor enforces 2nd-layer Pydantic validation/retry.
- **D-04:** `provider.data_collection = config.provider_data_collection` (`"deny"`) in EVERY OpenRouter request body. Hard compliance requirement. Assert in unit test.
- **D-05a:** `generate()` → native `AsyncAnthropic` with `config.anthropic_api_key`. Returns text string. NOT via OpenRouter.
- **D-05:** Real usage from provider response (OpenRouter `usage.cost` via `model_extra`; Anthropic `response.usage.input_tokens/output_tokens`). Optional `redis_client`/`session`/`lane` keyword deps for self-tracking; pure transport when absent.
- **D-06:** Fix `BRAVE_RUN_REAL_EXTERNALS` → `RUN_REAL_EXTERNALS` in docstrings/error strings of `brave/clients/apify.py`, `brave/clients/whatsapp.py`, `brave/clients/places.py`. String/doc only; no behavior change.
- **D-07:** 100% offline suite. Four new tests: guard, deny-enforcement, fallback, cost-guard wiring. Opt-in smoke with `pytest.mark.skipif` when key absent.

### Claude's Discretion

Exact instructor wiring (`from_openai` vs `from_provider`), where the deny-block is injected (extra_body vs default_headers), tenacity predicate reuse vs new, token/cost extraction helper placement, test fixture layout, commit granularity, whether `generate()` token logging mirrors `extract()` exactly.

### Deferred Ideas (OUT OF SCOPE)

- `:nitro` throughput-variant slug selection
- Real per-call token/cost backfill beyond `llm_generations` into dashboard Cost view
- Migrating lane agents to a uniform `LLMTracker` wrapper
- Anthropic streaming for WhatsApp conversation
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| OBS-01 | Pipeline records every LLM call in `llm_generations` (per-lane, per-model, USD cost) | D-05 wiring: `LLMTracker.track_and_call` or inline `pre_dispatch_check` + `record_spend` + `LLMGeneration` |
| OBS-02 | USD cost guard enforces spend ceiling, halts on breach | `cost_guard.pre_dispatch_check` before every dispatch; `record_spend` after |
| CORE-11 | Every external system behind a client interface with a fake | `RealLLMClient` implements `LLMClientProtocol` structurally; `FakeLLMClient` already exists |
| TEST-01 | Full suite 100% offline; real opt-in by flag; CI keyless | `run_real_externals` guard + `pytest.mark.skipif` on key absence |
</phase_requirements>

---

## Summary

Phase 6 has a single deliverable: `brave/clients/llm.py` containing `RealLLMClient`, which satisfies the existing `LLMClientProtocol` (`extract` + `generate`). All four phantom `from brave.clients.llm import RealLLMClient` sites in `pipeline.py` will resolve, unblocking real-data operation when `RUN_REAL_EXTERNALS=true`.

The implementation splits across two transport paths that are already locked by the project's LLM strategy: `extract()` goes through `instructor`-wrapped `AsyncOpenAI` pointed at OpenRouter (DeepSeek backend, Mode.TOOLS, ordered slug fallbacks, provider.data_collection=deny enforced in every request), and `generate()` goes through the native `AsyncAnthropic` SDK (Claude Sonnet 4.5, NOT via OpenRouter). Both paths must wire into the existing `cost_guard` + `llm_generations` infrastructure when optional `redis_client`/`session` deps are provided.

The footgun fix (D-06) is a pure docstring/error-string edit in three files (`apify.py`, `whatsapp.py`, `places.py`): replace `BRAVE_RUN_REAL_EXTERNALS` with `RUN_REAL_EXTERNALS` (the real env var, `AppConfig` field with `env_prefix=""`).

**Primary recommendation:** Use `instructor.from_openai(AsyncOpenAI(base_url=..., api_key=...), mode=instructor.Mode.TOOLS)` (not `from_provider`) for full control over the deny-block injection via `extra_body` in each `create()` call. Extract cost from `raw_completion.usage.model_extra.get("cost", 0.0)` via `create_with_completion`.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| LLM structured extraction (extract) | collector Python service | OpenRouter (transport) | All external API calls stay in the collector; OpenRouter is the provider routing layer |
| LLM conversation generation (generate) | collector Python service | Anthropic API (transport) | Native SDK for direct quota/streaming control; never via OpenRouter per locked split |
| Cost guard enforcement | collector Python service | Redis (counter) | Redis INCRBYFLOAT for atomic daily counter; guard halts before dispatch |
| llm_generations logging | collector Python service | PostgreSQL (persistence) | Observability row written post-call; session passed optionally |
| provider.data_collection enforcement | OpenRouter request body | — | `extra_body={"provider": {"data_collection": "deny"}}` on every create() |

---

## Standard Stack

### Verified Installed Versions

All packages confirmed in `.venv` via Python inspection. [VERIFIED: local .venv]

| Library | Installed Version | Purpose | Role in This Phase |
|---------|------------------|---------|-------------------|
| `instructor` | 1.15.1 | instructor Mode.TOOLS wrapper for structured LLM output | wraps AsyncOpenAI for extract() |
| `openai` | 2.41.1 | OpenAI-compatible SDK for OpenRouter | AsyncOpenAI client pointed at openrouter.ai |
| `anthropic` | 0.109.1 | Native Anthropic SDK | AsyncAnthropic client for generate() |
| `tenacity` | 9.1.x (existing) | Retry/backoff | 429/5xx/connection retry predicate on extract() and generate() |
| `structlog` | 26.x (existing) | Structured logging | logger = structlog.get_logger(__name__) |
| `fakeredis` | 2.36.x (existing) | In-process Redis for unit tests | fake_redis fixture already in conftest |

No new packages to install — all are already in the project's `.venv`.

### Package Legitimacy Audit

> No new packages are installed in this phase — all libraries are already in the existing `.venv`. This section is satisfied by the existing project dependency audit.

| Package | Status | Note |
|---------|--------|------|
| instructor 1.15.1 | Already installed, in-use | Existing project dependency |
| openai 2.41.1 | Already installed, in-use | Existing project dependency |
| anthropic 0.109.1 | Already installed, in-use | Existing project dependency |

---

## Architecture Patterns

### System Architecture Diagram

```
pipeline.py task (run_real_externals=True)
  │
  ├─► RealLLMClient(config=LLMConfig, redis_client=?, session=?, lane=?)
  │         │
  │    [__init__] AppConfig().run_real_externals check → RuntimeError if False
  │         │
  │    extract(prompt, schema, mode="tools")
  │         ├─ pre_dispatch_check(redis_client, config)  ← CostGuardError if budget exceeded
  │         ├─ instructor.AsyncInstructor.create_with_completion(
  │         │       response_model=schema,
  │         │       messages=[{"role":"user","content":prompt}],
  │         │       model=primary_slug,
  │         │       extra_body={"provider": {"data_collection": "deny"}},
  │         │   )  →  (result, raw_completion)
  │         ├─ [on failure] retry next slug in fallback_slugs
  │         ├─ record_spend(redis_client, raw_completion.usage.model_extra["cost"])
  │         └─ session.add(LLMGeneration(...))  → llm_generations row
  │
  │    generate(messages, model="claude-sonnet-4-5")
  │         ├─ pre_dispatch_check(redis_client, config)  ← CostGuardError if budget exceeded
  │         ├─ AsyncAnthropic(api_key=...).messages.create(
  │         │       model=model, max_tokens=2048,
  │         │       messages=messages,
  │         │   )  →  Message
  │         ├─ response.content[0].text  →  text string
  │         ├─ record_spend(redis_client, usd_cost)   ← computed from token counts
  │         └─ session.add(LLMGeneration(...))  → llm_generations row
  │
  └─► lane agent (DesmembramentoAgent / DiscoveryAgent / WhatsAppAgent)
         calls extract() or generate() → result
```

### Recommended Project Structure

```
brave/clients/
├── base.py          # LLMClientProtocol (unchanged)
├── llm.py           # NEW: RealLLMClient (this phase)
├── places.py        # docstring fix: BRAVE_RUN_REAL_EXTERNALS→RUN_REAL_EXTERNALS
├── apify.py         # docstring fix: BRAVE_RUN_REAL_EXTERNALS→RUN_REAL_EXTERNALS
└── whatsapp.py      # docstring fix: BRAVE_RUN_REAL_EXTERNALS→RUN_REAL_EXTERNALS

tests/unit/
└── test_real_llm_client.py  # NEW: 4 offline tests (guard, deny, fallback, cost-guard)

tests/integration/
└── test_real_llm_smoke.py   # NEW: opt-in smoke (skipif no key) — optional, see D-07
```

---

## Pattern 1: instructor wiring — `from_openai` (not `from_provider`)

**What:** Build the instructor `AsyncInstructor` by wrapping a manually constructed `AsyncOpenAI` pointed at OpenRouter.

**Why `from_openai` over `from_provider("openrouter/deepseek-chat")`:**
- `from_provider` reads `OPENROUTER_API_KEY` from env (not `BRAVE_LLM_OPENROUTER_API_KEY`), ignoring the project's env-var naming convention (CR-02).
- `from_openai` gives full control over `base_url`, `api_key`, and the `extra_body` injection pattern.
- Both are verified paths in instructor 1.15.x source; `from_openai` is the stable production path for custom base_url scenarios.

[VERIFIED: local .venv instructor 1.15.1 source]

```python
# Source: instructor 1.15.1 source / local .venv inspection
from openai import AsyncOpenAI
import instructor

_openai_client = AsyncOpenAI(
    api_key=config.openrouter_api_key,
    base_url=config.openrouter_base_url,  # "https://openrouter.ai/api/v1"
)
_instructor_client: instructor.AsyncInstructor = instructor.from_openai(
    _openai_client,
    mode=instructor.Mode.TOOLS,
)
```

The `mode` string param from the protocol (`"tools"` | `"json"` | `"md_json"`) maps to `instructor.Mode` members:

| Protocol `mode` string | instructor.Mode member | Mode.value |
|------------------------|----------------------|------------|
| `"tools"` | `instructor.Mode.TOOLS` | `"tool_call"` |
| `"json"` | `instructor.Mode.JSON` | `"json_mode"` |
| `"md_json"` | `instructor.Mode.MD_JSON` | `"markdown_json_mode"` |

**Note:** `instructor.from_openai` validates that OpenRouter provider only accepts `TOOLS`, `OPENROUTER_STRUCTURED_OUTPUTS`, or `JSON` modes. `MD_JSON` is **not** in that allowlist — it will raise an `AssertionError` at construction time if the mode is set to `MD_JSON` on an OpenRouter client. If the protocol ever calls `extract(mode="md_json")`, the client must either switch to a DeepSeek-specific construction that bypasses the OpenRouter provider check, or raise a clear error. [VERIFIED: local .venv instructor 1.15.1 `from_openai` source]

For Phase 6, the active callers (`DesmembramentoAgent`, `DiscoveryAgent`, `WhatsAppAgent`) all pass `mode="tools"`, so this is not an immediate issue.

---

## Pattern 2: `create_with_completion` for usage/cost extraction

**What:** Use `AsyncInstructor.create_with_completion` instead of `create` to get both the validated result and the raw completion object in one call.

**Why:** `create_with_completion` returns `(result, raw_completion)` where `raw_completion` is the underlying `ChatCompletion` object. Since `CompletionUsage` has `model_config = {'extra': 'allow'}`, OpenRouter's injected `cost` field is accessible via `raw_completion.usage.model_extra.get("cost", 0.0)`.

[VERIFIED: local .venv openai 2.41.1 + instructor 1.15.1 source]

```python
# Source: instructor 1.15.1 create_with_completion + openai 2.41.1 CompletionUsage
result, raw = await self._instructor_client.create_with_completion(
    messages=[{"role": "user", "content": prompt}],
    response_model=schema,
    model=slug,
    extra_body={"provider": {"data_collection": self._config.provider_data_collection}},
)
usage = raw.usage  # openai.types.completion_usage.CompletionUsage
prompt_tokens = usage.prompt_tokens if usage else 0
completion_tokens = usage.completion_tokens if usage else 0
usd_cost = float(usage.model_extra.get("cost", 0.0)) if usage and usage.model_extra else 0.0
```

---

## Pattern 3: `provider.data_collection` via `extra_body`

**What:** The OpenRouter `provider.data_collection=deny` parameter goes in the request body as a `provider` object. With the OpenAI SDK, this is injected via the `extra_body` kwarg to `create()`.

**Why:** `extra_body` is a documented parameter of `AsyncCompletions.create` in openai 2.41.x. instructor passes `**kwargs` through to the underlying `create_fn`, so `extra_body` reaches OpenRouter. [VERIFIED: local .venv openai 2.41.1 AsyncCompletions.create signature; OpenRouter provider routing docs]

```python
# Source: openrouter.ai/docs/guides/routing/provider-selection + local .venv inspection
extra_body={"provider": {"data_collection": self._config.provider_data_collection}}
# where config.provider_data_collection = "deny" (locked default, tested)
```

**Unit-assertable pattern:**
```python
# In tests — monkeypatch create_with_completion and capture kwargs
mock_create.assert_called_with(
    ...,
    extra_body={"provider": {"data_collection": "deny"}},
)
```

---

## Pattern 4: Ordered slug fallback strategy

**What:** Try `config.deepseek_primary_slug`, then walk `config.deepseek_fallback_slugs` on failure. Tenacity handles *transient* errors (429/5xx/connection) on a per-attempt basis; slug fallback handles *permanent model-unavailable* errors (404 Not Found, 503 model offline).

**Exception taxonomy for OpenRouter via openai SDK:**

| Exception class | status_code | Signal | Action |
|----------------|-------------|--------|--------|
| `openai.RateLimitError` | 429 | Transient quota | tenacity retry same slug |
| `openai.InternalServerError` | 500 | Transient 5xx | tenacity retry same slug |
| `openai.APIConnectionError` | — | Network flap | tenacity retry same slug |
| `openai.APITimeoutError` | — | Timeout | tenacity retry same slug |
| `openai.NotFoundError` | 404 | Model not found | skip to next slug (fallback) |
| `openai.BadRequestError` | 400 | Invalid request | raise immediately (permanent) |
| `openai.PermissionDeniedError` | 403 | Auth failure | raise immediately (permanent) |

[VERIFIED: local .venv openai 2.41.1 exception hierarchy]

**Pattern:**
```python
# Source: openai 2.41.1 exception hierarchy — local .venv
from openai import NotFoundError, BadRequestError, PermissionDeniedError, APIStatusError

slugs = [config.deepseek_primary_slug] + list(config.deepseek_fallback_slugs)
last_exc: Exception | None = None
for slug in slugs:
    try:
        result, raw = await self._call_with_retry(slug, ...)
        # success — break
        return result, raw
    except (NotFoundError,) as exc:
        last_exc = exc
        logger.warning("llm_slug_unavailable", slug=slug, error=str(exc))
        continue  # try next slug
    except (BadRequestError, PermissionDeniedError) as exc:
        raise  # permanent — don't retry different slug
raise last_exc  # all slugs exhausted
```

---

## Pattern 5: Anthropic `generate()` transport

**What:** Native `AsyncAnthropic` call, NOT via OpenRouter.

**Key details from installed SDK (anthropic 0.109.1):**
- `messages.create()` requires `max_tokens` (no default — required positional kwarg). [VERIFIED: local .venv anthropic 0.109.1]
- Content is `response.content` — a list of `ContentBlock` objects. Text is in `content[0].text` when `content[0].type == "text"`. [VERIFIED: local .venv anthropic.types.TextBlock]
- Usage: `response.usage.input_tokens` + `response.usage.output_tokens`. [VERIFIED: local .venv anthropic.types.Usage]
- USD cost must be computed from a token-price table (Anthropic API does not return a `cost` field in the response). For Sonnet 4.5: $3/MTok input, $15/MTok output per Anthropic docs. [CITED: platform.claude.com/docs/en/about-claude/models/overview]

```python
# Source: anthropic 0.109.1 — local .venv
import anthropic

_anthropic_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

response = await _anthropic_client.messages.create(
    model=model,           # e.g. "claude-sonnet-4-5"
    max_tokens=2048,       # required — no default
    messages=messages,     # list[dict[str, Any]] from protocol
)
text: str = response.content[0].text  # TextBlock.text
# Usage for llm_generations:
prompt_tokens = response.usage.input_tokens
completion_tokens = response.usage.output_tokens
# Cost: compute from price table (no cost field in response)
usd_cost = (prompt_tokens * 3.0 + completion_tokens * 15.0) / 1_000_000
```

---

## Pattern 6: Run-real-externals guard (mirror of RealPlacesClient)

**What:** Hard guard in `__init__`, same shape as `RealPlacesClient` and `RealApifyClient`.

```python
# Source: brave/clients/places.py __init__ pattern — existing codebase
def __init__(self, config: LLMConfig, *, redis_client=None, session=None, lane: str = "unknown") -> None:
    from brave.config.settings import AppConfig
    if not AppConfig().run_real_externals:
        raise RuntimeError(
            "RealLLMClient: run_real_externals=False — "
            "use FakeLLMClient in default test suite. "
            "Set RUN_REAL_EXTERNALS=true to enable real LLM calls."
        )
    if not config.openrouter_api_key:
        raise RuntimeError(
            "RealLLMClient: openrouter_api_key is empty — "
            "set BRAVE_LLM_OPENROUTER_API_KEY environment variable."
        )
    ...
```

---

## Pattern 7: Optional tracking deps (D-05 — pure-transport fallback)

**What:** `redis_client`, `session`, `lane` are optional keyword-only deps. When all three are provided, the client self-tracks (cost guard + llm_generations row). When any is absent, it is a pure transport.

**Context from call sites:**

| Call site in pipeline.py | session available? | redis_client available? | Current construction |
|--------------------------|-------------------|------------------------|----------------------|
| `discover_atrativo_task` (~line 668) | YES | NO | `RealLLMClient(config=app_config.llm)` |
| `sweep_uf_task` (~line 801) | YES | NO | `RealLLMClient(config=app_config.llm)` |
| `outreach_task` (~line 1238) | YES | YES | `RealLLMClient(config=app_config.llm)` |
| `resume_conversation_task` (~line 1431) | YES | YES | `RealLLMClient(config=app_config.llm)` |

The four sites keep `RealLLMClient(config=app_config.llm)` unchanged. The planner decides whether to update two of them (outreach/resume) to pass the available `redis_client` + `session` for full tracking, or leave all four as pure transport for now. D-05 says "when present, self-tracks" — the optional deps are the designed hook; it is the planner's call whether to wire them in this phase or leave as a follow-on.

**Design choice (Claude's Discretion — inline vs LLMTracker):**

`LLMTracker.track_and_call` encapsulates cost-guard + logging into a single callable. However:
- `track_and_call` takes a `call_fn: Callable` — it wraps the LLM call from outside.
- `RealLLMClient.extract` must do its own fallback-slug loop, which `track_and_call` does not know about.
- The cost guard must fire BEFORE each slug attempt (or once per top-level extract call).

**Recommendation:** Inline the cost guard and logging inside `extract()` / `generate()` directly, following the same helper calls `LLMTracker` uses (`pre_dispatch_check`, `record_spend`, `LLMGeneration`). This avoids an awkward lambda-inside-loop and keeps the fallback logic clear. Reference `LLMTracker` as documentation of the pattern but do not delegate to it.

---

## Pattern 8: Footgun fix — D-06 string replacements

**Exact occurrences of `BRAVE_RUN_REAL_EXTERNALS` to fix:** [VERIFIED: grep of codebase]

| File | Line | Current string | Fix to |
|------|------|---------------|--------|
| `brave/clients/places.py` | 95 | `"Set BRAVE_RUN_REAL_EXTERNALS=true to enable real API calls."` | `"Set RUN_REAL_EXTERNALS=true to enable real API calls."` |
| `brave/clients/apify.py` | 89 | `"Set BRAVE_RUN_REAL_EXTERNALS=true to enable real API calls."` | `"Set RUN_REAL_EXTERNALS=true to enable real API calls."` |
| `brave/clients/whatsapp.py` | 13 | `requires BRAVE_RUN_REAL_EXTERNALS=true` (module docstring) | `requires RUN_REAL_EXTERNALS=true` |
| `brave/clients/whatsapp.py` | 66 | `Requires BRAVE_RUN_REAL_EXTERNALS=true` (class docstring) | `Requires RUN_REAL_EXTERNALS=true` |
| `brave/clients/whatsapp.py` | 121 | `RuntimeError: If BRAVE_RUN_REAL_EXTERNALS is not True.` (docstring) | `RuntimeError: If RUN_REAL_EXTERNALS is not True.` |
| `brave/clients/whatsapp.py` | 129 | `"TwilioWhatsAppClient.send_template requires BRAVE_RUN_REAL_EXTERNALS=true."` | `"TwilioWhatsAppClient.send_template requires RUN_REAL_EXTERNALS=true."` |
| `tests/integration/test_atrativos_lane_e2e.py` | 7 | `BRAVE_RUN_REAL_EXTERNALS must be absent / False.` (docstring) | `RUN_REAL_EXTERNALS must be absent / False.` |

**Why:** `AppConfig` has `model_config = SettingsConfigDict(env_prefix="")`, so `run_real_externals` resolves from the bare `RUN_REAL_EXTERNALS` env var. Following the wrong `BRAVE_RUN_REAL_EXTERNALS` in docs silently keeps fake clients running. [VERIFIED: brave/config/settings.py line 238]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Structured LLM output with retry | Custom JSON parse + retry loop | `instructor.AsyncInstructor.create_with_completion` | Handles schema validation failure, feeds error back to model, configurable retries |
| Cost guard enforcement | Custom Redis counter | `cost_guard.pre_dispatch_check` + `record_spend` | Already built, tested, and handles TTL/atomic increment (INCRBYFLOAT) |
| LLM call logging | Custom DB write | `LLMGeneration` model + `session.add()` | Already built, correct schema, T-02-04 safe (no prompt content) |
| provider.data_collection deny | Custom header/body logic | `extra_body={"provider": {"data_collection": "deny"}}` | OpenRouter's documented mechanism; passes through OpenAI SDK cleanly |

**Key insight:** Every infrastructure piece (cost guard, logging model, fake client, protocol) is already built. Phase 6 wires existing pieces together via a new transport class.

---

## Common Pitfalls

### Pitfall 1: `from_provider` reads wrong env var for API key
**What goes wrong:** `instructor.from_provider("openrouter/deepseek-chat")` reads `OPENROUTER_API_KEY` from env, not `BRAVE_LLM_OPENROUTER_API_KEY`. The project's pydantic-settings + `BRAVE_LLM_` prefix stores the key differently.
**Why it happens:** `from_provider` is a convenience function with hardcoded env var names.
**How to avoid:** Use `instructor.from_openai(AsyncOpenAI(base_url=..., api_key=config.openrouter_api_key), ...)` — explicit API key from `LLMConfig`.
[VERIFIED: local .venv instructor 1.15.1 from_provider source]

### Pitfall 2: `MD_JSON` mode raises AssertionError with OpenRouter client
**What goes wrong:** `instructor.from_openai(..., mode=instructor.Mode.MD_JSON)` raises `AssertionError` because instructor validates that OpenRouter only supports TOOLS, OPENROUTER_STRUCTURED_OUTPUTS, or JSON.
**Why it happens:** instructor's `from_openai` detects the OpenRouter base URL via `get_provider(str(client.base_url))` and enforces mode allowlists per provider.
**How to avoid:** For Phase 6, all callers pass `mode="tools"`. Map the `mode` string to `instructor.Mode` at call time. If a future caller passes `mode="md_json"`, raise a `ValueError` with a clear message rather than letting it fail deep in instructor.
[VERIFIED: local .venv instructor 1.15.1 from_openai source]

### Pitfall 3: `max_tokens` is required in Anthropic SDK — no default
**What goes wrong:** `messages.create(model=..., messages=...)` raises a validation error because `max_tokens` has no default in anthropic 0.109.1.
**Why it happens:** It is an explicitly required parameter in `AsyncMessages.create`.
**How to avoid:** Always pass `max_tokens` explicitly in `generate()`. A sensible default for WhatsApp PT-BR follow-ups: 2048.
[VERIFIED: local .venv anthropic 0.109.1 AsyncMessages.create signature]

### Pitfall 4: OpenRouter cost is in `usage.model_extra`, not `usage.cost`
**What goes wrong:** `raw_completion.usage.cost` raises `AttributeError` — there is no typed `.cost` attribute on `CompletionUsage`.
**Why it happens:** OpenRouter injects the `cost` field as an extra, not a typed field. `CompletionUsage` has `model_config = {'extra': 'allow'}` so it lands in `model_extra`.
**How to avoid:** `usage.model_extra.get("cost", 0.0)` with null-safety on `usage` and `usage.model_extra`.
[VERIFIED: local .venv openai 2.41.1 CompletionUsage + openrouter docs usage-accounting]

### Pitfall 5: `create_with_completion` vs `create` — raw response access
**What goes wrong:** Using `create()` returns only the validated Pydantic instance with no access to `usage`.
**Why it happens:** `create()` discards the raw completion. `create_with_completion()` returns `(result, raw_completion)` where `raw_completion = getattr(response, "_raw_response", None)`.
**How to avoid:** Always use `create_with_completion()` on the real path where cost extraction is needed.
[VERIFIED: local .venv instructor 1.15.1 AsyncInstructor source]

### Pitfall 6: claude-sonnet-4-5 is now a legacy model
**What goes wrong:** Not wrong per se — the model still works — but the `FakeLLMClient` default and `LLMClientProtocol.generate()` both specify `"claude-sonnet-4-5"`, which is now a legacy alias. The current equivalent is `claude-sonnet-4-6`.
**Why it happens:** The protocol default was set during Phase 3 before the model lineup advanced.
**How to avoid:** Phase 6 should keep `claude-sonnet-4-5` as the default for the `generate()` method (it is still available, API alias still valid, alias resolves to `claude-sonnet-4-5-20250929`). Update the config default to the newer model ID in a separate follow-on. Do NOT change the protocol default in this phase — it would break `FakeLLMClient` interface consistency.
[CITED: platform.claude.com/docs/en/about-claude/models/overview — Legacy models table]

### Pitfall 7: Anthropic does NOT return a cost field — needs price table
**What goes wrong:** Trying to read cost from `response.usage.cost` on the Anthropic path raises `AttributeError` (no such field on `anthropic.types.Usage`).
**Why it happens:** Unlike OpenRouter, the native Anthropic API returns only token counts, not dollar cost.
**How to avoid:** Compute cost inline: `(input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000` for Sonnet 4.5. Define the prices as module-level constants so they are easy to update.
[VERIFIED: local .venv anthropic 0.109.1 Usage type; CITED: Anthropic pricing docs]

### Pitfall 8: Guard in `__init__` vs guard in method
**What goes wrong:** `TwilioWhatsAppClient` defers the guard to `send_template()`, not `__init__`. This means the object can be constructed with `run_real_externals=False` — only the call fails.
**Why it happens:** Historical choice; lazy guard.
**How to avoid:** Mirror `RealPlacesClient` / `RealApifyClient` pattern: guard in `__init__`. Fail at construction time so `pipeline.py`'s if/else branch is the only guard needed.

---

## Code Examples

### Complete `extract()` call shape (verified API)

```python
# Source: instructor 1.15.1 + openai 2.41.1 — local .venv

from openai import AsyncOpenAI
import instructor

client = instructor.from_openai(
    AsyncOpenAI(api_key="...", base_url="https://openrouter.ai/api/v1"),
    mode=instructor.Mode.TOOLS,
)

result, raw = await client.create_with_completion(
    messages=[{"role": "user", "content": prompt}],
    response_model=MySchema,
    model="deepseek/deepseek-chat",
    extra_body={"provider": {"data_collection": "deny"}},
)
# result: MySchema instance (validated by instructor)
# raw: openai.types.chat.ChatCompletion
usage = raw.usage  # CompletionUsage
usd_cost = float(usage.model_extra.get("cost", 0.0)) if usage and usage.model_extra else 0.0
```

### Complete `generate()` call shape (verified API)

```python
# Source: anthropic 0.109.1 — local .venv

import anthropic

client = anthropic.AsyncAnthropic(api_key="...")
response = await client.messages.create(
    model="claude-sonnet-4-5",  # or config-supplied model
    max_tokens=2048,            # REQUIRED — no default
    messages=messages,          # list[{"role": ..., "content": ...}]
)
text = response.content[0].text  # TextBlock.text
input_tokens = response.usage.input_tokens
output_tokens = response.usage.output_tokens
usd_cost = (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000
```

### Mode string → instructor.Mode mapping

```python
# Source: instructor 1.15.1 Mode enum — local .venv
_MODE_MAP: dict[str, instructor.Mode] = {
    "tools": instructor.Mode.TOOLS,
    "json": instructor.Mode.JSON,
    "md_json": instructor.Mode.MD_JSON,  # NOTE: raises AssertionError with OpenRouter client
}
mode_enum = _MODE_MAP.get(mode, instructor.Mode.TOOLS)
```

### Deny-enforcement unit test pattern

```python
# Source: D-07 design + existing test patterns in tests/integration/
from unittest.mock import AsyncMock, patch

async def test_deny_block_present_in_openrouter_request(monkeypatch):
    """Assert provider.data_collection=deny is in every extract() request."""
    mock_create = AsyncMock(return_value=(FakeResult(), FakeCompletion()))
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_LLM_OPENROUTER_API_KEY", "test-key")

    client = RealLLMClient(config=LLMConfig(openrouter_api_key="test-key"))
    with patch.object(client._instructor_client, "create_with_completion", mock_create):
        await client.extract(prompt="test", schema=SomeSchema)

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["extra_body"]["provider"]["data_collection"] == "deny"
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `from_provider("openrouter/...")` auto-wires key | `from_openai(AsyncOpenAI(base_url=..., api_key=...))` explicit wiring | Phase 6 research | Respects BRAVE_LLM_ prefix convention |
| instructor `create()` — no usage access | `create_with_completion()` — returns `(result, raw)` | instructor 1.x GA | Can extract tokens/cost post-call |
| OpenRouter `usage: {include: true}` (deprecated) | Usage always included automatically | OpenRouter ~2025 | `model_extra["cost"]` always available; no extra param needed |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Anthropic Sonnet 4.5 pricing is $3/MTok input, $15/MTok output | Pattern 5, Common Pitfalls §7 | Wrong cost logged in `usd_cost` field — observability only, not enforcement |
| A2 | OpenRouter model-unavailable returns 404 NotFoundError | Pattern 4 (fallback strategy) | Wrong exception type → fallback not triggered; primary slug failures not retried correctly |
| A3 | `claude-sonnet-4-5` alias remains valid in the Anthropic API | Pattern 5 | API returns error on invalid model → generate() fails entirely |

**A1 note:** Pricing verified from Anthropic docs at time of research but changes over time. The cost field in `llm_generations` is for observability; the enforcing guard is the Redis daily budget, not per-call cost. LOW risk.

**A2 note:** OpenRouter error shapes for model-unavailable are not directly testable from SDK inspection alone. The 404 assumption is based on standard HTTP semantics for "resource not found" and has HIGH confidence but is `[ASSUMED]` until verified with a real API call or OpenRouter error docs.

**A3 note:** Confirmed via Anthropic models overview: `claude-sonnet-4-5` is an active alias resolving to `claude-sonnet-4-5-20250929`. Model is legacy (superseded by `claude-sonnet-4-6`) but still available. [CITED: platform.claude.com/docs/en/about-claude/models/overview]

---

## Open Questions

1. **OpenRouter model-unavailable exception type**
   - What we know: 404 for not-found resources is standard HTTP, and `openai.NotFoundError` has `status_code = 404`.
   - What's unclear: OpenRouter may return a 503 with a body saying "model offline" rather than a 404 for temporarily-unavailable models.
   - Recommendation: Implement fallback on both `NotFoundError` (404) and `APIStatusError` with status 503, matching OpenRouter's documented behavior. Add a TODO comment for the operator to verify in the opt-in smoke test.

2. **Whether to wire `redis_client`/`session` in the four call sites**
   - What we know: `outreach_task` and `resume_conversation_task` already construct a `redis_client` from env. `discover_atrativo_task` and `sweep_uf_task` do not.
   - What's unclear: D-05 says "pure transport when absent" — acceptable to ship without full tracking wiring? Or must two of the four sites be updated?
   - Recommendation: Ship with all four sites unchanged (`RealLLMClient(config=app_config.llm)`). Full tracking available via the optional deps in a follow-on. The `llm_generations` rows require a DB session — adding session wiring to all four sites is low-risk but out of the minimal scope definition.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `instructor` | `extract()` | ✓ (in .venv) | 1.15.1 | — |
| `openai` AsyncOpenAI | `extract()` | ✓ (in .venv) | 2.41.1 | — |
| `anthropic` AsyncAnthropic | `generate()` | ✓ (in .venv) | 0.109.1 | — |
| `tenacity` | retry decorator | ✓ (in .venv) | 9.1.x | — |
| `structlog` | logging | ✓ (in .venv) | 26.x | — |
| `fakeredis` | unit tests | ✓ (in .venv / conftest) | 2.36.x | — |
| `RUN_REAL_EXTERNALS=true` | real-path activation | ✗ (env var, unset in CI) | — | `FakeLLMClient` (default) |
| `BRAVE_LLM_OPENROUTER_API_KEY` | extract() smoke test | ✗ (not in CI) | — | `pytest.mark.skipif` |
| `BRAVE_LLM_ANTHROPIC_API_KEY` | generate() smoke test | ✗ (not in CI) | — | `pytest.mark.skipif` |

**Missing dependencies with no fallback:** None — all code dependencies are installed. Key-gated paths are behind skipif markers.

---

## Sources

### Primary (HIGH confidence — verified via local .venv)
- `brave/clients/base.py` — LLMClientProtocol contract (lines 24–72)
- `brave/clients/places.py` — RealPlacesClient mirror pattern
- `brave/clients/apify.py` — RealApifyClient mirror pattern + footgun locations
- `brave/clients/whatsapp.py` — TwilioWhatsAppClient mirror pattern + footgun locations
- `tests/fakes/fake_llm.py` — FakeLLMClient shapes
- `brave/observability/cost_guard.py` — pre_dispatch_check / record_spend API
- `brave/observability/llm_tracker.py` — LLMTracker.track_and_call pattern
- `brave/core/models.py` — LLMGeneration fields
- `brave/config/settings.py` — LLMConfig + AppConfig field names/prefixes
- `brave/tasks/pipeline.py` lines 665–671, 798–804, 1234–1241, 1428–1434 — four call sites
- instructor 1.15.1 source (`.venv`) — from_openai, from_provider, AsyncInstructor, Mode enum
- openai 2.41.1 source (`.venv`) — AsyncCompletions.create, CompletionUsage model_config
- anthropic 0.109.1 source (`.venv`) — AsyncMessages.create signature, TextBlock, Usage

### Secondary (HIGH confidence — official docs)
- [CITED: platform.claude.com/docs/en/about-claude/models/overview] — claude-sonnet-4-5 alias, pricing table, legacy status
- [CITED: openrouter.ai/docs/guides/routing/provider-selection] — provider.data_collection=deny syntax
- [CITED: openrouter.ai/docs/cookbook/administration/usage-accounting] — usage.cost field via model_extra

---

## Metadata

**Confidence breakdown:**
- instructor wiring (`from_openai`, `create_with_completion`, mode map): HIGH — verified from installed source
- OpenRouter `extra_body` deny block: HIGH — verified from openai SDK + OpenRouter docs
- Anthropic `generate()` call shape: HIGH — verified from installed SDK source
- Cost extraction (OpenRouter `model_extra["cost"]`): HIGH — verified via Python inspection
- Anthropic cost (price table): MEDIUM — pricing from docs, subject to change
- OpenRouter slug-fallback exception type: MEDIUM — 404→NotFoundError is HIGH; 503-as-503 vs 503-as-4xx is ASSUMED

**Research date:** 2026-06-17
**Valid until:** 2026-09-17 (stable libraries; re-verify anthropic model ID and OpenRouter model lineup before use)
