---
phase: 11-tripadvisor-source-lane-graphql-scraper
verified: 2026-06-23T18:35:00Z
status: resolved
score: 8/8 requirements verified
overrides_applied: 0
resolution: "TA-06 gap closed in commit 840482d — repointed import to brave.lanes.tripadvisor.client and passed the required redis arg (a second latent TypeError in the same untested branch); fixed stale README paths. Backend DB-backed suite re-run green (0 failures)."
gaps:
  - truth: "sweep_tripadvisor task can activate the real TripAdvisorClient under run_real_externals=True"
    status: resolved
    reason: "FIXED (840482d). pipeline.py imported 'from brave.clients.tripadvisor import TripAdvisorClient' — module does not exist; class is at brave.lanes.tripadvisor.client. Additionally the call omitted the required redis arg (TypeError). Both were gated behind run_real_externals so the offline suite never caught them. Corrected import + redis client built from BRAVE_DB_REDIS_URL."
    artifacts:
      - path: "brave/tasks/pipeline.py"
        issue: "Line 931: 'from brave.clients.tripadvisor import TripAdvisorClient' — module brave.clients.tripadvisor does not exist; correct path is brave.lanes.tripadvisor.client"
      - path: "data/tripadvisor/README"
        issue: "Lines 11, 61, 129 reference 'brave/clients/tripadvisor/session.py', 'brave/clients/tripadvisor/client.py', 'brave/clients/tripadvisor/fake.py' — none of these paths exist (stale copy from earlier planning)"
    missing:
      - "Fix pipeline.py:931: change 'from brave.clients.tripadvisor import TripAdvisorClient' to 'from brave.lanes.tripadvisor.client import TripAdvisorClient'"
      - "Fix data/tripadvisor/README path references to use brave/lanes/tripadvisor/client.py"
---

# Phase 11: TripAdvisor Source Lane — Verification Report

**Phase Goal:** Add `brave/lanes/tripadvisor/` — a self-hosted GraphQL-hybrid scraper producing destinos and atrativos per UF, scoring reviews into §7.6, and an audited human promote-override so review-validated attractions reach Mar by operator action without weakening the canonical ≥85 gate.

**Verified:** 2026-06-23T18:35:00Z
**Status:** resolved — 8/8 (initial blocker TA-06 closed in commit 840482d)
**Re-verification:** TA-06 fix verified — backend DB-backed suite green after fix

---

## Goal Achievement

### Observable Truths (Requirements TA-01 through TA-08)

| # | Requirement | Truth | Status | Evidence |
|---|-------------|-------|--------|----------|
| 1 | TA-01 | TripAdvisor GraphQL client posts persisted queries via httpx after DataDome session bootstrap; queryId never hardcoded; Playwright lazy-imported; NullClient matches protocol; geo cached Redis→seed | VERIFIED | `brave/lanes/tripadvisor/client.py` — top-level imports are only `json`, `typing`, `httpx`, `structlog`; `sync_playwright` lazy-imported inside `_bootstrap_session()` (line 111); `BRAVE_TA_SESSION_KEY="brave:ta:session"` (line 42); `uf_geoids.json` has 27 keys; `TripAdvisorConfig` env_prefix=`BRAVE_TA_`, no `Field(alias=...)` |
| 2 | TA-02 | Producers `TripAdvisorDestinosIngest` + `TripAdvisorAtrativosIngest` write Nascente via `store_raw` with `source='tripadvisor'`, `origem_value=65.0`; parent_rio_id carried in atrativos payload | VERIFIED | `brave/lanes/tripadvisor/destinos.py:57,224,227` — `TA_DESTINO_ORIGEM_VALUE=65.0`, `store_raw(source="tripadvisor")`; `brave/lanes/tripadvisor/atrativos.py` — `parent_rio_id` in payload from `destino_rio_map`; `test_producers.py` passes |
| 3 | TA-03 | IBGE resolver: rapidfuzz token_sort_ratio ≥88, haversine <15km fallback, None→quarantine `ibge_unmatched`; parent=destino RioRecord from same sweep; quarantine `parent_destino_absent` only when no RioRecord | VERIFIED | `brave/lanes/tripadvisor/ibge.py:111-171` — UF filter → rapidfuzz → haversine → None; `destinos.py:186-194` — `quarantine_poison(..."ibge_unmatched"...)`; `atrativos.py` uses `destino_rio_map`; `test_ibge.py` passes |
| 4 | TA-04 | §7.6 scoring: `origem=65`, `corroboracao_from_reviews` log curve, `atualidade_from_recency` step function; proof: 200 reviews/4.5★/~5mo → ~67.05 → dlq; sparse → ~27.5 → descarte; val=100 → ~82.05 < 85 (proves override required) | VERIFIED | `brave/lanes/tripadvisor/scoring.py` — pure functions; `test_scoring.py` 3 proof tests pass with exact ranges (66.5–67.6 dlq, 27.0–28.0 descarte, <85 not-mar); `TripAdvisorReviewSignals(extra="forbid")` in `schemas.py:47` |
| 5 | TA-05 | `rio_records.mar_ready` column (migration 0006, `down_revision='0005'`); `route_by_score` sets flag only for TA attractions; `promote_override` raises `PromoteNotAllowed`→409 for non-`mar_ready`, bypasses ≥85 gate for `mar_ready=True` with `promotion_reason` provenance | VERIFIED | `alembic/versions/0006_add_rio_mar_ready.py:19-20`; `routing.py:87-92` — explicit boolean condition + `False` for all other paths; `promote/service.py:73-77` — guard raises `PromoteNotAllowed`; provenance append at line 105; `test_promote_service.py` + `test_route_mar_ready.py` pass |
| 6 | TA-06 | `sweep_tripadvisor` Celery task; `engine_sweep_run` dispatches it for `source='tripadvisor'`; `set_source/get_source` whitelist fail-closed; `/engine/start` validates `source` → 422 before state mutation; `/engine/status` includes `source`; promote-batch + single endpoints; 409 for non-mar_ready | PARTIAL — gap in real-client import path | `engine.py:129-146` — `set_source/get_source` with `_VALID_SOURCES={"default","tripadvisor"}`; `engine.py:149-158` — `get_status` includes `"source"`; `engine_router` validates source before `start_run`; `atrativos.py` — PATCH/POST endpoints with `require_steward_or_bearer`; **GAP:** `pipeline.py:931` imports `brave.clients.tripadvisor.TripAdvisorClient` — module does not exist (class is at `brave.lanes.tripadvisor.client`) |
| 7 | TA-07 | Dashboard source radiogroup (`data-testid="engine-source"`); UF multi-select chips when `tripadvisor`; `/mar-ready` route in SURFACES nav; optimistic single + bulk multi-select promote with confirm dialog | VERIFIED | `EngineControl.tsx:170` — `data-testid="engine-source"` radiogroup; `EngineControl.tsx:194-226` — UF chips shown conditionally for tripadvisor; `app/page.tsx:25` — `/mar-ready` in `SURFACES`; `MarReadyList.tsx` + `MarReadyActions.tsx` — optimistic remove + snapshot rollback + confirm dialog; all 153 Vitest tests pass |
| 8 | TA-08 | `data/tripadvisor/README` legal-risk note; lane docstring ToS note; root `SOURCES.md` with mtur/places/tripadvisor rows; 100% offline default; real scrape only via `@pytest.mark.real_browser` | VERIFIED | `data/tripadvisor/README:36-50` — ToS violation section, mitigations, operator gate; `destinos.py:7-19` docstring ToS/LGPD note; `SOURCES.md:14` — tripadvisor row with "ToS violation — operator-gated only"; all unit tests offline; `@pytest.mark.real_browser` guards live tests |

**Score:** 7/8 requirements verified (TA-06 has one blocker sub-item)

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/lanes/tripadvisor/client.py` | TripAdvisorClient + SessionExpiredError; Playwright lazy-import | VERIFIED | 361 lines; playwright imported only inside `_bootstrap_session` (line 111) |
| `brave/lanes/tripadvisor/geo.py` | `resolve_geo_id` Redis→seed; `load_uf_geoids` | VERIFIED | Redis→JSON fallback→ValueError; TTL=86400 |
| `brave/lanes/tripadvisor/schemas.py` | `TripAdvisorReviewSignals(extra="forbid")`, destino + atrativo payloads | VERIFIED | `extra="forbid"` at line 47; three models present |
| `brave/lanes/tripadvisor/ibge.py` | `IbgeMunicipio` dataclass; `resolve_municipio` rapidfuzz+haversine | VERIFIED | Full implementation present |
| `brave/lanes/tripadvisor/scoring.py` | `corroboracao_from_reviews`, `atualidade_from_recency`, `completude_from_fields` | VERIFIED | Pure functions; proof values verified by tests |
| `brave/lanes/tripadvisor/destinos.py` | `TripAdvisorDestinosIngest.produce` | VERIFIED | Mirrors mtur.py; `source='tripadvisor'`, `origem=65.0` |
| `brave/lanes/tripadvisor/atrativos.py` | `TripAdvisorAtrativosIngest.produce` with parent linkage | VERIFIED | `parent_rio_id` + `parent_source_ref` from `destino_rio_map` |
| `brave/clients/null_tripadvisor.py` | `NullTripAdvisorClient` + protocol compliance | VERIFIED | Returns `[]`/`0`; `_check_protocol_compliance` present |
| `brave/core/promote/service.py` | `PromoteNotAllowed` + `promote_override` | VERIFIED | Guard at line 73; `promotion_reason` at line 105 |
| `brave/core/models.py` | `RioRecord.mar_ready` column | VERIFIED | `Mapped[bool]` at line 157 |
| `brave/core/rio/routing.py` | `mar_ready` flag in `route_by_score` | VERIFIED | Lines 87-92; explicit False for non-qualifying paths |
| `brave/core/engine.py` | `set_source/get_source`, `_SOURCE_KEY`, `_VALID_SOURCES`, `source` in `get_status` | VERIFIED | Lines 47-52, 129-158 |
| `brave/api/routers/atrativos.py` | GET `/mar-ready`, PATCH `/promote`, POST `/promote-batch` | VERIFIED | Three endpoints; auth: `require_bearer` on GET, `require_steward_or_bearer` on mutations |
| `brave/api/routers/engine.py` | Source validation in `/start`; source in `/status` | VERIFIED | Lines 114-121 validate before `start_run`; status at line 77 |
| `brave/tasks/pipeline.py` | `sweep_tripadvisor` task; `engine_sweep_run` source branch | PARTIAL | Task exists (line 894-1010); source branch (line 1773-1777); **broken import** at line 931 under `run_real_externals=True` |
| `alembic/versions/0006_add_rio_mar_ready.py` | Migration 0006, `down_revision='0005'` | VERIFIED | `revision="0006"`, `down_revision="0005"`; up/down SQL correct |
| `data/tripadvisor/uf_geoids.json` | 27 UF keys | VERIFIED | 27 keys confirmed |
| `data/ibge/ibge_municipios.csv` | ≥5500 rows | VERIFIED | 5571 data rows (5572 lines - 1 header) |
| `dashboard/components/engine/EngineControl.tsx` | Source radiogroup; UF chips | VERIFIED | `data-testid="engine-source"` at line 170; UF chips conditional on tripadvisor |
| `dashboard/components/mar-ready/MarReadyList.tsx` | Mar-ready list with promote | VERIFIED | Table + single-promote + bulk multi-select |
| `dashboard/components/mar-ready/MarReadyActions.tsx` | Optimistic actions, rollback | VERIFIED | Snapshot+rollback pattern; confirm dialog |
| `dashboard/app/mar-ready/page.tsx` | `/mar-ready` route | VERIFIED | Page renders `MarReadyList` |
| `SOURCES.md` | Root index with mtur/places/tripadvisor | VERIFIED | 3 source rows present |
| `data/tripadvisor/README` | Legal-risk note, ToS, mitigations, operator gate | VERIFIED (stale paths) | Risk note present; paths reference non-existent `brave/clients/tripadvisor/` (doc-only issue) |
| `tests/fakes/fake_tripadvisor.py` | `FakeTripAdvisorClient` with call recording | VERIFIED | Used by producer tests |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `brave/lanes/tripadvisor/client.py` | `brave:ta:session` | `redis.setex` + `redis.get` | VERIFIED | Lines 194-198; `BRAVE_TA_SESSION_KEY="brave:ta:session"` |
| `brave/config/settings.py` | `brave/lanes/tripadvisor/client.py` | `TripAdvisorConfig` injected into `TripAdvisorClient.__init__` | VERIFIED | `TripAdvisorConfig` in settings.py; constructor accepts it |
| `brave/core/rio/routing.py` | `brave/core/models.py` | `route_by_score` sets `rio_record.mar_ready` | VERIFIED | routing.py:87; models.py:157 |
| `brave/api/routers/engine.py` | `brave/core/engine.py` | `set_source(redis, source)` called in `/start` after validation | VERIFIED | engine_router:129-130 |
| `brave/api/routers/atrativos.py` | `brave/core/promote/service.py` | `promote_override(db, rio, reason=...)` called in PATCH handler | VERIFIED | atrativos.py:112 |
| `brave/tasks/pipeline.py` | `brave/lanes/tripadvisor/destinos.py` | `sweep_tripadvisor` instantiates `TripAdvisorDestinosIngest` | VERIFIED | pipeline.py:948-955 |
| `brave/tasks/pipeline.py` | `brave/lanes/tripadvisor/client.py` (real client) | `from brave.clients.tripadvisor import TripAdvisorClient` | FAILED | Module `brave.clients.tripadvisor` does not exist; should be `brave.lanes.tripadvisor.client` |
| `dashboard/lib/engine-api.ts` | `brave/api/routers/engine.py` | `startEngine({source})` POSTs to `/api/v1/engine/start` | VERIFIED | `engine-api.ts:startEngine` sends `source` in body |
| `dashboard/lib/mar-ready-api.ts` | `brave/api/routers/atrativos.py` | `promoteAtrativo` PATCHes `/api/v1/atrativos/{id}/promote` | VERIFIED | `mar-ready-api.ts:promoteAtrativo` |

---

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `brave/core/rio/routing.py::route_by_score` | `rio_record.mar_ready` | `score_input` from `rio_record.normalized` | Yes — reads actual `*_value` fields set by producers | FLOWING |
| `brave/core/promote/service.py::promote_override` | `MarRecord.provenance` | `promote_to_mar()` return + dict merge | Yes — `promote_to_mar` builds real provenance from score fields | FLOWING |
| `brave/api/routers/atrativos.py::list_mar_ready` | `rows` | SQLAlchemy query: `mar_ready==True AND routing=='dlq' AND canonical_key.like('tripadvisor:%')` | Yes — real DB query | FLOWING |
| `dashboard/components/mar-ready/MarReadyList.tsx` | `items` | `useQuery(fetchMarReadyList)` → GET `/api/v1/atrativos/mar-ready` | Yes — real API call | FLOWING |
| `dashboard/components/engine/EngineControl.tsx` | `data.source` | `useQuery(fetchEngineStatus)` → GET `/api/v1/engine/status` | Yes — `get_status(redis)` returns real Redis value | FLOWING |

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Unit suite (88 TA-related tests) | `.venv/bin/python -m pytest tests/unit/ -q -m "not real_browser" --tb=no` | All dots, no FAILED lines (warnings only) | PASS |
| Dashboard Vitest suite | `cd dashboard && bun run test --run` | 153 passed, 24 files, exit 0 | PASS |
| Migration 0006 metadata | `python -m pytest tests/integration/test_migration_0006.py::test_migration_0006_revision_metadata -q` | revision="0006", down_revision="0005" | PASS |
| `brave.clients.tripadvisor` importable | `python3 -c "from brave.clients import tripadvisor"` | ImportError — module does not exist | FAIL |

---

## Probe Execution

Step 7c: No explicit probe scripts declared in plans or found under `scripts/*/tests/probe-*.sh`. SKIPPED (no probes declared).

---

## Requirements Coverage

| Requirement | Plans | Description | Status | Evidence |
|-------------|-------|-------------|--------|----------|
| TA-01 | 11-01 | GraphQL hybrid client, lazy Playwright, protocol, geo cache | SATISFIED | `client.py`, `geo.py`, `null_tripadvisor.py`, `uf_geoids.json`, unit tests pass |
| TA-02 | 11-02 | Producers `TripAdvisorDestinosIngest` + `TripAdvisorAtrativosIngest` | SATISFIED | `destinos.py`, `atrativos.py`, `store_raw(source='tripadvisor')`, `origem=65.0` |
| TA-03 | 11-02 | IBGE linkage: rapidfuzz + haversine; parent from same-sweep RioRecord | SATISFIED | `ibge.py`, producer quarantine paths, `destino_rio_map` in sweep task |
| TA-04 | 11-02 | §7.6 scoring proof: dlq/descarte/override-required; LGPD `extra=forbid` | SATISFIED | `scoring.py`, `schemas.py`, 3 proof tests pass with exact ranges |
| TA-05 | 11-03 | `mar_ready` migration 0006, `route_by_score` flag, `promote_override` | SATISFIED | `0006_add_rio_mar_ready.py`, `routing.py:87-92`, `promote/service.py`, tests pass |
| TA-06 | 11-03 | Engine source-awareness, `sweep_tripadvisor` task, promote API | BLOCKED (partial) | Task exists; engine Redis keys correct; API endpoints correct; **`brave.clients.tripadvisor` import broken under `run_real_externals`** |
| TA-07 | 11-04 | Dashboard source selector, UF chips, `/mar-ready` route, optimistic promote | SATISFIED | `EngineControl.tsx`, `MarReadyList.tsx`, `MarReadyActions.tsx`, 153 Vitest tests pass |
| TA-08 | 11-05 | Compliance docs, offline default, `@pytest.mark.real_browser` gating | SATISFIED | `data/tripadvisor/README`, lane docstrings, `SOURCES.md`, all tests offline by default |

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `brave/tasks/pipeline.py` | 931 | `from brave.clients.tripadvisor import TripAdvisorClient` — wrong module path | BLOCKER | Real TripAdvisor scraping (behind `run_real_externals=True`) will always fail with `ImportError` before any data is collected |
| `data/tripadvisor/README` | 11, 61, 129 | References `brave/clients/tripadvisor/session.py`, `client.py`, `fake.py` — paths do not exist | WARNING | Documentation mismatch (doc-only, no runtime impact; stale from planning phase) |

No `TBD`, `FIXME`, `XXX` markers found in any phase-11-modified source files (outside documentation strings). No unreferenced debt markers.

---

## Human Verification Required

*(None — all verifiable items resolved programmatically. The live TripAdvisor scraping path requires `run_real_externals=True` and a Playwright-capable environment; this is intentionally behind the `@pytest.mark.real_browser` gate and out of scope for this verification.)*

---

## Gaps Summary

**1 blocker gap — wrong import path for real TripAdvisorClient in sweep_tripadvisor task**

`brave/tasks/pipeline.py` line 931 contains:
```python
from brave.clients.tripadvisor import TripAdvisorClient
```

`brave.clients.tripadvisor` is not a module. The class lives at `brave.lanes.tripadvisor.client.TripAdvisorClient`. This path is only exercised when `AppConfig().run_real_externals` is `True` — the offline test suite takes the `NullTripAdvisorClient` branch and never reaches this import, so all 88 unit tests and 153 Vitest tests pass. However the entire production scraping path (the purpose of TA-01 through TA-04) is broken: any operator who sets `RUN_REAL_EXTERNALS=1` and starts a TripAdvisor sweep will get an `ImportError` immediately.

**Fix (one line):**
```python
# Before (line 931):
from brave.clients.tripadvisor import TripAdvisorClient
# After:
from brave.lanes.tripadvisor.client import TripAdvisorClient
```

Secondary (doc-only): `data/tripadvisor/README` lines 11, 61, 129 reference `brave/clients/tripadvisor/{session,client,fake}.py` — these paths are stale holdovers from planning and should be updated to `brave/lanes/tripadvisor/client.py` and `tests/fakes/fake_tripadvisor.py`.

---

_Verified: 2026-06-23T18:35:00Z_
_Verifier: Claude (gsd-verifier)_
