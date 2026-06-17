# Phase 6: Real-Externals Enablement (RealLLMClient + live 24/7 collection) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-17
**Phase:** 6-Real-Externals Enablement (RealLLMClient + live 24/7 collection)
**Mode:** `--auto` (all gray areas auto-resolved with the recommended option; no interactive prompts)
**Origin:** real-data dogfooding gap found in Phase 4/5 — `brave/clients/llm.py`/`RealLLMClient` does not exist; the four `from brave.clients.llm import RealLLMClient  # type: ignore[import]` sites in pipeline.py ImportError the moment `run_real_externals=True`. RealPlaces/RealApify/TwilioWhatsApp all exist; only the LLM client is missing, so every LLM lane is blocked in real mode.
**Areas discussed:** RealLLMClient structure, extract() transport, generate() transport, cost guard + logging, toggle footgun, offline test strategy, scope fence

---

## RealLLMClient structure
| Option | Description | Selected |
|--------|-------------|----------|
| Single `brave/clients/llm.py` / `RealLLMClient` (extract+generate), mirror RealPlacesClient | One class, signature-compatible with the 4 call sites | ✓ |
| Split deepseek-client + anthropic-client modules | Two classes; more surface, more wiring | |
**Auto-selected:** D-01/D-02. Keep `RealLLMClient(config=app_config.llm)` signature; guard+tenacity+structlog like places.py.

## extract() transport (DeepSeek / structured)
| Option | Description | Selected |
|--------|-------------|----------|
| AsyncOpenAI→OpenRouter base_url + instructor Mode.Tools, deny + ordered fallback | Locked LLM split; 2nd-layer Pydantic via instructor | ✓ |
| Raw `response_format: json_schema` (no instructor) | DeepSeek schema adherence looser; rejected in CLAUDE.md | |
**Auto-selected:** D-03/D-04. Enforce `provider.data_collection="deny"` in every request body (asserted in test).

## generate() transport (Sonnet / conversation)
| Option | Description | Selected |
|--------|-------------|----------|
| Native AsyncAnthropic, Sonnet | Locked split (quality/quota/streaming) | ✓ |
| Sonnet via OpenRouter | Loses native control; off-spec | |
**Auto-selected:** D-05a. Off the live-collection critical path but implemented for protocol completeness.

## Cost guard + llm_generations logging
| Option | Description | Selected |
|--------|-------------|----------|
| In-client self-tracking via existing cost_guard + LLMGeneration; optional redis/session deps; no-op when absent | Honors protocol mandate; call sites unchanged; fake stays zero-cost | ✓ |
| Wrap every lane agent in LLMTracker | Rewires agent signatures; bigger blast radius | |
**Auto-selected:** D-05. Real usage (tokens/usd) parsed from provider response.

## Toggle footgun
| Option | Description | Selected |
|--------|-------------|----------|
| Fix docstrings/errors `BRAVE_RUN_REAL_EXTERNALS`→`RUN_REAL_EXTERNALS` in apify/whatsapp/places | Real toggle has no BRAVE_ prefix (env_prefix=""); current docs yield silent fakes | ✓ |
| Leave as-is | Persistent operator footgun | |
**Auto-selected:** D-06. Doc/string-only, no behavior change.

## Offline test strategy
| Option | Description | Selected |
|--------|-------------|----------|
| 100% offline default (guard-raise + deny-in-body + fallback + cost-guard, mocked); opt-in real smoke skipped w/o key | Preserves keyless CI mandate | ✓ |
| Require live key for the LLM tests | Breaks offline mandate | |
**Auto-selected:** D-07.

## Scope fence
| Option | Description | Selected |
|--------|-------------|----------|
| No new lanes/orchestration/auto-send; enable real LLM behind existing sweep/beat | Phase 5 owns orchestration; WhatsApp stays human-gated | ✓ |
**Auto-selected:** scope anchored in `<domain>`.

## Claude's Discretion
instructor wiring (`from_openai` vs `from_provider`), deny-block injection point, tenacity predicate reuse, token/cost extraction helper placement, test fixture layout, commit granularity, `generate()` token-logging parity.

## Deferred Ideas
`:nitro` batch slug · dashboard cost backfill beyond llm_generations · uniform LLMTracker wrapper migration · Anthropic streaming for WhatsApp.
