---
phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr
plan: "07"
subsystem: brave-core-rio-routing + harness
tags: [gap-closure, g2, parent-link, rio-normalized, load-test-harness]
dependency_graph:
  requires: [07-05, 07-06]
  provides: [harness-step4-runs, atrativo-parent-link-queryable]
  affects: [brave/core/rio/routing.py, scripts/loadtest_destinos_atrativos.py, tests/unit/lanes/test_discovery_agent.py]
tech_stack:
  added: []
  patterns:
    - "attraction entity-type gate in process_nascente_record (mirrors place_id_cache pattern)"
    - "Python-side grouping over rio.normalized JSON dict (avoids SQL column that does not exist)"
key_files:
  created: []
  modified:
    - brave/core/rio/routing.py
    - scripts/loadtest_destinos_atrativos.py
    - tests/unit/lanes/test_discovery_agent.py
decisions:
  - "G2: parent_mar_id flows nascente.payload → rio.normalized via 4-line entity-type-gated copy in process_nascente_record (no new column, no migration)"
  - "Harness Step-4 uses Python-side grouping (load atrativos, dict-comprehend by normalized.get) — no SQL column reference to non-existent RioRecord.parent_mar_id"
metrics:
  duration: "4min"
  completed: "2026-06-18"
  tasks_completed: 2
  files_modified: 3
---

# Phase 07 Plan 07: G2 Parent Link in rio.normalized + Harness Step-4 Fix Summary

**One-liner:** Copies `parent_mar_id` from attraction nascente payload to `rio.normalized` (4-line gate mirroring `place_id_cache`) and fixes harness Step-4 crash by grouping in Python over the JSON field instead of a non-existent DB column.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| RED | Add failing test for parent_mar_id in rio.normalized | ca0d2a0 | tests/unit/lanes/test_discovery_agent.py |
| 1 | Copy parent_mar_id to rio.normalized in process_nascente_record | 9d1992a | brave/core/rio/routing.py, tests/unit/lanes/test_discovery_agent.py |
| 2 | Fix harness Step-4 to group by rio.normalized parent_mar_id in Python | aa282ea | scripts/loadtest_destinos_atrativos.py |

## What Was Built

### Task 1: routing.py — parent_mar_id copy block

Added 4 lines to `process_nascente_record` in `brave/core/rio/routing.py`, immediately after the `place_id_cache` copy block (lines 155-156). The new block:

```python
if nascente.entity_type == "attraction" and "parent_mar_id" in payload:
    normalized["parent_mar_id"] = payload["parent_mar_id"]
```

This is entity-type-gated — destinos (which have no `parent_mar_id`) are completely unaffected. The `normalized` dict is a fresh Python dict at this point (not yet persisted), so direct key assignment is correct — no `flag_modified` needed.

### Task 1: TDD test — test_produce_for_destino_parent_link_in_normalized

Added `test_produce_for_destino_parent_link_in_normalized` to `tests/unit/lanes/test_discovery_agent.py`. The test:
- Builds a mock `NascenteRecord` with `entity_type="attraction"` and `payload["parent_mar_id"]="uuid-test-parent"`
- Patches `find_duplicate`, `compute_embedding`, `label_entity`
- Calls `process_nascente_record(session_mock, nascente_mock, ScoreConfig())`
- Asserts `rio_record.normalized["parent_mar_id"] == "uuid-test-parent"`

### Task 2: harness Step-4 Python-side grouping

Replaced the crashing SQLAlchemy query:
```python
# BEFORE (crashes — RioRecord.parent_mar_id is not a column)
atr_rows = session.execute(
    select(RioRecord.parent_mar_id, func.count(RioRecord.id))
    ...
).all()
```

With Python-side grouping:
```python
# AFTER — loads all atrativos, groups by normalized.get("parent_mar_id") in Python
atr_records = list(session.scalars(
    select(RioRecord).where(RioRecord.entity_type == "attraction")
).all())
atr_counts: dict[str, int] = {}
for r in atr_records:
    parent_id = (r.normalized or {}).get("parent_mar_id") or "unknown"
    atr_counts[parent_id] = atr_counts.get(parent_id, 0) + 1
atr_rows = sorted(atr_counts.items())
```

The downstream loop `for parent_id, cnt in atr_rows` receives the same `(str, int)` tuple shape as the old query's `Row` objects — no downstream changes needed. The `func` import is preserved (still used by `mar_count` query and `routing_rows` query).

## Verification Results

| Check | Result |
|-------|--------|
| `grep -n "parent_mar_id" brave/core/rio/routing.py` | 1 match in `process_nascente_record` body |
| `grep -n "RioRecord\.parent_mar_id" scripts/loadtest_destinos_atrativos.py` | 0 matches |
| `grep -n "normalized.get" scripts/loadtest_destinos_atrativos.py` | 1 match in Step-4 comment + 1 in Step-2 |
| `ast.parse(scripts/loadtest_destinos_atrativos.py)` | SYNTAX OK |
| `pytest test_produce_for_destino_parent_link_in_normalized` | 1 passed |
| Full offline suite (excl. real smoke) | 404 passed, 1 pre-existing failure |

## Deviations from Plan

None — plan executed exactly as written. The 4-line addition and Python-side grouping match the plan spec precisely.

## Pre-existing Failure (Out of Scope)

`tests/integration/test_destinos_lane.py::test_notebooklm_corroboration_boosts_mtur` — 1 failure that pre-dates phase 07 (last modified in phase 02). Not caused by any change in this plan. Logged to `deferred-items.md` scope.

## TDD Gate Compliance

- RED gate commit: ca0d2a0 (`test(07-07): add failing test for parent_mar_id in rio.normalized (RED)`) — test failed as expected
- GREEN gate commit: 9d1992a (`feat(07-07): copy parent_mar_id from attraction nascente payload to rio.normalized`) — test passed after implementation
- No REFACTOR step needed (4-line addition, no cleanup required)

## Known Stubs

None — no placeholder text or hardcoded empty values introduced.

## Threat Flags

None — changes are internal data-flow only (nascente.payload → rio.normalized within the Brave pipeline; no new network endpoints, auth paths, or trust boundary crossings).

## Self-Check: PASSED

| Item | Status |
|------|--------|
| brave/core/rio/routing.py | FOUND |
| scripts/loadtest_destinos_atrativos.py | FOUND |
| tests/unit/lanes/test_discovery_agent.py | FOUND |
| .planning/.../07-07-SUMMARY.md | FOUND |
| Commit ca0d2a0 (RED test) | FOUND |
| Commit 9d1992a (routing.py GREEN) | FOUND |
| Commit aa282ea (harness fix) | FOUND |
