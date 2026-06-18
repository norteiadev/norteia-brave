---
phase: "07"
plan: "05"
subsystem: scripts
tags: [load-test, harness, acceptance-gate, destinos, atrativos, real-externals]
dependency_graph:
  requires:
    - "07-01"  # RealPlacesClient D-01/D-02 fixes (field masks + municipio fields)
    - "07-02"  # validate_and_promote_rio extracted to brave/core/dlq/service.py
    - "07-03"  # DiscoveryAgent.produce_for_destino targeted discovery
  provides:
    - scripts/loadtest_destinos_atrativos.py
  affects: []
tech_stack:
  added: []
  patterns:
    - "ingest_destinos.py structure mirrored exactly (main(), env checks, create_engine, sessionmaker, asyncio.run)"
    - "4-step harness: MturSeedIngest → validate_and_promote_rio × N → DiscoveryAgent.produce_for_destino × N → summary"
    - "DB non-clean warning pattern (WARNING + manual TRUNCATE command, no auto-reset)"
    - "Cost guard operator note printed before run"
    - "ACCEPTANCE: PASS/FAIL gate on 10 Mar destinos + >=10 Rio atrativos per parent_mar_id"
key_files:
  created:
    - scripts/loadtest_destinos_atrativos.py
  modified: []
decisions:
  - "Build ibge_lookup from all BR UFs (best-effort) so name→IBGE resolution works for any target UF, not just BA"
  - "Use LOAD_TARGET_DESTINOS and ATRATIVO_TARGET_COUNT env vars for CLI-configurable acceptance bars without argparse complexity"
  - "Print routing summary after ingest (Step 1) AND after full run (Step 4) for diagnostics"
metrics:
  duration_seconds: 130
  completed_date: "2026-06-18"
  tasks_completed: 1
  tasks_total: 1
  files_created: 1
  files_modified: 0
---

# Phase 07 Plan 05: Load-Test Harness (Destinos × Atrativos) Summary

**One-liner:** Real E2E load-test harness driving MturSeedIngest → DLQ→Mar promotion → targeted DiscoveryAgent.produce_for_destino → ACCEPTANCE: PASS/FAIL summary for 10×10 operator UAT.

## What Was Built

`scripts/loadtest_destinos_atrativos.py` — a 4-step operator UAT harness that exercises all Phase 7 fixes together against a live DB + Google Places API + DeepSeek LLM.

### The 4-Step Flow

| Step | Action | Key Function |
|------|--------|--------------|
| 0 | DB non-clean warning | Prints existing Mar count + manual TRUNCATE command |
| 1 | Ingest destinos for chosen UF(s) | `MturSeedIngest.produce(uf)` → DLQ |
| 2 | Promote ≤10 DLQ destinos to Mar | `validate_and_promote_rio(session, rio, score_config)` |
| 3 | Targeted atrativos discovery | `DiscoveryAgent.produce_for_destino(mar, target_count=10)` |
| 4 | Summary + acceptance gate | Prints `ACCEPTANCE: PASS` iff 10 Mar destinos + ≥10 atrativos/parent |

### Key Design Points

- **Never auto-resets DB** — prints `WARNING: {n} active Mar destinos already exist` + manual `TRUNCATE ... CASCADE` command for operator to run before a clean baseline.
- **Cost guard note** — prints operator guidance about `BRAVE_LLM_USD_DAILY_BUDGET` before any LLM calls; default $10 covers the test (~$0.02), but raises to $50 if needed.
- **Three required env keys** — fails early with a clear `ERROR:` message if `BRAVE_DB_URL`, `BRAVE_PLACES_API_KEY`, or `BRAVE_LLM_OPENROUTER_API_KEY` is missing.
- **Full ibge_lookup** — builds name→IBGE resolution from all 27 BR UFs (best-effort) so `RealPlacesClient` can resolve municipality IBGE codes for any target UF.
- **CLI**: `LOAD_TARGET_DESTINOS` and `ATRATIVO_TARGET_COUNT` env vars control acceptance bars without argparse complexity.

### Operator Run

```bash
set -a; source .env; set +a
.venv/bin/python -m scripts.loadtest_destinos_atrativos BA
# defaults to BA if no UF given
```

## Verification Results

| Check | Result |
|-------|--------|
| Syntax parse (`ast.parse`) | PASS |
| Import check (no keys needed) | PASS |
| Content assertions (validate_and_promote_rio, produce_for_destino, TRUNCATE, CostGuardError, ACCEPTANCE) | PASS |
| Unit tests `tests/unit/ -x` | 229 passed, 4 skipped |
| Full offline suite `tests/ --ignore=tests/integration/test_real_llm_smoke.py` | 401 passed, 0 failed |

## Deviations from Plan

None — plan executed exactly as written. The script structure mirrors `scripts/ingest_destinos.py` exactly (from `__future__` import, asyncio, os, sys, sqlalchemy, main(), `if __name__ == "__main__"`). All 5 `must_haves.truths` satisfied.

## Known Stubs

None. This is an operator-run harness script — it uses real clients and real DB. No data is mocked or stubbed.

## Threat Flags

None. The threat model entries T-07-13 through T-07-SC were reviewed:
- API keys are existence-checked (`BRAVE_PLACES_API_KEY not set` message) but never printed.
- No auto-truncate in the script — only prints the manual `TRUNCATE` command.
- Cost guard operator note is present.
- All imports were already project dependencies — no new packages.

## Self-Check: PASSED

- `scripts/loadtest_destinos_atrativos.py` exists and is 241 lines
- Commit f28bc91 exists in git log
- 401 offline tests pass
