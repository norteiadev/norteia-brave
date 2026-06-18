---
phase: 06-real-externals-enablement-realllmclient-live-24-7-collection
verified: 2026-06-17T00:00:00Z
status: human_needed
score: 7/7
overrides_applied: 0
human_verification:
  - test: "Run RUN_REAL_EXTERNALS=true BRAVE_LLM_OPENROUTER_API_KEY=<key> python -m pytest tests/integration/test_real_llm_smoke.py -v -s"
    expected: "test_smoke_extract_real_openrouter passes: result is a _Ping instance with non-empty answer field; no ModuleNotFoundError, no cost guard error"
    why_human: "Requires a live OpenRouter API key and real network call; CI runs keyless by design (D-07). Structural wiring is verified offline; live dispatch can only be confirmed by an operator with a key."
---

# Phase 6: Real-Externals Enablement — Verification Report

**Phase Goal:** Implement the single missing real client `brave/clients/llm.py` / `RealLLMClient` (LLMClientProtocol: extract+generate) so the existing 24/7 sweep/beat orchestration runs on real external data when `RUN_REAL_EXTERNALS=true` — with NO ModuleNotFoundError, the USD cost guard enforced, and real `llm_generations` rows written on the real path; and fix the `BRAVE_RUN_REAL_EXTERNALS` docstring footgun (real toggle is `RUN_REAL_EXTERNALS`).
**Verified:** 2026-06-17
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `brave/clients/llm.py` / `RealLLMClient` exists and imports cleanly with no `ModuleNotFoundError` | VERIFIED | `.venv/bin/python -c "from brave.clients.llm import RealLLMClient"` exits 0; file is 370 lines of substantive implementation |
| 2 | `RealLLMClient` implements `LLMClientProtocol` (extract+generate signatures match) | VERIFIED | `extract(self, prompt, schema, mode)` and `generate(self, messages, model)` confirmed via `inspect.signature`; `_check_protocol_compliance()` function present |
| 3 | `run_real_externals` guard raises `RuntimeError` at `__init__` time (fail-closed) | VERIFIED | Behavioral spot-check: guard fires with message containing "run_real_externals=False" when env var absent; mirrors `RealPlacesClient` pattern |
| 4 | `extract()` uses `instructor.from_openai` + `Mode.TOOLS` + `create_with_completion` + slug fallback; `extra_body` `provider.data_collection="deny"` present on every OpenRouter call | VERIFIED | `llm.py:153` `instructor.from_openai(…, mode=instructor.Mode.TOOLS)`; `llm.py:181-186` `create_with_completion` with `extra_body={"provider": {"data_collection": self._config.provider_data_collection}}`; slug fallback loop `llm.py:229-247` |
| 5 | `generate()` uses `AsyncAnthropic` with `max_tokens=2048`; cost guard (`pre_dispatch_check`/`record_spend`) + `LLMGeneration` rows written with no prompt content | VERIFIED | `llm.py:311-313` `AsyncAnthropic.messages.create(model=model, max_tokens=2048, …)`; `llm.py:225-226` and `llm.py:308-309` cost guard calls; `LLMGeneration` at lines 271-280 and 340-349 — no `prompt=` kwarg; T4 unit test confirms row written with `usd_cost > 0` and `lane == "test"` |
| 6 | Footgun eliminated: zero occurrences of `BRAVE_RUN_REAL_EXTERNALS` in `brave/` and `tests/` | VERIFIED | `grep -rn "BRAVE_RUN_REAL_EXTERNALS" brave/ tests/` returns empty; all 7 occurrences replaced: `places.py:95`, `apify.py:89`, `whatsapp.py:13/66/121/129`, `test_atrativos_lane_e2e.py:7` |
| 7 | All 4 `pipeline.py` `RealLLMClient` call sites pass `redis_client=redis_client, session=session` (cost guard + `llm_generations` on real path); no `# type: ignore[import]` remaining | VERIFIED | `grep -Fc "RealLLMClient(config=app_config.llm, redis_client=redis_client, session=session"` returns 4; `grep "type: ignore\[import\]"` returns 0; confirmed at lines 671, 807, 1247, 1439 |

**Score:** 7/7 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/clients/llm.py` | `RealLLMClient` implementing `LLMClientProtocol` | VERIFIED | 370 lines; `class RealLLMClient`, `instructor.from_openai`, `create_with_completion`, `provider_data_collection`, `AsyncAnthropic`, `max_tokens=2048`, `pre_dispatch_check`, `record_spend`, `LLMGeneration` all present |
| `tests/unit/clients/test_real_llm_client.py` | 5 offline unit tests (D-07) | VERIFIED | Contains all 5 required test functions; runs in 1.86s with no network |
| `tests/integration/test_real_llm_smoke.py` | Opt-in smoke test skipped without key | VERIFIED | `skipif` gate on `_HAS_OPENROUTER_KEY and _HAS_REAL_EXTERNALS`; skips cleanly in CI |
| `brave/tasks/pipeline.py` | Clean imports (no `type: ignore`) + 4 wired call sites | VERIFIED | Zero `type: ignore[import]` on `RealLLMClient` lines; 4 instances of `RealLLMClient(config=app_config.llm, redis_client=redis_client, session=session` |
| `brave/clients/places.py` | Docstring says `RUN_REAL_EXTERNALS=true` | VERIFIED | Line 95: `"Set RUN_REAL_EXTERNALS=true to enable real API calls."` |
| `brave/clients/apify.py` | Docstring says `RUN_REAL_EXTERNALS=true` | VERIFIED | Line 89: `"Set RUN_REAL_EXTERNALS=true to enable real API calls."` |
| `brave/clients/whatsapp.py` | All 4 occurrences say `RUN_REAL_EXTERNALS=true` | VERIFIED | Lines 13, 66, 121, 129 all use `RUN_REAL_EXTERNALS` |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `brave/clients/llm.py RealLLMClient` | `brave/clients/base.py LLMClientProtocol` | structural typing | VERIFIED | `extract(prompt, schema, mode)` and `generate(messages, model)` match protocol signatures; `_check_protocol_compliance()` present |
| `brave/clients/llm.py extract()` | `brave/observability/cost_guard.py pre_dispatch_check` | direct call when `redis_client is not None` | VERIFIED | `llm.py:225-226`: `if self._redis_client is not None: pre_dispatch_check(self._redis_client, self._config)` |
| `brave/clients/llm.py extract()` | `brave/core/models.py LLMGeneration` | `session.add(LLMGeneration(...))` | VERIFIED | `llm.py:270-281`: adds `LLMGeneration` with `lane`, `model_slug`, tokens, `usd_cost` — no prompt content |
| `brave/tasks/pipeline.py` (4 sites) | `brave/clients/llm.py RealLLMClient` | `redis_client=redis_client, session=session` | VERIFIED | Lines 671, 807, 1247, 1439 — all 4 sites pass `redis_client` and `session` |
| `brave/config/settings.py AppConfig` | `run_real_externals` env var | `env_prefix=""` → bare `RUN_REAL_EXTERNALS` | VERIFIED | No `BRAVE_RUN_REAL_EXTERNALS` occurrences remain anywhere; docstrings corrected to match |

### Data-Flow Trace (Level 4)

`RealLLMClient` is not a rendering component but a transport client with observable side-effects. The data flow: LLM response usage → `LLMGeneration` row → database.

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `brave/clients/llm.py extract()` | `usd_cost`, `prompt_tokens`, `completion_tokens` | `raw.usage` from `create_with_completion` response | Yes — parsed from real provider response via `usage.model_extra.get("cost", 0.0)` | FLOWING |
| `brave/clients/llm.py generate()` | `usd_cost`, `prompt_tokens`, `completion_tokens` | `response.usage.input_tokens` / `.output_tokens` from `AsyncAnthropic` | Yes — computed from real Anthropic response usage | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `RealLLMClient` imports cleanly | `.venv/bin/python -c "from brave.clients.llm import RealLLMClient; print('import ok')"` | `import ok` | PASS |
| Guard raises `RuntimeError` when `run_real_externals=False` | Python guard test (see Step 7b) | `PASS: guard raised with correct message` | PASS |
| Structural protocol compliance (`extract` + `generate` signatures) | `inspect.signature` check | Both params match; `PASS` printed | PASS |
| 5 offline unit tests pass | `.venv/bin/python -m pytest tests/unit/clients/test_real_llm_client.py -v` | `5 passed in 1.86s` | PASS |
| Smoke test skips without key | `.venv/bin/python -m pytest tests/integration/test_real_llm_smoke.py -v` | `1 skipped in 0.01s` | PASS |
| Full offline suite | `.venv/bin/python -m pytest tests/ --ignore=tests/integration/test_real_llm_smoke.py` | `393 passed, 1 warning in 20.85s` | PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` files declared or found for Phase 6. Phase 6 uses pytest as its verification mechanism. Step 7c: SKIPPED (no probe scripts).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| OBS-01 | 06-02, 06-03 | Every LLM call logged in `llm_generations` (per-lane, per-model, USD cost) | SATISFIED | `LLMGeneration` row written in both `extract()` and `generate()` with `lane`, `model_slug`, `prompt_tokens`, `completion_tokens`, `usd_cost`; T4 unit test confirms row + `usd_cost > 0` |
| OBS-02 | 06-02, 06-03 | USD cost guard enforces spend ceiling | SATISFIED | `pre_dispatch_check` called before every LLM dispatch in both `extract()` and `generate()`; all 4 pipeline call sites pass `redis_client` so guard fires on the real path |
| CORE-11 | 06-02 | Every external system behind a client interface with a fake | SATISFIED | `RealLLMClient` implements `LLMClientProtocol`; `FakeLLMClient` already existed; the protocol boundary is now complete for the LLM client (the last missing real implementation) |
| TEST-01 | 06-01, 06-02, 06-03 | Full suite 100% offline; real externals opt-in by flag; CI keyless | SATISFIED | 393 tests pass offline; smoke test skips without key; `RUN_REAL_EXTERNALS=false` guard prevents any real call in CI |

**Note on traceability:** REQUIREMENTS.md traceability table maps OBS-01, OBS-02, CORE-11, TEST-01 to Phase 1 with "Complete" status. Phase 6 extends these requirements' implementation to the LLM client specifically (the RealLLMClient gap was not present at Phase 1). The traceability table is informational and was not updated for Phase 6's contribution — this is a documentation gap, not a blocker.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `brave/clients/llm.py` | 247, 314, 317 | `# type: ignore[misc/arg-type/union-attr]` | Info | Legitimate suppressions: `[misc]` on re-raising a typed-as-None exception (Python limitation), `[arg-type]` on Anthropic SDK messages type narrowing, `[union-attr]` on `TextBlock.text` access. None block real calls. |
| `brave/clients/llm.py` | 67 | `# NOTE: raises AssertionError on OpenRouter client` | Info | Intentional documentation of a known instructor limitation; no behavior impact. |

No `TBD`, `FIXME`, `XXX`, `TODO`, `HACK`, or `PLACEHOLDER` markers found in any phase-modified file.

### Human Verification Required

#### 1. Live RealLLMClient smoke test (opt-in, requires real key)

**Test:** With a valid OpenRouter key: `RUN_REAL_EXTERNALS=true BRAVE_LLM_OPENROUTER_API_KEY=<key> .venv/bin/python -m pytest tests/integration/test_real_llm_smoke.py -v -s`

**Expected:** `test_smoke_extract_real_openrouter` passes — `result` is a `_Ping` instance with a non-empty `answer` field. No `ModuleNotFoundError`, no `CostGuardError` (assuming budget not exhausted), no `NotFoundError` from the primary slug.

**Why human:** Requires a live OpenRouter API key and real network call to the DeepSeek endpoint. CI runs keyless by design (D-07). All structural wiring is verified offline via the 5 unit tests and pipeline grep assertions. The smoke test is the only remaining path to confirm the live transport works end-to-end. This is explicitly scoped by D-07 as "real network verified only by an opt-in smoke path."

---

## Gaps Summary

No gaps. All 7 observable truths are VERIFIED. The human verification item is not a gap — it is the D-07-designed opt-in confirmation path for the live network call, intentionally deferred from CI. The phase goal is structurally achieved: `RealLLMClient` exists, imports, guards, wires cost guard and `llm_generations`, the footgun is eliminated, and 393 offline tests confirm no regressions.

---

_Verified: 2026-06-17_
_Verifier: Claude (gsd-verifier)_
