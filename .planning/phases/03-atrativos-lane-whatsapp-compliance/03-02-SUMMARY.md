---
phase: 03-atrativos-lane-whatsapp-compliance
plan: 02
subsystem: atrativos-lane
tags:
  - discovery-agent
  - contact-finder-agent
  - signal-agent
  - state-machine
  - celery-tasks
  - places-client
  - apify-client
  - sub-state-fsm
dependency_graph:
  requires:
    - "03-01: schemas + fake clients scaffolded"
    - "Phase 01: core (store_raw, process_nascente_record, route_by_score, quarantine_poison)"
    - "Phase 02: Mar destinos in DB as parent destino source (D-03)"
  provides:
    - "DiscoveryAgent.produce(uf): Places sweep → parent destino resolution → Nascente"
    - "ContactFinderAgent.run(rio): discovered → contacts_found"
    - "SignalAgent.run(rio): contacts_found → signals_gathered → §7.6 score → DLQ/Mar"
    - "advance_sub_state(session, rio, expected, next): idempotent FSM guard + audit"
    - "RealPlacesClient: google-maps-places 0.9.x production impl"
    - "RealApifyClient: apify-client 3.0.x production impl"
    - "discover_atrativo_task / find_contacts_task / gather_signals_task: Celery pipeline"
    - "beat_schedule: sweep_atrativos_by_uf fan-out (27 UFs at 3 AM UTC daily)"
  affects:
    - "brave/tasks/pipeline.py: 3 new tasks appended"
    - "brave/tasks/beat_schedule.py: 27 new sweep_atrativos entries"
tech_stack:
  added:
    - "RealPlacesClient using google-maps-places 0.9.x (already in pyproject.toml)"
    - "RealApifyClient using apify-client 3.0.x (already in pyproject.toml)"
  patterns:
    - "Celery idempotent sub-state FSM (D-01/D-02)"
    - "advance_sub_state guard: returns False on state mismatch"
    - "CLOSED_* hard descarte before §7.6 scoring (D-05)"
    - "Apify best-effort: try/except → ig_data={} (D-05)"
    - "flag_modified on JSONB normalized (Phase 2 lesson T-02-06-04)"
    - "quarantine_poison on parent_destino_absent (D-03)"
    - "place_id-only cache in Nascente payload (D-04/COMP-03)"
key_files:
  created:
    - brave/lanes/atrativos/state_machine.py
    - brave/lanes/atrativos/discovery_agent.py
    - brave/lanes/atrativos/contact_finder_agent.py
    - brave/lanes/atrativos/signal_agent.py
    - brave/clients/places.py
    - brave/clients/apify.py
    - tests/unit/lanes/__init__.py
    - tests/unit/lanes/test_discovery_agent.py
    - tests/unit/lanes/test_signal_agent.py
  modified:
    - brave/tasks/pipeline.py
    - brave/tasks/beat_schedule.py
decisions:
  - "D-03 enforced: parent_destino_absent → quarantine_poison + continue; store_raw not called"
  - "D-04/COMP-03: only place_id stored as cache key; AtrativoResult = canonical data"
  - "D-05: CLOSED_* fires before route_by_score; Apify exception degrades signal, never raises"
  - "D-01/D-02: advance_sub_state returns False on state mismatch (idempotency guard)"
  - "Chose source_ref pattern matching for parent destino lookup (canonical is JSON not JSONB)"
  - "discover_atrativo_task time_limit=600 (Places API latency); other tasks time_limit=300"
  - "Beat schedule offset: atrativos at 3 AM UTC (1h after sweep_uf at 2 AM)"
metrics:
  duration: 10 minutes
  completed: 2026-06-15T14:22:00Z
  tasks_completed: 2
  files_created: 9
  files_modified: 2
---

# Phase 03 Plan 02: Discovery/Contact/Signal Agents + Sub-state FSM Summary

**One-liner:** Celery-idempotent sub-state FSM with DiscoveryAgent (parent-destino guard + place_id-only cache), ContactFinderAgent, SignalAgent (CLOSED_* hard descarte + Apify non-blocking), three pipeline tasks, and UF beat schedule.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Agent files + state machine + real client impls | 2d75a9a | 9 files created |
| 2 | Celery pipeline tasks + beat schedule | bef42fe | 2 files modified |

## What Was Built

### Task 1: Agent files + state machine + real client impls

**state_machine.py**: `advance_sub_state(session, rio, expected_state, next_state)` — idempotency guard (returns False when sub_state != expected), audit row write on every transition, session flush. Actor parameterizable (D-02).

**discovery_agent.py**: `DiscoveryAgent.produce(uf)` — sweeps `text_search(f"atrativos em {uf}", uf)` and `text_search(f"pontos turísticos em {uf}", uf)`. For each result: (1) resolves parent destino via source_ref pattern match on MarRecord; if None → `quarantine_poison(error="parent_destino_absent")` + continue (D-03). (2) LLM extraction via `llm_client.extract(schema=AtrativoResult, mode="tools")`. (3) `store_raw` with `source="places_discovery"`, `source_ref="places:{uf}:{place_id}"`, `entity_type="attraction"`. Payload canonical = AtrativoResult fields + `place_id` only (D-04/COMP-03).

**contact_finder_agent.py**: `ContactFinderAgent.run(rio)` — idempotency guard (sub_state != "discovered" → return). Fetches `place_details(place_id_cache)`, builds `ContactResult`, mutates `normalized` with `flag_modified`, sets sub_state="contacts_found", writes audit row.

**signal_agent.py**: `SignalAgent.run(rio)` — idempotency guard (sub_state != "contacts_found" → return). HARD DESCARTE CHECK fires first (D-05): `business_status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY")` → `rio.routing="descarte"`, `rio.sub_state=None`, `rio.dlq_reason="closed_place"`, audit, flush, return. Apify: `try scrape_ig(ig_handle) except Exception: ig_data={}` (D-05 non-blocking). Computes atualidade_value (100/50/0 based on review publishTime). Calls `route_by_score`. If routing=="dlq" → sub_state="aguardando_consulta_whatsapp" (D-06).

**places.py / apify.py**: `RealPlacesClient` and `RealApifyClient` — both raise `RuntimeError` if `AppConfig().run_real_externals` is False (safety guard). Tenacity 3-retry exponential backoff. `_check_protocol_compliance()` at module level (structural typing noted; cannot instantiate without run_real_externals=True).

**Tests (7 passing, 100% offline)**:
- `test_discovery_skips_when_no_parent_destino` — store_raw not called; quarantine error contains "parent_destino_absent"
- `test_discovery_stores_raw_with_place_id_only` — store_raw called; payload["canonical"]["place_id"] present; entity_type="attraction"
- `test_discovery_dedup_idempotent` — two produce calls, no quarantine
- `test_signal_agent_hard_descarte_closed_permanently` — rio.routing=="descarte", rio.sub_state is None
- `test_signal_agent_hard_descarte_closed_temporarily` — same for CLOSED_TEMPORARILY
- `test_signal_agent_apify_failure_degrades_gracefully` — FakeApifyClient raises; no exception propagated; sub_state=="signals_gathered"
- `test_signal_agent_advances_sub_state_for_open_place` — SIGNAL_FIXTURE_OPEN; sub_state=="signals_gathered"; atualidade_value > 0

### Task 2: Celery pipeline tasks + beat schedule

Three tasks appended to `brave/tasks/pipeline.py`:

| Task name | Celery name | time_limit | Client selection |
|-----------|-------------|------------|-----------------|
| `discover_atrativo_task(uf)` | `brave.discover_atrativo` | 600s | RealPlacesClient / FakePlacesClient |
| `find_contacts_task(rio_id)` | `brave.find_contacts` | 300s | RealPlacesClient / FakePlacesClient |
| `gather_signals_task(rio_id)` | `brave.gather_signals` | 300s | RealPlacesClient + RealApifyClient / Fakes |

All three: `acks_late=True`, `reject_on_worker_lost=True`, full `try/except/finally` with `quarantine_poison` on `MaxRetriesExceededError`.

Beat schedule: 27 `sweep-atrativos-{uf}-daily` entries added to `BRAVE_BEAT_SCHEDULE` (3 AM UTC, queue="brave.sweep"), staggered 1h after `sweep_uf` (2 AM UTC) to avoid peak DB contention.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] SQLAlchemy JSON vs JSONB .astext incompatibility**
- **Found during:** Task 1, GREEN phase (first test run)
- **Issue:** `MarRecord.canonical["municipio_ibge"].astext` raised `AttributeError` because the `canonical` column uses `sqlalchemy.JSON` (not `JSONB`). The `.astext` operator is PostgreSQL-specific and only available on the JSONB dialect extension.
- **Fix:** Replaced JSONB path expression with `source_ref.contains(municipio_ibge)` pattern matching, which works on the JSON column without dialect-specific operators. Added a fallback for `mtur:{uf}:` source_ref prefix.
- **Files modified:** `brave/lanes/atrativos/discovery_agent.py` — `_resolve_parent_destino()` function
- **Commit:** 2d75a9a (included in Task 1 commit)

**2. [Rule 2 - Missing critical functionality] DiscoveryAgent needs two search queries**
- **Found during:** Task 1 implementation
- **Issue:** A single `text_search(f"atrativos em {uf}")` would miss many attraction types.
- **Fix:** Added two queries per UF sweep: `"atrativos em {uf}"` + `"pontos turísticos em {uf}"`. Dedup handled by `store_raw` content_hash idempotency.
- **Files modified:** `brave/lanes/atrativos/discovery_agent.py`

## Threat Model Coverage

All T-03-02-* threats addressed:

| Threat | Mitigation |
|--------|------------|
| T-03-02-01 (race condition) | advance_sub_state returns False on mismatch; each agent has idempotency guard |
| T-03-02-02 (Google data leak) | Only place_id in canonical; AtrativoResult = first-party data |
| T-03-02-03 (stale sub_state) | acks_late=True + reject_on_worker_lost=True + sub_state guard |
| T-03-02-04 (Apify ToS) | Read-only signal; never DM; best-effort non-blocking |
| T-03-02-05 (LLM PII) | Discovery sends only structured Places business data (no personal info) |

## Known Stubs

None that block the plan's goal. Production use requires:
- `BRAVE_PLACES_API_KEY` — for RealPlacesClient
- `BRAVE_APIFY_API_KEY` — for RealApifyClient
- `BRAVE_RUN_REAL_EXTERNALS=true` — to select real clients in tasks

Both real clients raise `RuntimeError` if env guard not satisfied — intentional safety.

## Self-Check: PASSED

All 9 created files found on disk. Both commits (2d75a9a, bef42fe) verified in git log.
7 unit tests pass 100% offline. Task names verified: brave.discover_atrativo, brave.find_contacts, brave.gather_signals.
