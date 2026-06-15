---
phase: "03"
plan: "05"
subsystem: "atrativos-lane"
tags: [integration-tests, acceptance-gate, phase3, tdd]
dependency_graph:
  requires:
    - 03-01  # DiscoveryAgent
    - 03-02  # ContactFinderAgent + SignalAgent
    - 03-03  # WhatsApp gate + compliance
    - 03-04  # Mar promotion + DLQ human review
  provides:
    - Phase 3 acceptance gate (all 5 success criteria, 9 requirements ATR-01..06, COMP-01..03)
  affects:
    - tests/integration/test_atrativos_lane_e2e.py
tech_stack:
  added: []
  patterns:
    - "Offline integration tests with FakeClients and fakeredis (no network calls)"
    - "Seed helpers (_seed_parent_destino, _seed_rio_attraction) for DB fixture setup"
    - "FakePlacesClient SIGNAL_FIXTURE_OPEN/CLOSED for closed-entity descarte tests"
key_files:
  created:
    - tests/integration/test_atrativos_lane_e2e.py
  modified:
    - brave/core/rio/routing.py
decisions:
  - "Tests run entirely offline — FakePlacesClient, FakeLLMClient, FakeApify, FakeWhatsApp, FakeNorteiaApi, fakeredis"
  - "DiscoveryAgent.produce() only creates NascenteRecord; test manually calls process_nascente_record() then advance_sub_state() to simulate the pipeline task chain"
  - "Used synthetic UF='XX' for SC2 (no-parent-destino guard) to avoid matching pre-existing MarRecords in test DB"
metrics:
  duration: "~3 days (session resumed from compaction)"
  completed: "2026-06-15T21:40:24Z"
  tasks_completed: 1
  files_created: 1
  files_modified: 1
---

# Phase 3 Plan 05: Phase 3 Acceptance Gate (Offline E2E Integration Tests) Summary

**One-liner:** 7 offline integration tests proving all Phase 3 success criteria — discovery, D-03 parent guard, closed-entity descarte, contact finder, signal agent, WhatsApp compliance gate, and full E2E Mar promotion.

## What Was Built

Single test file `tests/integration/test_atrativos_lane_e2e.py` with 7 scenarios:

| Test | Scenario | Requirements Covered |
|------|----------|----------------------|
| SC1 | Discovery → RioRecord creation, sub_state=discovered | ATR-01 |
| SC2 | D-03 guard: no parent destino in Mar → descarte | ATR-02 |
| SC3 | CLOSED_PERMANENTLY → hard descarte before §7.6 scoring | ATR-03 |
| SC4 | ContactFinderAgent: place_id_cache → contacts_found sub_state | ATR-04 |
| SC5 | SignalAgent: open business → signals_gathered sub_state | ATR-05 |
| SC6 | WhatsApp gate: send_path_gate raises ComplianceError on violation | COMP-01, COMP-02 |
| SC7 | Full E2E happy path: discovery → contacts → signals → WhatsApp → Mar | ATR-01..06, COMP-01..03 |

All tests marked `@pytest.mark.integration`. All pass 100% offline. Run:
```bash
python -m pytest tests/integration/test_atrativos_lane_e2e.py -v
# 7 passed in 0.43s
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] process_nascente_record did not forward place_id_cache to rio.normalized**
- **Found during:** SC4 — ContactFinderAgent.run() logged `contact_finder_no_place_id` and returned early despite `place_id_cache` being present in the nascente payload
- **Issue:** `process_nascente_record` in `brave/core/rio/routing.py` built the normalized dict with only the name/address/lat/lon/score fields. It never copied `place_id_cache` from the nascente payload to `rio.normalized`. ContactFinderAgent reads `rio.normalized["place_id_cache"]`; it was always absent, so the agent always skipped contact finding.
- **Fix:** Added attraction-specific forwarding block in `process_nascente_record`:
  ```python
  if nascente.entity_type == "attraction" and "place_id_cache" in payload:
      normalized["place_id_cache"] = payload["place_id_cache"]
  ```
  This matches D-04 (only `place_id` cached, no re-querying text_search) and COMP-03 (Google Places ToS compliance).
- **Files modified:** `brave/core/rio/routing.py`
- **Commit:** 76bc741

**2. [Rule 3 - Blocking] DiscoveryAgent.produce() does not call process_nascente_record**
- **Found during:** SC1 — after `agent.produce(uf)`, no RioRecord existed; assertion failed
- **Issue:** `produce()` only calls `store_raw()` which creates NascenteRecord. The Celery task chain (`process_nascente` task) creates RioRecord. In tests, no Celery worker runs.
- **Fix:** Tests manually call `process_nascente_record(db_session, nascente, config)` then `advance_sub_state(session, rio, ...)` after produce(), simulating what the pipeline Celery task would do.
- **Files modified:** test file only

**3. [Rule 3 - Blocking] Pre-existing BA MarRecords in test DB caused SC2 D-03 guard to find a parent**
- **Found during:** SC2 — `_resolve_parent_destino` found a real `mtur:BA:...` MarRecord from prior tests
- **Issue:** SC2 needed to test the "no parent found" path. Using `uf="BA"` matched leftover test data.
- **Fix:** Changed SC2 to use `uf="XX"` (non-existent UF), guaranteeing no parent MarRecord match.
- **Files modified:** test file only

## Score Math Validation

- **Borderline attraction (→ DLQ):** origem=60×0.3 + completude=75×0.2 + corroboracao=40×0.2 + atualidade=100×0.15 + validacao_humana=0×0.15 = 18+15+8+15+0 = **56** → DLQ ✓
- **After owner validation (→ Mar):** origem=100×0.3 + completude=100×0.2 + corroboracao=40×0.2 + atualidade=100×0.15 + validacao_humana=100×0.15 = 30+20+8+15+15 = **88** → Mar ✓

## Known Stubs

None. All 7 scenarios exercise real production code paths. No placeholder assertions.

## Threat Flags

None. Tests are read-only with respect to the threat surface — they exercise existing endpoints/functions, create no new network exposure.

## Self-Check: PASSED

- `tests/integration/test_atrativos_lane_e2e.py` — FOUND (982 lines)
- `brave/core/rio/routing.py` — FOUND (modified, place_id_cache fix present)
- Task commit `76bc741` — FOUND
