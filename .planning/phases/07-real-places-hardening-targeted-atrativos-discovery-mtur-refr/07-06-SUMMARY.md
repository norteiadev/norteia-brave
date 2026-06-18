---
phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr
plan: "06"
subsystem: pipeline
tags: [score-engine, corroboracao, harness, gap-closure, sec-7-6]

# Dependency graph
requires:
  - phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr
    provides: "07-05 — loadtest harness script and DLQ validate_and_promote_rio integration"
provides:
  - "IBGE corroboration boost (+50, capped at 100) in harness Step 2 before validate_and_promote_rio"
  - "Offline unit tests proving boost enables Mar promotion (score 85.5 ≥ 85 threshold)"
  - "ScoreConfig-unchanged guard preventing accidental §7.6 weight modification"
affects:
  - "07-07 (parent link gap closure — depends on Mar records being created by this step)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Harness corroboration boost: normalized dict reassign + flag_modified + flush + reprocess_record (mirrors notebooklm.py:214-221)"
    - "ScoreConfig guard test: import ScoreConfig at test time and assert exact weight/threshold values"

key-files:
  created:
    - "tests/unit/test_harness_corroboration.py"
  modified:
    - "scripts/loadtest_destinos_atrativos.py"

key-decisions:
  - "G1 gap closure: harness-only corroboration boost (+50) standing in for NotebookLM/2nd-source corroboration; global §7.6 weights/thresholds untouched"
  - "Boost placed BEFORE validate_and_promote_rio to avoid double-reprocess (validate_and_promote_rio calls reprocess_record internally)"
  - "session.refresh(rio) added after reprocess_record so routing state is current before validate_and_promote_rio reads it"

patterns-established:
  - "Harness corroboration boost pattern: dict(rio.normalized or {}) → set key → reassign → flag_modified → flush → reprocess_record → refresh — same sequence as notebooklm.py"

requirements-completed: [PLACE-05]

# Metrics
duration: 15min
completed: 2026-06-18
---

# Phase 07 Plan 06: Harness IBGE Corroboration Boost (Gap G1) Summary

**IBGE corroboration boost (+50, capped at 100) added to harness Step 2, raising Mtur destino scores from 75.5 to 85.5 (≥threshold_mar=85) enabling Mar promotion**

## Performance

- **Duration:** 15 min
- **Started:** 2026-06-18T19:30:00Z
- **Completed:** 2026-06-18T19:45:00Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments

- Harness Step 2 now applies the IBGE corroboration boost to each DLQ destino rio BEFORE calling `validate_and_promote_rio`, using the exact same pattern as `notebooklm.py:214-221` (normalized reassign + `flag_modified` + `flush` + `reprocess_record` + `refresh`)
- New offline test file `tests/unit/test_harness_corroboration.py` (3 tests, 100% passing) proves: boost enables Mar promotion (score 85.5), cap at 100 works, ScoreConfig weights/thresholds are unchanged
- Score math verified: with `corroboracao=50` + `validacao_humana=100` + `origem=100, completude=100, atualidade=70`: score = 30+20+10+10.5+15 = 85.5 ≥ 85 → routing `"mar"`

## Task Commits

Each task was committed atomically:

1. **Task 1: Add corroboration step to harness Step 2 + offline test** - `759408c` (feat)

**Plan metadata:** (see final commit below)

## Files Created/Modified

- `scripts/loadtest_destinos_atrativos.py` — Added `flag_modified` + `reprocess_record` imports; added corroboration boost block in Step 2 loop before `validate_and_promote_rio`
- `tests/unit/test_harness_corroboration.py` — 3 offline unit tests: boost enables Mar promotion, boost capped at 100, ScoreConfig-unchanged guard

## Decisions Made

- **Boost before validate_and_promote_rio:** The boost must run before `validate_and_promote_rio` because that function also calls `reprocess_record` internally — boosting corroboration first, then calling validate, means the validacao=100 is set on top of already-boosted corroboration so the final reprocess sees both values simultaneously
- **session.refresh(rio) after reprocess_record:** Required so the ORM-cached `rio.routing` reflects the updated routing from `reprocess_record` before `validate_and_promote_rio` reads it
- **No changes to §7.6 engine or settings:** The boost is a harness-only step standing in for NotebookLM/2nd-source corroboration; the global gate is unchanged

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

**Pre-existing flaky integration test:** `tests/integration/test_destinos_lane.py::test_notebooklm_corroboration_boosts_mtur` fails when run as part of the full suite (403 passed, 1 failed). Confirmed pre-existing: identical failure occurs when running with git stash (original code). Root cause: `_make_dlq_record()` and `test_mtur_lane_end_to_end` call `db_session.commit()` explicitly, leaving Porto Seguro (IBGE `2927408`) records in the DB across test runs. The conftest `db_session` fixture only calls `session.rollback()` on teardown — which cannot undo already-committed rows. When the corroboration test runs, `NotebookLMIngest` IBGE-match query finds the OLD committed record and boosts it, while the test checks the NEWLY created record. Logged to `deferred-items.md`. NOT caused by plan 07-06 changes.

## Known Stubs

None — this plan adds no placeholder data flows.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced.

## Next Phase Readiness

- Plan 07-07 (G2 parent link gap closure) can proceed — Mar destinos will now be created by the harness when run against a live DB
- The harness Step 3 (targeted atrativos discovery) depends on `promoted` list being non-empty, which requires this boost to produce Mar records

---
*Phase: 07-real-places-hardening-targeted-atrativos-discovery-mtur-refr*
*Completed: 2026-06-18*
