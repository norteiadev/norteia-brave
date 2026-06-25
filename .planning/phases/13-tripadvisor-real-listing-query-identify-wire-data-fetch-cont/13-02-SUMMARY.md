---
phase: "13"
plan: "02"
subsystem: "lanes/tripadvisor"
tags: ["tripadvisor", "canary", "atrativos", "fetch_attractions", "card-fields"]
dependency_graph:
  requires: ["Phase 13 Plan 01 (fetch_attractions rewire + normalized card dict shape)"]
  provides: ["_run_canary probes real AttractionsFusion qid", "atrativos._ingest_one aligned to normalized card fields"]
  affects:
    - "brave/api/routers/tripadvisor_session.py"
    - "brave/lanes/tripadvisor/atrativos.py"
    - "tests/unit/api/test_tripadvisor_session.py"
    - "tests/unit/lanes/tripadvisor/test_atrativos.py"
    - "tests/unit/lanes/tripadvisor/test_producers.py"
tech_stack:
  added: []
  patterns:
    - "Canary probes same qid as lane (fetch_attractions qid a5cb7fa004b5e4b5) — empty-result detection is now meaningful"
    - "most_recent_review_at=None unconditionally at Nascente for AttractionsFusion listing cards"
decisions:
  - "Canary now probes fetch_attractions(geo_id=303380, max_pages=1) — Minas Gerais geoId, avoids dependency on geo resolve in the canary path"
  - "most_recent_review_at=None at Nascente (listing card lacks it) per Phase 13 CONTEXT.md decision — atualidade_from_recency(None)=0.0 is correct"
  - "category stored in raw Nascente payload dict only (TripAdvisorAtrativoPayload has no category field)"
key_files:
  created:
    - "tests/unit/lanes/tripadvisor/test_atrativos.py"
  modified:
    - "brave/api/routers/tripadvisor_session.py"
    - "brave/lanes/tripadvisor/atrativos.py"
    - "tests/unit/api/test_tripadvisor_session.py"
    - "tests/unit/lanes/tripadvisor/test_producers.py"
metrics:
  duration: "9m"
  completed_at: "2026-06-25T14:51:23Z"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 5
  tests_added: 4
  tests_passing: 100
---

# Phase 13 Plan 02: Canary + Atrativos Field-Mapping Fix Summary

**One-liner:** Rewired `_run_canary` to probe `fetch_attractions(geo_id=303380, max_pages=1)` (qid `a5cb7fa004b5e4b5`) instead of the wrong `fetch_destinations`, and aligned `atrativos._ingest_one` to the normalized AttractionsFusion card dict shape (`review_count`, `category`, `most_recent_review_at=None`).

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | Fix `_run_canary` to probe `fetch_attractions` + update WR-02 canary tests | `0a58fb6` | tripadvisor_session.py, test_tripadvisor_session.py |
| 2 | Fix `atrativos._ingest_one` card-field mapping + add offline lane tests | `fbc45a7` | atrativos.py, test_atrativos.py, test_producers.py |

## What Was Built

### Task 1: Canary rewire

**`brave/api/routers/tripadvisor_session.py`:**
- Replaced `client.fetch_destinations("RJ", max_pages=1)` with `client.fetch_attractions(geo_id=303380, max_pages=1)` in `_run_canary`.
- `geo_id=303380` = Minas Gerais. Avoids depending on the geo-resolve path in the canary flow; any valid UF geoId works. National geoId 294280 explicitly avoided per plan.
- Updated docstring comment from "fetch_destinations" to "fetch_attractions + qid rationale".

**`tests/unit/api/test_tripadvisor_session.py`:**
- Updated `test_canary_infra_error_returns_503_and_keeps_key`: monkeypatch signature changed from `fetch_destinations(self, uf, ...)` to `fetch_attractions(self, geo_id, ...)`.
- Updated `test_canary_session_expired_returns_422_and_deletes_key`: same signature update.
- Added `test_canary_probes_fetch_attractions`: monkeypatches `fetch_destinations` to `AssertionError` guard; `fetch_attractions` records its call. Asserts `geo_id=303380` and `max_pages=1`.

### Task 2: atrativos field-mapping

**`brave/lanes/tripadvisor/atrativos.py`:**
- Changed `entity.get("reviewCount", 0)` → `entity.get("review_count", 0)` (normalized card key).
- Removed `most_recent_str` parsing block (ISO date + timezone.utc) — replaced with `most_recent_dt: datetime | None = None` unconditionally, with Phase 13 decision comment.
- Removed unused `timezone` from `datetime` import (`datetime` kept for type annotation).
- Added `category = str(entity.get("category", ""))` after `name` extraction.
- Added `"category": category` to the raw Nascente payload dict.

**`tests/unit/lanes/tripadvisor/test_atrativos.py`** (new file):
- `TestAtrativosIngestCardFields.test_ingest_one_maps_review_count_underscore`: injects card with `review_count=500` and `reviewCount=999`; asserts payload `review_count == 500`.
- `TestAtrativosIngestCardFields.test_ingest_one_sets_most_recent_review_at_none`: card has no `mostRecentReviewDate`; asserts ingest completes and `atualidade_value == 0.0`.
- `TestAtrativosIngestCardFields.test_ingest_one_stores_category`: card with `category="Waterfalls"`; asserts `payload["category"] == "Waterfalls"`.

**`tests/unit/lanes/tripadvisor/test_producers.py`** (Rule 1 fix):
- Updated `_FIXTURE_ATRATIVO` from `"reviewCount": 200` to `"review_count": 200`; removed `"mostRecentReviewDate"` key. Existing producer tests continue to pass with correct data.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated _FIXTURE_ATRATIVO in test_producers.py to use new card field keys**
- **Found during:** Task 2 — `_FIXTURE_ATRATIVO` still used `reviewCount` and `mostRecentReviewDate` (old camelCase). After the rename, `_ingest_one` would read `review_count=0` from those fixtures, silently producing wrong data in existing producer tests.
- **Fix:** Updated `_FIXTURE_ATRATIVO` to `review_count=200` (underscore) and removed `mostRecentReviewDate` key.
- **Files modified:** `tests/unit/lanes/tripadvisor/test_producers.py`
- **Commit:** `fbc45a7`

## Verification Results

```
cd norteia-brave && .venv/bin/python -m pytest tests/unit/ --tb=short
414 passed, 5 skipped, 15 warnings in 4.46s
```

Spot checks:
- `grep -n "fetch_attractions" .../tripadvisor_session.py | grep "303380"` → 1 match (line 140)
- `grep -n "review_count" .../atrativos.py | grep -v "reviewCount"` → 4 matches (underscore only)
- `grep -n "mostRecentReviewDate" .../atrativos.py` → 0 matches

## Known Stubs

None — all changes are complete implementations. `most_recent_review_at=None` at Nascente is intentional (documented decision), not a stub.

## Threat Flags

None — no new network endpoints, auth paths, or file access patterns introduced. The canary geo_id `303380` is a public TripAdvisor location identifier (T-13-02-01: accepted).

## Self-Check: PASSED

- `brave/api/routers/tripadvisor_session.py` — exists, contains `fetch_attractions` + `303380`
- `brave/lanes/tripadvisor/atrativos.py` — exists, contains `review_count` (underscore), no `mostRecentReviewDate`, has `category`
- `tests/unit/lanes/tripadvisor/test_atrativos.py` — exists, contains `TestAtrativosIngestCardFields`
- `tests/unit/api/test_tripadvisor_session.py` — exists, contains `test_canary_probes_fetch_attractions`
- Task 1 commit `0a58fb6` — exists
- Task 2 commit `fbc45a7` — exists
