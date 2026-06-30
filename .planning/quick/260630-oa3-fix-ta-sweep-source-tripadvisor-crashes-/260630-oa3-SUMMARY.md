---
phase: quick-260630-oa3
plan: 01
subsystem: pipeline
tags: [tripadvisor, celery, sweep, atrativos, destinos, rio, mtur]

requires:
  - phase: quick-260629-rmz
    provides: TA-lane geo-targeting spike findings (uf_geoids wrong, destinos query_id empty)

provides:
  - Fixed sweep_tripadvisor per-UF path that no longer crashes with ValueError on TA-destinos step
  - destino_rio_map built from ALL destination RioRecords (Mtur/IBGE authoritative) per UF
  - Updated atrativos.py docstring clarifying Mtur seed dependency
  - Updated test helper and new test proving non-source-filtered map sourcing

affects:
  - sweep_tripadvisor task (pipeline.py)
  - TripAdvisorAtrativosIngest (atrativos.py)
  - Any operator running TA atrativos sweep (must seed Mtur destinos first)

tech-stack:
  added: []
  patterns:
    - "TA sweep is atrativos-only; parent destinos come from authoritative Mtur/IBGE Rio records"
    - "destino_rio_map query filters by entity_type+uf, never by source"

key-files:
  created: []
  modified:
    - brave/tasks/pipeline.py
    - brave/lanes/tripadvisor/atrativos.py
    - tests/unit/tasks/test_sweep_tripadvisor.py

key-decisions:
  - "TA-destinos step removed from per-UF sweep — TripAdvisorDestinosIngest.produce() was crash-triggering; QID never captured; deferred until QID is discovered"
  - "destino_rio_map widened to ALL destination RioRecords in UF (entity_type+uf only); Mtur seed sweep is operator pre-condition"
  - "TripAdvisorDestinosIngest class left intact in codebase (fail-loud preserved for when QID is captured)"

patterns-established:
  - "sweep_tripadvisor per-UF = atrativos only; parent map sourced from authoritative Rio (Mtur/IBGE)"

requirements-completed:
  - oa3-crash-fix
  - oa3-test-coverage

duration: 15min
completed: 2026-06-30
---

# Quick 260630-oa3: Fix TA Sweep Source TripAdvisor Crashes Summary

**Removed TA-destinos Step 1 from sweep_tripadvisor per-UF path and widened destino_rio_map query to all destination RioRecords (Mtur/IBGE, no source filter) — every-UF ValueError crash eliminated**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-06-30T00:00:00Z
- **Completed:** 2026-06-30T00:15:00Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- Removed `TripAdvisorDestinosIngest.produce()` call from sweep_tripadvisor per-UF path (was crashing every single UF with `ValueError: No destinations queryId configured`)
- Widened destino_rio_map query: removed `_NascenteRecord.source == "tripadvisor"` filter; kept `entity_type == "destination"` + `RioRecord.uf == uf` — now reads authoritative Mtur/IBGE Rio records
- Relocated `import asyncio as _asyncio` to before the map query so `_asyncio.run(atrativos_ingest.produce(...))` still has it in scope
- Updated atrativos.py class docstring to note Mtur seed pre-condition
- Updated sweep_tripadvisor docstring: atrativos-only, cold-start requirement documented
- Reworked `_run_sweep_with_stub_client` helper to raise from `fetch_attractions` path (not `fetch_destinations`) and removed `mock_destinos_ingest` block
- Added `TestSweepTripAdvisorPerUfDestinoBuild.test_per_uf_destino_rio_map_sourced_from_authoritative_rio` — proves Mtur row keyed by IBGE code reaches atrativos constructor

## Task Commits

1. **Task 1: Remove TA-destinos Step 1, widen destino_rio_map, update docstrings** — `ed22eb9` (fix)
2. **Task 2: Update test_sweep_tripadvisor.py** — `ed22eb9` (same commit — all three files committed together per plan)
3. **Task 3: Full offline suite green** — `ed22eb9` (892 passed, 1 skipped, 0 failures)

## Files Created/Modified
- `brave/tasks/pipeline.py` — Removed TA-destinos import + Step 1 block; widened WHERE clause (dropped source filter); relocated asyncio import; updated docstrings
- `brave/lanes/tripadvisor/atrativos.py` — Updated TripAdvisorAtrativosIngest class docstring (Mtur seed pre-condition)
- `tests/unit/tasks/test_sweep_tripadvisor.py` — Fixed `_stub_produce` to call `fetch_attractions`; removed `mock_destinos_ingest` block; added `TestSweepTripAdvisorPerUfDestinoBuild`

## Decisions Made
- `TripAdvisorDestinosIngest` class left in codebase (not deleted) — the fail-loud design (`_DESTINATIONS_QID=None → ValueError`) is correct for when the QID eventually gets captured; only the sweep's invocation is removed
- destino_rio_map comment updated to document cold-start pre-condition as operator runbook requirement (not a code guard)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Stale comment] Updated stale dispatch comment at line 1925**
- **Found during:** Task 1 (pipeline.py edits)
- **Issue:** Comment `TripAdvisor lane — single task covers both destinos + atrativos (sweep_tripadvisor runs TripAdvisorDestinosIngest then TripAdvisorAtrativosIngest)` was now false
- **Fix:** Updated to `atrativos-only per oa3 fix; parent destinos must be seeded via Mtur sweep first`
- **Files modified:** brave/tasks/pipeline.py
- **Committed in:** ed22eb9

---

**Total deviations:** 1 auto-fixed (stale comment)
**Impact on plan:** Cosmetic accuracy fix. No scope creep.

## Issues Encountered
- `grep -v "^\s*#"` in the plan's verify command does not filter lines where `\s` is used without `-E` (basic grep treats `\s` literally). Both remaining grep hits for `TripAdvisorDestinosIngest` are in a docstring (line 938) and a `#` comment (now updated to line 1925 in updated form). No executable code references remain.

## Self-Check

### Files exist
- `brave/tasks/pipeline.py` — FOUND
- `brave/lanes/tripadvisor/atrativos.py` — FOUND
- `tests/unit/tasks/test_sweep_tripadvisor.py` — FOUND

### Commits exist
- `ed22eb9` — FOUND

### Suite green
- 892 passed, 1 skipped, 0 failures (BRAVE_USE_FAKEREDIS=1)

## Self-Check: PASSED

## Next Phase Readiness
- TA atrativos per-UF sweep is now crash-free; operator must seed Mtur destinos sweep before running TA atrativos per-UF, or every atrativo quarantines with `parent_destino_absent`
- Bulk national branch (`bulk_national=True`) is unchanged (already correct — no destinos step)
- When TA destinos QID is eventually captured, re-wire `TripAdvisorDestinosIngest.produce()` in the per-UF path and revert to source-filtered map

---
*Phase: quick-260630-oa3*
*Completed: 2026-06-30*
