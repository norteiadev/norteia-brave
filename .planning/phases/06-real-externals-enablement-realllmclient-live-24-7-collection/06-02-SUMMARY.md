---
phase: "06"
plan: "02"
subsystem: "clients"
tags: ["llm", "openrouter", "instructor", "anthropic", "cost-guard", "real-client"]
dependency_graph:
  requires:
    - "brave/clients/base.py (LLMClientProtocol)"
    - "brave/observability/cost_guard.py (pre_dispatch_check, record_spend)"
    - "brave/core/models.py (LLMGeneration)"
    - "brave/config/settings.py (LLMConfig, AppConfig)"
  provides:
    - "brave/clients/llm.py RealLLMClient"
  affects:
    - "brave/tasks/pipeline.py (4 call sites — type: ignore[import] now resolvable)"
tech_stack:
  added: []
  patterns:
    - "instructor.from_openai(AsyncOpenAI@OpenRouter, Mode.TOOLS) for structured extraction"
    - "AsyncAnthropic native SDK for generate() — NOT via OpenRouter"
    - "tenacity retry on transient openai exceptions + slug fallback on NotFoundError"
    - "Optional tracking deps pattern (redis_client + session) — pure transport when absent"
key_files:
  created:
    - "brave/clients/llm.py"
  modified: []
decisions:
  - "D-03: extract() uses instructor Mode.TOOLS + OpenRouter — mode='tools' is the only supported value"
  - "D-04: extra_body provider.data_collection deny locked into every create_with_completion call"
  - "D-05a: generate() uses AsyncAnthropic directly with max_tokens=2048 (no default in 0.109.x)"
  - "D-05: optional tracking deps (redis_client/session/lane) — cost guard + LLMGeneration only when present"
  - "T-02-04: LLMGeneration stores slug/tokens/usd_cost ONLY — prompt content never persisted"
metrics:
  duration: "~2 minutes"
  completed: "2026-06-18"
  tasks_completed: 1
  files_created: 1
  files_modified: 0
---

# Phase 06 Plan 02: RealLLMClient Implementation Summary

**One-liner:** instructor Mode.TOOLS + AsyncOpenAI@OpenRouter for extract() and native AsyncAnthropic for generate(), with run_real_externals guard, tenacity retry, slug fallback, and optional cost-guard/LLMGeneration tracking.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Implement RealLLMClient (brave/clients/llm.py) | 9e97fef | brave/clients/llm.py (created, 369 lines) |

## What Was Built

Created `brave/clients/llm.py` containing `RealLLMClient` — the single missing piece blocking every LLM-bearing lane in real mode.

### Key implementation details

**Guard (D-02, T-06-02-05):** `__init__` imports `AppConfig` inside the method (avoids circular import, mirrors `RealPlacesClient`). Raises `RuntimeError` when `AppConfig().run_real_externals` is False, or when `config.openrouter_api_key` is empty. Fail-closed at construction time.

**extract() — OpenRouter/DeepSeek (D-03, D-04):**
- `instructor.from_openai(AsyncOpenAI(base_url=openrouter_base_url, ...), mode=Mode.TOOLS)` built at `__init__` time
- `_call_slug()` internal method decorated with `@retry(retry_if_exception(_is_openai_retryable), stop_after_attempt(3), wait_exponential(...))` — retries on `RateLimitError`, `InternalServerError`, `APIConnectionError`, `APITimeoutError`
- Slug fallback loop: primary → `deepseek_fallback_slugs` on `NotFoundError`; `BadRequestError`/`PermissionDeniedError` re-raised immediately (permanent errors)
- `extra_body={"provider": {"data_collection": self._config.provider_data_collection}}` injected on every `create_with_completion` call (D-04, T-06-02-03)
- Cost parsed from `raw.usage.model_extra.get("cost", 0.0)` (OpenRouter-specific field)

**generate() — native Anthropic (D-05a):**
- `AsyncAnthropic(api_key=config.anthropic_api_key).messages.create(model=model, max_tokens=2048, messages=messages)`
- `max_tokens=2048` required — anthropic 0.109.x has no default (Research Pitfall 7)
- Cost computed from price table: `(input * 3.0 + output * 15.0) / 1_000_000` (no cost field in Anthropic response)

**Optional tracking deps (D-05, T-02-04):**
- When `redis_client is not None`: `pre_dispatch_check` called before LLM dispatch
- When `redis_client and session are both not None`: `record_spend` + `LLMGeneration(id, lane, model_slug, resolved_provider, prompt_tokens, completion_tokens, usd_cost)` written and flushed
- `LLMGeneration` rows NEVER contain prompt content (T-02-04) — only slug, tokens, cost

## Deviations from Plan

None — plan executed exactly as written.

## Threat Model Compliance

| Threat ID | Status | Notes |
|-----------|--------|-------|
| T-06-02-01 (API key disclosure) | Mitigated | Keys come from LLMConfig fields only; never passed to structlog |
| T-06-02-02 (prompt content in llm_generations) | Mitigated | LLMGeneration receives only slug/tokens/usd_cost; verified by field inspection |
| T-06-02-03 (data_collection compliance) | Mitigated | provider_data_collection injected in every create_with_completion via extra_body |
| T-06-02-04 (cost budget overshoot) | Mitigated | pre_dispatch_check (atomic Redis) called before every LLM dispatch |
| T-06-02-05 (accidental real LLM in CI) | Mitigated | Hard guard in __init__ raises RuntimeError when run_real_externals=False |

## Known Stubs

None — all methods are fully implemented. The `_check_protocol_compliance()` footer is intentionally a `pass` (guard prevents instantiation without real externals; this is the documented comment-only compliance pattern from places.py).

## Threat Flags

None — no new network endpoints, auth paths, or schema changes introduced beyond what the plan's threat model covers.

## Self-Check: PASSED

- `brave/clients/llm.py` exists: FOUND
- Commit `9e97fef` exists: FOUND
- `.venv/bin/python -c "from brave.clients.llm import RealLLMClient"` exits 0: PASS
- Guard test (run_real_externals=False → RuntimeError): PASS
- All grep checks (class, create_with_completion, provider_data_collection, pre_dispatch_check, LLMGeneration): PASS
- No prompt content in LLMGeneration fields: PASS
