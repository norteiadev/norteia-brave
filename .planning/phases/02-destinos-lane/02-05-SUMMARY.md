---
phase: 02-destinos-lane
plan: "05"
subsystem: destinos-lane
tags: [mtur, lane, producer, score-engine, tdd, d06-firewall, dest-01, test-02]
dependency_graph:
  requires: [02-01, 02-02, 02-03]
  provides: [MturSeedIngest, test_producer_score_boundaries]
  affects: [brave/lanes/destinos/mtur.py, tests/unit/test_score_engine.py]
tech_stack:
  added: []
  patterns: [LaneProtocol.produce, store_raw, process_nascente_record, pytest.mark.parametrize]
key_files:
  created: []
  modified:
    - brave/lanes/destinos/mtur.py
    - tests/unit/test_score_engine.py
decisions:
  - "D-06 firewall proven by unit test: origen=40 without human validation caps at 67.0, mathematically impossible to reach Mar (85.0 threshold)"
  - "Descarte boundary case uses (100,20,0,0,0) → 34.0 since original RESEARCH.md case (100,70,0,30,0)=48.5 lands in DLQ with calibrated threshold_dlq=40"
  - "Seven parametrize cases cover all producer scenarios: D-06 firewall, Mtur cold-start DLQ, Mtur descarte risk, NotebookLM min DLQ, validation+corroboração→Mar, validation no corroboração→DLQ, Desmembramento post-validate→DLQ"
metrics:
  duration: "~20m"
  completed: "2026-06-12"
  tasks: 2
  files_modified: 2
---

# Phase 2 Plan 05: MturSeedIngest Lane and Score Boundary Tests Summary

**One-liner:** MturSeedIngest lane wiring Mtur CSV → Nascente → Rio → DLQ with D-06 origem=40 firewall proven by 7 parametrized unit test cases (threshold_dlq=40 calibration).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | MturSeedIngest lane implementation (TDD) | 9f8f8df (RED), 4487ad9 (GREEN) | brave/lanes/destinos/mtur.py |
| 2 | Extend test_score_engine with Phase 2 producer boundary cases | 4110688 | tests/unit/test_score_engine.py |

## What Was Built

### Task 1: MturSeedIngest (previously completed)

`brave/lanes/destinos/mtur.py` implements `LaneProtocol.produce(uf)` for the Mtur seed lane:

- `MturSeedIngest(mtur_client, session, config).produce("BA")` calls `fetch_municipalities("BA")`, then for each municipality calls `store_raw(source="mtur", source_ref=f"mtur:{uf}:{ibge_code}", ...)` followed by `process_nascente_record`.
- Payload includes all `*_value` criterion fields (D-04): `origem_value=100.0`, `completude_value` from `_completude_from_fields`, `corroboracao_value=0.0`, `atualidade_value=MTUR_ATUALIDADE_DEFAULT (70.0)`, `validacao_humana_value=0.0`.
- Canonical sub-dict includes `ibge_code` (D-10, RISK-01 fix): `{"name", "uf", "municipio", "ibge_code"}`.
- `source_ref` format: `"mtur:{uf}:{ibge_code}"`.
- D-18 boundary preserved: imports only from `brave.core`, `brave.clients`, `brave.config`.

### Task 2: test_producer_score_boundaries (TEST-02, D-06)

Seven parametrize cases added to `tests/unit/test_score_engine.py`:

| Case | Inputs (o,c,cor,a,v) | Score | Routing | Invariant |
|------|---------------------|-------|---------|-----------|
| D-06 firewall | (40,100,100,100,0) | 67.0 | dlq | origen=40+no human → never Mar |
| Mtur cold-start DLQ | (100,70,0,50,0) | 51.5 | dlq | adequate fields → safe DLQ landing |
| Mtur descarte risk | (100,20,0,0,0) | 34.0 | descarte | sparse record → below threshold_dlq=40 |
| NotebookLM min DLQ | (80,100,0,50,0) | 51.5 | dlq | origem=80 with decent data → DLQ |
| Mtur+corroboração→Mar | (100,100,50,70,100) | 85.5 | mar | human+corroboration crosses threshold |
| Mtur no corroboração | (100,100,0,100,100) | 80.0 | dlq | Pitfall 2: no corroboração → stays DLQ |
| Desmembramento post-validate | (40,100,0,70,100) | 57.5 | dlq | origin=40 caps max reachable score |

All 25 score engine tests pass (7 new + 18 pre-existing).

## Deviations from Plan

### Adjusted descarte boundary case (RESEARCH.md vs calibrated threshold)

- **Found during:** Task 2 score case analysis
- **Issue:** RESEARCH.md test case `(100, 70, 0, 30, 0, "descarte")` has score 48.5, which with `threshold_dlq=40` routes to **dlq**, not descarte.
- **Fix:** Used PLAN.md's corrected case `(100, 20, 0, 0, 0, "descarte")` with score 34.0 < 40 → descarte.
- **Impact:** None (the descarte boundary is still demonstrated; this is the case that produces a score below threshold_dlq=40).

### Task 1 was pre-committed

- **Found during:** Execution start
- **Situation:** `brave/lanes/destinos/mtur.py` already existed with commits `9f8f8df` (TDD RED) and `4487ad9` (TDD GREEN) from a prior partial execution.
- **Action:** Verified all done criteria satisfied, proceeded directly to Task 2.

## Verification Results

```
uv run python -m pytest tests/unit/test_score_engine.py -v  # 25 passed
uv run python -c "from brave.lanes.destinos.mtur import MturSeedIngest; print('OK')"  # OK
grep -n "test_producer_score_boundaries" tests/unit/test_score_engine.py  # line 308
grep -n "ibge_code.*canonical" brave/lanes/destinos/mtur.py  # present
ScoreConfig().threshold_dlq == 40.0  # OK
```

## Known Stubs

None. MturSeedIngest is fully wired to real `store_raw` and `process_nascente_record` core services. Score boundary tests are pure computations — no stubs.

## Threat Flags

No new security-relevant surface introduced. MturSeedIngest reads from a trusted bundled CSV (T-02-05-01: accept disposition). No new network endpoints or auth paths.

## Self-Check: PASSED

- `brave/lanes/destinos/mtur.py` exists and is importable: FOUND
- `tests/unit/test_score_engine.py` contains `test_producer_score_boundaries`: FOUND (line 308)
- Commits: 9f8f8df (RED), 4487ad9 (GREEN), 4110688 (Task 2 test extension) — all present in git log
