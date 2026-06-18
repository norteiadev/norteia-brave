---
status: partial
phase: 06-real-externals-enablement-realllmclient-live-24-7-collection
source: [06-VERIFICATION.md]
started: 2026-06-17T00:00:00Z
updated: 2026-06-17T00:00:00Z
---

## Current Test

[awaiting human testing — requires a real BRAVE_LLM_OPENROUTER_API_KEY]

## Tests

### 1. Live OpenRouter smoke test (opt-in, real network)
expected: With `RUN_REAL_EXTERNALS=true` and a real `BRAVE_LLM_OPENROUTER_API_KEY` set, `.venv/bin/python -m pytest tests/integration/test_real_llm_smoke.py` runs (not skipped) and `test_smoke_extract_real_openrouter` passes with a non-empty extracted result — proving the real DeepSeek-via-OpenRouter path (instructor Mode.TOOLS + provider.data_collection=deny) works end-to-end.
result: [pending]

### 2. Real Destinos+Atrativos sweep to the human gates
expected: With `RUN_REAL_EXTERNALS=true` + `BRAVE_LLM_OPENROUTER_API_KEY` (+ `BRAVE_PLACES_API_KEY` for Atrativos), `.venv/bin/python -m brave.cli sweep BA` runs the real sweep with zero `ModuleNotFoundError`, writes real `llm_generations` rows (cost guard enforced — `pre_dispatch_check` fires), and advances Atrativos to `aguardando_consulta_whatsapp` with NO automated WhatsApp send.
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
