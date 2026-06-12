---
phase: 02-destinos-lane
plan: "08"
subsystem: destinos-lane
tags: [desmembramento, llm-agent, quarantine, d18-boundary, dest-03, test-02]
dependency_graph:
  requires: [02-03, 02-05]
  provides: [DesmembramentoAgent, quarantine_poison-in-core, test_desmembramento]
  affects: [brave/core/quarantine.py, brave/tasks/pipeline.py, brave/lanes/destinos/desmembramento.py]
tech_stack:
  added: []
  patterns: [validate-or-quarantine-d11, origem-40-d06-firewall, fan-out-per-oferta-principal]
key_files:
  created:
    - brave/core/quarantine.py
    - brave/lanes/destinos/desmembramento.py
    - tests/unit/test_desmembramento.py
  modified:
    - brave/tasks/pipeline.py
decisions:
  - "quarantine_poison relocated to brave/core/quarantine.py so lane code (desmembramento.py) can import from core without depending on tasks layer (D-18 fix)"
  - "DesmembramentoAgent does not call process_nascente_record after store_raw — origem=40 records need steward validation before Rio processing is meaningful"
metrics:
  duration: 13m
  completed: "2026-06-12T17:25:49Z"
  tasks_completed: 2
  files_changed: 4
requirements: [DEST-03, TEST-02]
---

# Phase 2 Plan 08: DesmembramentoAgent Summary

DesmembramentoAgent fan-out with validate-or-quarantine (D-11) and D-18 boundary fix: quarantine_poison relocated to brave/core/quarantine.py so lane code imports from core, not tasks.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | DesmembramentoAgent + quarantine relocation | b54553e | brave/core/quarantine.py, brave/tasks/pipeline.py, brave/lanes/destinos/desmembramento.py |
| 2 | Offline unit tests for DesmembramentoAgent | 514fab4 | tests/unit/test_desmembramento.py |

## What Was Built

### brave/core/quarantine.py (new)
`quarantine_poison` extracted from `brave/tasks/pipeline.py` into a standalone core module. Both `brave/tasks/pipeline.py` and `brave/lanes/destinos/desmembramento.py` now import from `brave.core.quarantine`. The tasks layer re-exports via `from brave.core.quarantine import quarantine_poison  # noqa: F401` so existing callers have zero behavior change.

### brave/lanes/destinos/desmembramento.py (new)
`DesmembramentoAgent` with:
- `__init__(llm_client, mtur_client, session, config)`
- `async produce(uf)` — fan-out loop over Oferta Principal municipalities
- `DESMEMBRAMENTO_PROMPT` string constant (PT-BR structured extraction prompt)
- `_completude_desmembramento(destino)` — 75/50/25 scoring helper

Fan-out behavior:
- Filters to `categoria == "Oferta Principal"` (Complementar/Apoio silently skipped)
- Calls `llm_client.extract(prompt, DesmembramentoResult, mode="tools")`
- On success: iterates `result.destinos`, writes each via `store_raw(source="desm", source_ref=f"desm:{uf}:{ibge_code}:{slug}", ...)`
- On any exception: `quarantine_poison(session, nascente_id=None, task_name="brave.desmembramento", ...)` then `continue` (no propagation — D-11)
- All records carry `origem_value=40.0`, `source_note="LLM-generated, pending validation"`, `validacao_humana_value=0.0` (D-06 firewall)

### tests/unit/test_desmembramento.py (new)
Four offline tests using `FakeLLMClient` + `FakeMturClient` + `db_session` fixture:
1. `test_desmembramento_agent_happy_path` — NascenteRecord with source="desm", origem=40
2. `test_desmembramento_agent_malformed_output_quarantined` — PoisonQuarantine row; no NascenteRecord
3. `test_desmembramento_agent_empty_destinos_skips` — empty destinos → no error, no records
4. `test_desmembramento_agent_skips_non_oferta_principal` — Complementar → zero LLM calls

## Verification Results

```
tests/unit/test_desmembramento.py ....   4 passed
tests/integration/test_celery_tasks.py ...   3 passed (pipeline re-export unchanged)
```

D-18 boundary verified: `grep -n "^from brave.tasks\|^import brave.tasks" brave/lanes/destinos/desmembramento.py` returns empty (matches in comments only, not imports).

## Deviations from Plan

None — plan executed exactly as written.

The one minor implementation detail: `DesmembramentoAgent.produce` does NOT call `process_nascente_record` after `store_raw`. This is intentional — origin=40 records need steward validation before re-scoring is useful, and the plan's behavior spec for produce does not include the Rio pipeline step (unlike MturSeedIngest). The scoring gate (D-06) means these records land in DLQ anyway; running the Rio pipeline at ingest time would be wasteful and premature.

## Known Stubs

None — all behavior is fully implemented and verified by the offline test suite.

## Threat Flags

No new threat surface introduced beyond what is in the plan's threat model. All LLM output goes through the DesmembramentoResult Pydantic schema (instructor 2nd-layer validation) before any data reaches Nascente; malformed output routes to PoisonQuarantine, not Nascente.

## Self-Check: PASSED

- brave/core/quarantine.py exists and exports `quarantine_poison`
- brave/lanes/destinos/desmembramento.py exists and imports from `brave.core.quarantine`
- brave/tasks/pipeline.py re-exports `quarantine_poison` from core
- tests/unit/test_desmembramento.py exists with 4 tests
- Commits b54553e and 514fab4 exist on main
