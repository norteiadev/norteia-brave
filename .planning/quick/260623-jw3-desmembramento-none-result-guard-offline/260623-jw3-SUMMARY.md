---
quick_id: 260623-jw3
slug: desmembramento-none-result-guard-offline
date: 2026-06-23
status: complete
commit: a49ebbd
---

# Summary: Desmembramento None-result guard (offline NullLLMClient)

## Problem (found during Phase 10 live `nascente_rio` test)
`DesmembramentoAgent.produce()` accessed `result.destinos` immediately after
`llm_client.extract()`. The offline `NullLLMClient.extract()` returns `None`
(by design — it is an import-safety stub, not a functional fake), so
`result.destinos` raised `AttributeError`. That exception escaped `produce()`,
`sweep_uf` caught it generically, rolled back the shared session, retried 3×,
then failed — silently discarding the Mtur seed records written earlier in the
same transaction. Observed live: an offline `nascente_rio` sweep of AP left the
UF with **0 nascente + 0 rio** rows.

## Fix
`brave/lanes/destinos/desmembramento.py` — after the extract try/except, guard
`if result is None: continue` (treat as "no sub-destinos extracted"). Real LLM
clients return a populated `DesmembramentoResult`, so this only changes the
offline/degenerate path. Also defends against a real LLM returning a null result.

## Test
`tests/unit/test_desmembramento.py::test_desmembramento_offline_null_llm_does_not_crash`
— NullLLMClient + Oferta-Principal município → `produce()` does not raise and
writes zero `source="desm"` records. `pytest tests/unit/test_desmembramento.py -q` → **5 passed**.

## Live re-test (after fix)
`POST /engine/start {depth: nascente_rio, lane: destinos, ufs: [AP]}` →
AP: 10 nascente, 10 rio (all `routing=dlq`, score 60.50), `mar=0`,
`llm_generations=0`, WhatsApp gate=0. Confirms the `nascente_rio` cost boundary:
Rio runs, Mar promotion + WhatsApp blocked.

## Follow-up (not fixed here — out of scope)
`sweep_uf` runs MturSeedIngest + DesmembramentoAgent in one shared session with a
single commit, so ANY Desmembramento failure still rolls back the Mtur seed.
Consider committing the Mtur seed before running Desmembramento, or isolating the
two producers in separate transactions. Tracked as a robustness concern.

## Commit
`a49ebbd` — fix(desmembramento): skip on None LLM result instead of crashing the sweep
