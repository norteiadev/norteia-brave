---
phase: 11-tripadvisor-source-lane-graphql-scraper
plan: "03"
subsystem: tripadvisor-backend-wiring
tags: [tripadvisor, migration, promote-override, celery, fastapi, engine-source, tdd]
dependency-graph:
  requires:
    - "11-02" (RioRecord.mar_ready column + route_by_score mar_ready flag)
    - "11-01" (TripAdvisorClientProtocol, NullTripAdvisorClient)
  provides:
    - alembic/versions/0006_add_rio_mar_ready.py (migration 0006: rio_records.mar_ready column + index)
    - brave/core/promote/service.py (PromoteNotAllowed + promote_override)
    - brave/core/engine.py (_SOURCE_KEY + _VALID_SOURCES + set_source + get_source + source in get_status)
    - brave/tasks/pipeline.py (sweep_tripadvisor Celery task + engine_sweep_run source branch)
    - brave/api/routers/atrativos.py (GET /mar-ready + PATCH promote + POST promote-batch)
    - brave/api/routers/engine.py (source validation in /start + set_source)
  affects:
    - brave/api/main.py (atrativos router registered)
tech-stack:
  added: []
  patterns:
    - TDD (RED→GREEN per task)
    - promote_override mirrors validate_and_promote_rio (dlq/service.py) with mar_ready guard + force routing
    - set_source/get_source mirrors set_depth/get_depth (engine.py) exactly
    - atrativos router mirrors dlq.py (broker-down 503 contract, audit, 404/202 shape)
    - FastAPI dependency_overrides for unit-level DB mocking (no real DB)
key-files:
  created:
    - alembic/versions/0006_add_rio_mar_ready.py (migration: add_column mar_ready + create_index)
    - brave/core/promote/__init__.py (package init)
    - brave/core/promote/service.py (PromoteNotAllowed, promote_override)
    - brave/api/routers/atrativos.py (3 endpoints: GET /mar-ready, PATCH promote, POST promote-batch)
    - tests/unit/core/test_promote_service.py (3 tests)
    - tests/unit/api/test_engine_source.py (7 tests)
    - tests/unit/api/test_promote_override.py (4 tests)
    - tests/unit/api/__init__.py
    - tests/integration/test_migration_0006.py (2 tests — 1 skips without DB)
  modified:
    - brave/core/engine.py (_SOURCE_KEY + _VALID_SOURCES + set_source + get_source + source in get_status)
    - brave/tasks/pipeline.py (sweep_tripadvisor task added + engine_sweep_run source param)
    - brave/api/routers/engine.py (source validation + set_source + source in response)
    - brave/api/main.py (atrativos router registered)
decisions:
  - "promote_override writes promotion_reason to MarRecord.provenance AFTER promote_to_mar returns (not before) — avoids modifying rio.score_breakdown; uses flag_modified on mar.provenance"
  - "atrativos router filters by canonical_key.like('tripadvisor:%') for source='tripadvisor' — RioRecord has no direct source column; canonical_key is the reliable TA identity marker"
  - "engine_sweep_run source='tripadvisor' branch is checked BEFORE nascente_only — tripadvisor lane has no per-depth cost gate at dispatch level (depth controls run_rio inside sweep_tripadvisor)"
  - "test_engine_status_includes_source_key tests the engine module directly rather than HTTP endpoint — status endpoint requires DB for _pipeline_counts which is unavailable in unit scope"
  - "FastAPI dependency_overrides used for promote endpoint unit tests — avoids the BRAVE_DB_URL requirement"
metrics:
  duration: "~19min"
  completed: "2026-06-23"
  tasks: 2
  files: 13
requirements_completed:
  - TA-05
  - TA-06
---

# Phase 11 Plan 03: Backend Wiring Summary

**One-liner:** Migration 0006 (rio_records.mar_ready), promote_override service with PromoteNotAllowed guard, engine source-awareness (set_source/get_source), sweep_tripadvisor Celery task, engine /start source validation, and atrativos API router with 3 endpoints.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for promote_service + migration_0006 | 3dbe014 | tests/unit/core/test_promote_service.py, tests/integration/test_migration_0006.py |
| 1 (GREEN) | Migration 0006 + promote_override + engine source | b01c261 | 0006_add_rio_mar_ready.py, promote/service.py, engine.py |
| 2 (RED) | Failing tests for engine source + atrativos router | cb69a57 | tests/unit/api/test_engine_source.py, test_promote_override.py |
| 2 (GREEN) | sweep_tripadvisor + engine router + atrativos router | 84c527e | pipeline.py, engine.py router, atrativos.py, main.py |

## What Was Built

### Migration 0006 (alembic/versions/0006_add_rio_mar_ready.py)
- `revision="0006"`, `down_revision="0005"`
- `upgrade()`: `op.add_column("rio_records", sa.Column("mar_ready", sa.Boolean, nullable=False, server_default="false"))` + `op.create_index("ix_rio_records_mar_ready", "rio_records", ["mar_ready"])`
- `downgrade()`: drops index then drops column
- Standard B-tree index (not CONCURRENTLY — inside Alembic transaction)

### promote_override (brave/core/promote/service.py)
```python
class PromoteNotAllowed(Exception): ...

def promote_override(session, rio, reason, config=None) -> MarRecord:
    if not rio.mar_ready:
        raise PromoteNotAllowed(f"RioRecord {rio.id} is not mar_ready ...")
    # flag_modified(rio, "normalized") + reprocess_record + session.refresh
    rio.routing = "mar"  # force bypass ≥85 gate
    mar = promote_to_mar(session, rio)
    mar.provenance = {**mar.provenance, "promotion_reason": reason}
    flag_modified(mar, "provenance")
    return mar
```

### Engine Source Awareness (brave/core/engine.py)
```python
_SOURCE_KEY = "brave:engine:source"
_VALID_SOURCES = frozenset({"default", "tripadvisor"})

def set_source(redis, source) -> None: ...  # mirrors set_depth
def get_source(redis) -> str | None: ...   # mirrors get_depth
# get_status now includes "source": get_source(redis)
```

### sweep_tripadvisor Task (brave/tasks/pipeline.py)
- `name="brave.sweep_tripadvisor"`, same acks_late/reject_on_worker_lost/time_limit as sweep_uf
- Runs `TripAdvisorDestinosIngest.produce(uf, run_rio=run_rio)` first
- Builds `destino_rio_map` by querying RioRecord after destinos flush (keyed by municipio_id)
- Runs `TripAdvisorAtrativosIngest.produce(uf, run_rio=run_rio)` with the map
- PermanentError → quarantine; Exception → retry (max 3)

### engine_sweep_run Source Branch
```python
if source == "tripadvisor":
    sweep_tripadvisor.delay(uf, depth=effective_depth)
elif nascente_only:
    sweep_uf.delay(uf, depth=effective_depth)
else:
    if lane in ("destinos", "both"): sweep_uf.delay(...)
    if lane in ("atrativos", "both"): discover_atrativo_task.delay(...)
```
No regression on source="default" path (all existing tests pass).

### Engine /start Source Validation (brave/api/routers/engine.py)
Source validation added BEFORE `start_run` (same order as depth — T-11-03-03):
```python
source = body.get("source", "default")
if source not in collection_engine._VALID_SOURCES:
    raise HTTPException(422, "source must be 'default' or 'tripadvisor'")
# ... start_run + set_depth ...
collection_engine.set_source(redis, source)
engine_sweep_run.delay(ufs=ufs, lane=lane, depth=depth, source=source)
return {..., "source": source}
```

### Atrativos Router (brave/api/routers/atrativos.py)
Three endpoints:
- `GET /api/v1/atrativos/mar-ready` (require_bearer): filters `mar_ready=True AND routing='dlq' AND canonical_key.like('tripadvisor:%')`
- `PATCH /api/v1/atrativos/{rio_id}/promote` (require_steward_or_bearer): calls `promote_override`, catches PromoteNotAllowed → 409, dispatches `push_attraction_task`, audits `atrativo_promoted_override`
- `POST /api/v1/atrativos/promote-batch` (require_steward_or_bearer): batch loop with same broker-down 503 contract, limit capped at 1000 (T-11-03-04)

## Test Results

| Suite | Tests | Result |
|-------|-------|--------|
| test_promote_service.py | 3 | PASS |
| test_engine_source.py | 7 | PASS |
| test_promote_override.py | 4 | PASS |
| test_migration_0006.py | 1 pass + 1 skip (no DB) | PASS |
| Full unit suite (not real_browser) | 372 | PASS |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Design] promotion_reason written to MarRecord.provenance post-promote_to_mar**
- **Found during:** Task 1 implementation — `promote_to_mar` builds its own provenance dict from `rio_record.score_breakdown`; the PATTERNS.md snippet sets `rio.provenance` but RioRecord has no provenance column, so setting it on the ORM object would be silently lost
- **Fix:** After `promote_to_mar(session, rio)` returns the MarRecord, append `promotion_reason` to `mar.provenance` and call `flag_modified(mar, "provenance")` to track the JSON mutation
- **Files modified:** brave/core/promote/service.py
- **Commit:** b01c261

**2. [Rule 3 - Blocking] test_engine_status_includes_source_key required DB access**
- **Found during:** Task 2 RED→GREEN — the HTTP `/engine/status` endpoint calls `_pipeline_counts(db)` which requires a live Postgres; without `BRAVE_DB_URL` it returns 500
- **Fix:** Changed test to exercise `collection_engine.get_status()` directly (bypassing HTTP) — tests the same behavior (source key in status dict) without DB dependency
- **Files modified:** tests/unit/api/test_engine_source.py
- **Commit:** 84c527e

**3. [Rule 3 - Blocking] promote endpoint tests needed FastAPI dependency_overrides**
- **Found during:** Task 2 RED→GREEN — `monkeypatch.setattr("brave.api.routers.atrativos.get_db", ...)` doesn't override FastAPI's DI container; the endpoint still tries to create a real DB session
- **Fix:** Used `app.dependency_overrides[get_db] = _fake_get_db` pattern (FastAPI's correct override mechanism) with a context manager for cleanup
- **Files modified:** tests/unit/api/test_promote_override.py
- **Commit:** 84c527e

## Known Stubs

None — all functions are fully implemented with no placeholder returns.

## Threat Flags

All threats from the plan's threat register are mitigated:

| Flag | File | Status |
|------|------|--------|
| T-11-03-01 (EoP: mar_ready guard) | promote/service.py + atrativos.py | Mitigated — PromoteNotAllowed raised and mapped to 409 for any non-mar_ready record; test asserts 409 |
| T-11-03-02 (EoP: auth on endpoints) | atrativos.py | Mitigated — all endpoints use require_steward_or_bearer or require_bearer; auth tests assert 401 |
| T-11-03-03 (Tampering: source whitelist) | engine.py router | Mitigated — source validated against _VALID_SOURCES before start_run; test asserts 422 for invalid source |
| T-11-03-04 (DoS: batch without limit) | atrativos.py | Mitigated — Query(le=1000) enforced on promote-batch limit param |
| T-11-03-05 (InfoDisc: broker-down logging) | atrativos.py | Mitigated — only log+raise 503 when run_real_externals=True; offline swallows exception |

No new threat surface introduced beyond what is in the plan's threat register.

## TDD Gate Compliance

| Gate | Commit | Status |
|------|--------|--------|
| RED (Task 1) | 3dbe014 | test(11-03): RED tests Task 1 |
| GREEN (Task 1) | b01c261 | feat(11-03): Task 1 |
| RED (Task 2) | cb69a57 | test(11-03): RED tests Task 2 |
| GREEN (Task 2) | 84c527e | feat(11-03): Task 2 |

All RED gates confirmed failing before GREEN implementation.

## Self-Check: PASSED

Verified created files exist:
- [x] alembic/versions/0006_add_rio_mar_ready.py — FOUND
- [x] brave/core/promote/__init__.py — FOUND
- [x] brave/core/promote/service.py — FOUND
- [x] brave/core/engine.py — FOUND (modified)
- [x] brave/tasks/pipeline.py — FOUND (modified)
- [x] brave/api/routers/engine.py — FOUND (modified)
- [x] brave/api/routers/atrativos.py — FOUND
- [x] brave/api/main.py — FOUND (modified)
- [x] tests/unit/core/test_promote_service.py — FOUND
- [x] tests/unit/api/test_engine_source.py — FOUND
- [x] tests/unit/api/test_promote_override.py — FOUND
- [x] tests/integration/test_migration_0006.py — FOUND

Verified commits exist:
- [x] 3dbe014 — test(11-03): RED tests Task 1
- [x] b01c261 — feat(11-03): Task 1
- [x] cb69a57 — test(11-03): RED tests Task 2
- [x] 84c527e — feat(11-03): Task 2

Verified key acceptance criteria:
- [x] alembic/versions/0006_add_rio_mar_ready.py: `grep "down_revision.*0005"` → matches
- [x] alembic/versions/0006_add_rio_mar_ready.py: 6 matches for `mar_ready` (column + index create + drop)
- [x] brave/api/routers/atrativos.py: `require_steward_or_bearer` ≥ 2 matches
- [x] brave/api/routers/atrativos.py: `atrativo_promoted_override` ≥ 1 match
- [x] brave/api/routers/atrativos.py: `PromoteNotAllowed` ≥ 1 match
- [x] Full unit suite (not real_browser): 372 passed, 0 failed
