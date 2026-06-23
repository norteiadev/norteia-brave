---
phase: 10-engine-stage-depth-selector-cost-gated-collection
plan: 02
subsystem: orchestrator
tags: [engine, depth, cost-gate, orchestrator, mtur, atrativos]
requires:
  - "brave/core/engine.py depth constants (NASCENTE/NASCENTE_RIO/NASCENTE_RIO_MAR) — plan 10-01"
  - "engine_sweep_run.delay(..., depth=depth) dispatch — plan 10-01"
provides:
  - "engine_sweep_run(ufs, lane, depth): depth threaded to producers; nascente forces Mtur-only"
  - "sweep_uf(uf, depth): derives run_rio/run_desmembramento (both off under nascente)"
  - "discover_atrativo_task(uf, depth): find_contacts fan-out gated off under nascente_rio"
  - "MturSeedIngest.produce(uf, *, run_rio): Nascente always, Rio only when run_rio"
affects:
  - "plan 10-03 (dashboard mirrors the depth enum + start body — already consuming this contract)"
tech-stack:
  added: []
  patterns:
    - "Read Redis depth ONCE at the /start edge; thread it down as an explicit task arg — lanes never read Redis depth"
    - "Depth orthogonal to lane: under nascente, force Mtur-only regardless of lane"
    - "No automated Mar push in the recurring sweep under any depth — Mar push stays on the human DLQ gate + WhatsApp finalize"
key-files:
  created:
    - tests/unit/test_engine_depth_gating.py
  modified:
    - brave/lanes/destinos/mtur.py
    - brave/tasks/pipeline.py
decisions:
  - "run_rio is keyword-only on MturSeedIngest.produce — the orchestrator owns the depth read; the lane never reads Redis"
  - "depth=None (legacy/direct call) defaults to NASCENTE_RIO_MAR to preserve prior behavior; the API edge always supplies it (10-01)"
  - "The find_contacts fan-out guard wraps the ENTIRE loop (both .delay dispatch AND the inline .run fallback) so nascente_rio leaks neither path"
  - "Tests swap whole Celery task objects (not per-instance .delay patches) because Celery resolves .delay through a proxy that bypasses instance attribute patches inside a running task"
metrics:
  duration: ~25min
  completed: 2026-06-23
  tasks: 2
  files: 3
---

# Phase 10 Plan 02: Engine Stage-Depth Selector (orchestrator gating) Summary

Threaded the operator-selected pipeline **depth** (persisted by plan 10-01) through the orchestrator and the destinos lane so the three medallion depths become real cost boundaries: `nascente` is Mtur-only Nascente+score (zero external cost), `nascente_rio` runs producers+Rio but never kicks the atrativos WhatsApp-gate chain, and `nascente_rio_mar` is the full pipeline as today. Depth is read once at the `/start` edge and passed down as an explicit task arg — the lanes never read Redis depth.

## What Was Built

**Task 1 — `run_rio` gate on `MturSeedIngest.produce` (`brave/lanes/destinos/mtur.py`):**
- `produce(self, uf, *, run_rio: bool = True)` — keyword-only param; default preserves today's behavior.
- `store_raw` (Nascente write + the §7.6 `*_value` score inputs incl. `origem_value=100`) always runs.
- `process_nascente_record` (Rio) is now wrapped in `if run_rio:` — `run_rio=False` writes Nascente only, creates zero RioRecords, and issues zero LLM/Places calls (the free `Apenas nascente` path).
- The lane reads no Redis depth — `run_rio` arrives from the caller.

**Task 2 — depth threaded through the orchestrator + per-depth gating (`brave/tasks/pipeline.py`):**
- `engine_sweep_run(ufs, lane, depth=None)` — `effective_depth = depth or NASCENTE_RIO_MAR`, read ONCE outside the loop (never re-read from Redis mid-run, T-10-04). Under `nascente` it dispatches **only** `sweep_uf.delay(uf, depth=...)` regardless of `lane` (atrativos have no free source); at the rio depths it honors `lane` and threads `depth` into both `sweep_uf.delay` and `discover_atrativo_task.delay`. The result dict now carries `depth`.
- `sweep_uf(uf, depth=None)` — derives `run_rio = depth != NASCENTE` and `run_desmembramento = depth != NASCENTE`; calls `seed.produce(uf, run_rio=run_rio)` and wraps the entire DesmembramentoAgent construction+produce in `if run_desmembramento:` (no LLM client even instantiated under `nascente`).
- `discover_atrativo_task(uf, depth=None)` — after discovery/Rio, the **entire** `find_contacts_task` fan-out loop (both `.delay` dispatch AND the inline `.run` except-fallback) is wrapped in `if effective_depth != NASCENTE_RIO:`, so under `nascente_rio` neither path fires and the WhatsApp-gate chain is never kicked. `nascente_rio_mar` runs it exactly as before.
- No `promote_to_mar` / `push_mar` call was added anywhere — `nascente_rio_mar` differs from `nascente_rio` ONLY by kicking the atrativos chain. Mar push stays on the unchanged human DLQ gate + WhatsApp finalize path (ENG-05).
- Imports the depth constants from `brave.core.engine` (10-01); does not redefine them.

## Tasks Completed

| Task | Name | Commits | Files |
| ---- | ---- | ------- | ----- |
| 1 | run_rio gate on MturSeedIngest.produce | `6a01a48` test RED → `0eb14e8` feat GREEN | brave/lanes/destinos/mtur.py, tests/unit/test_engine_depth_gating.py |
| 2 | Thread depth through engine_sweep_run/sweep_uf/discover_atrativo_task | (shared RED `6a01a48`) → `9c14559` feat GREEN | brave/tasks/pipeline.py, tests/unit/test_engine_depth_gating.py |

Both tasks share the one offline test file `tests/unit/test_engine_depth_gating.py`; the RED commit (`6a01a48`) covers both, GREEN landed per task.

## Test Results

```
unset RUN_REAL_EXTERNALS && BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/unit/test_engine_depth_gating.py -q
.................. [100%]
18 passed in 0.55s
```

Coverage:
- **nascente = Mtur-only + no-Rio + no-atrativos:** orchestrator dispatches only `sweep_uf` (with `depth="nascente"`) even when `lane="both"`; `discover_atrativo_task` never dispatched; `sweep_uf` calls `produce(run_rio=False)` and never instantiates Desmembramento; `produce(run_rio=False)` writes Nascente but zero RioRecords.
- **nascente_rio = no find_contacts `.delay` nor `.run`:** `discover_atrativo_task` with `depth="nascente_rio"` fires neither the dispatch nor the inline fallback; with `nascente_rio_mar` it fires `.delay` per discovered row.
- **no promote_to_mar/push under any depth:** parametrized over all three depths, asserts `promote_to_mar.call_count == 0` and `push_mar` dispatch count `== 0`.

Full unit suite green (no regressions): `tests/unit` all pass (4 skips are pre-existing). `test_engine_state.py`, `test_mtur_lane.py`, `test_desmembramento.py` re-run clean.

Acceptance greps:
- `grep -v '^#' brave/tasks/pipeline.py | grep -c depth` → **30** (≥6 ✓)
- `grep -v '^#' brave/lanes/destinos/mtur.py | grep -c run_rio` → **8** (≥2 ✓)
- `grep -c 'brave:engine:depth' brave/lanes/destinos/mtur.py` → **0** (lane never reads Redis depth ✓)

All offline: fakeredis, monkeypatched dispatch, fake Mtur/Discovery/session, `RUN_REAL_EXTERNALS` unset, no broker.

## Deviations from Plan

**1. [Rule 1 - Test correctness] Swap whole Celery task objects in tests instead of per-instance `.delay` patches**
- **Found during:** Task 2 (GREEN).
- **Issue:** The plan's acceptance criteria suggested `monkeypatch`-ing `sweep_uf.delay` / `find_contacts_task.delay`. A per-instance `setattr(task, "delay", spy)` is silently bypassed when the orchestrator calls `task.delay(...)` from inside a running Celery task — Celery resolves `.delay` through a proxy, so the spy never recorded calls even though dispatch happened (verified empirically: `dispatched=1` while the spy stayed empty).
- **Fix:** Tests replace the whole module-global task object with a tiny `_FakeTask`/spy class exposing `.delay` (and `.run`) via `monkeypatch.setattr(pipeline, "sweep_uf", _FakeTask(...))`. This deterministically captures dispatch. No production code changed for this; it is a test-harness correctness fix.
- **Files modified:** tests/unit/test_engine_depth_gating.py
- **Commit:** `9c14559`

## Cross-Plan Seam (expected, not a deviation)

Plan 10-01 already dispatches `engine_sweep_run.delay(ufs=ufs, lane=lane, depth=depth)`; this plan adds the matching `depth` parameter on `engine_sweep_run` (and threads it to `sweep_uf` / `discover_atrativo_task`). The contract is now closed end-to-end: `/start` (10-01) → orchestrator (10-02) → producers.

## Known Stubs

None. Depth is fully threaded and enforced at every dispatch chokepoint.

## Threat Flags

None beyond the plan's register. T-10-04 (depth read once at the edge, lanes never re-read Redis) and T-10-05 (nascente issues zero external calls — asserted by RioRecord count 0 and atrativos dispatch count 0) are both covered by explicit tests. T-10-SC — no new packages introduced.

## Self-Check: PASSED

- `brave/lanes/destinos/mtur.py` present and modified (run_rio gate).
- `brave/tasks/pipeline.py` present and modified (depth threading).
- `tests/unit/test_engine_depth_gating.py` present — 18 tests pass offline.
- Commits found in git log: `6a01a48` (RED test), `0eb14e8` (Task 1 GREEN), `9c14559` (Task 2 GREEN).
