---
phase: "07"
plan: "03"
subsystem: lanes/atrativos
tags: [discovery-agent, empty-ibge-guard, targeted-discovery, produce-for-destino, offline-tests]

# Dependency graph
requires:
  - plan: "07-01"
    provides: "municipio_ibge/municipio_nome populated in text_search results; fixed RealPlacesClient field masks"
provides:
  - brave/lanes/atrativos/discovery_agent.py with D-02 empty-ibge guard and D-03 produce_for_destino
  - tests/unit/lanes/test_discovery_agent.py with 3 new offline tests
affects:
  - plan 07-05 (load-test harness can now call produce_for_destino per destino Mar record)
  - Any downstream that relies on _resolve_parent_destino never matching on empty ibge

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Early-return guard pattern: if not field or not field.strip(): return None — prevents contains('') DB queries"
    - "Targeted discovery bypass: produce_for_destino injects parent_mar.id directly, skipping _resolve_parent_destino entirely"
    - "Municipality-scoped queries: 'pontos turísticos em {municipio_nome} {uf}' + 'o que fazer em {municipio_nome} {uf}'"
    - "place_id deduplication via seen_place_ids set across multiple search queries"
    - "target_count short-circuit: outer and inner loops both check created >= target_count before proceeding"

key-files:
  modified:
    - brave/lanes/atrativos/discovery_agent.py
    - tests/unit/lanes/test_discovery_agent.py

key-decisions:
  - "D-02: Guard placed as the FIRST executable line of _resolve_parent_destino, before any import or SQL — prevents source_ref.contains('') from matching every MarRecord in the DB"
  - "D-03: produce_for_destino uses canonical.get('municipio') with fallback to canonical.get('name', '') — handles both field naming conventions in MarRecord canonical dicts"
  - "D-03: place_municipio_ibge falls back to canonical ibge_code when Places result has no municipio_ibge — ensures LLM prompt is always populated"
  - "D-03: produce_for_destino reuses the identical pipeline (LLM extract → store_raw → process_nascente_record → advance_sub_state) from produce() — no parallel code paths for scoring/routing"

requirements-completed:
  - PLACE-03
  - PLACE-04

# Metrics
duration: 8min
completed: 2026-06-18
---

# Phase 7 Plan 03: Empty-IBGE Guard + Targeted Atrativos Discovery Summary

**Fixed the `_resolve_parent_destino` contains("") mislink bug and added `DiscoveryAgent.produce_for_destino` for municipality-targeted discovery that bypasses the parent-destino lookup entirely.**

## Performance

- **Duration:** ~8 min
- **Completed:** 2026-06-18
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- **D-02 guard:** `_resolve_parent_destino` now returns `None` immediately when `municipio_ibge` is empty or whitespace-only — before the `session.scalar` call and before the `from sqlalchemy import and_` lazy import. This prevents `source_ref.contains("")` from matching any active `MarRecord` in the DB.
- **D-03 method:** `DiscoveryAgent.produce_for_destino(parent_mar, target_count=10)` runs two targeted queries (`"pontos turísticos em {municipio_nome} {uf}"` and `"o que fazer em {municipio_nome} {uf}"`), deduplicates results by `place_id` across both queries, injects `str(parent_mar.id)` directly as `parent_mar_id` in the payload, and reuses the identical LLM extraction + `store_raw` + `process_nascente_record` + `advance_sub_state` pipeline from `produce()`. Returns `int` count of Rio records created.
- **3 new offline tests** covering the D-02 guard (no DB query on empty IBGE), the D-03 targeted link (correct `parent_mar_id` injected, `session.scalar` never called), and the zero-return early-exit path (missing `municipio`/`name` in canonical).
- Full offline suite: **401 passed, 0 failed** (was 398 before this plan).

## Task Commits

1. **Task 1: Add empty-ibge guard + produce_for_destino to DiscoveryAgent** — `75280d2` (feat)
2. **Task 2: Extend test_discovery_agent.py with D-02/D-03 offline tests** — `82fe085` (test)

## Files Created/Modified

- `brave/lanes/atrativos/discovery_agent.py` — Added D-02 guard as first line of `_resolve_parent_destino`; added `produce_for_destino` method (167 lines) at end of `DiscoveryAgent` class
- `tests/unit/lanes/test_discovery_agent.py` — Added 3 new test functions after existing tests (173 lines); no existing tests modified

## Decisions Made

- D-02 guard placed before the lazy `from sqlalchemy import and_` import inside `_resolve_parent_destino` — the return happens before any DB or import work executes
- `produce_for_destino` falls back `canonical.get("municipio") or canonical.get("name", "")` to handle heterogeneous MarRecord canonical schemas
- Place-level `municipio_ibge`/`municipio_nome` fall back to canonical values when the Places result doesn't carry them — ensures DISCOVERY_PROMPT is always fully populated
- Tests patch `write_audit` in the produce_for_destino test to isolate the store_raw assertion (write_audit calls `session.add` which would otherwise count against `session.scalar` assertion)

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. Both `produce()` and `produce_for_destino()` are fully wired. The `produce_for_destino` targeted path is ready to be called from the harness (plan 07-05) once a `MarRecord` with `entity_type='destination'` exists.

## Threat Flags

No new threat surface introduced. Changes are internal to `discovery_agent.py`:
- T-07-07 (Tampering via contains("") mislink): **mitigated** — D-02 guard is in place and unit-tested by `test_empty_ibge_guard_quarantines_without_db_query`
- T-07-08 (Spoofing parent_mar_id without DB verify): accepted — parent_mar passed from harness which owns the MarRecord query; documented in plan threat model

## Self-Check: PASSED

- FOUND: brave/lanes/atrativos/discovery_agent.py
- FOUND: tests/unit/lanes/test_discovery_agent.py
- FOUND commit: 75280d2 (Task 1)
- FOUND commit: 82fe085 (Task 2)
- All 6 tests in test_discovery_agent.py pass
- Full offline suite: 401 passed
