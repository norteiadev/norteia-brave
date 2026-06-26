---
phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
plan: 06
subsystem: tripadvisor-lane
tags: [TA-12, bulk-ingest, nascente, pagination, geocode-national, ibge, lgpd, sweep-progress, resume-integrity, offline-tests]

# Dependency graph
requires:
  - phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
    plan: 02
    provides: "GeocoderClientProtocol.geocode_national + TripAdvisorClientProtocol.fetch_attractions_paginated protocol stubs + fake widening"
  - phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
    plan: 04
    provides: "TripAdvisorClient.fetch_attractions_paginated (HTML-SSR transport, async iterator yielding (offset, cards))"
  - phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
    plan: 05
    provides: "resolve_municipio_national (national haversine over all IBGE seats) + NominatimGeocoderClient.geocode_national"
  - phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
    plan: 03
    provides: "sweep_progress.record_page / record_error / get_progress / get_resume_offset (Redis HASH progress surface)"
provides:
  - "TripAdvisorAtrativosIngest._ingest_one_bulk — parent-less national ingest; derives uf+municipio from geocode_national + resolve_municipio_national; drops the parent_destino_absent gate; ibge_unmatched quarantine on geo/resolution miss"
  - "TripAdvisorAtrativosIngest.produce_paginated — drives fetch_attractions_paginated, ingests per card via _ingest_one_bulk, commits PER PAGE before record_page (resume integrity), wires record_error for the live panel error counter, lets SessionExpiredError propagate"
affects:
  - "the 15-07 task layer (sweep_tripadvisor bulk branch) will call produce_paginated and own the fail-fast + needs_bootstrap on SessionExpiredError"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Bulk lane is a DISTINCT path: _ingest_one_bulk reuses the _ingest_one body minus the parent-destino gate; _ingest_one stays byte-for-byte unchanged (git diff: pure additions, no removed lines inside its body)"
    - "uf is DERIVED (ibge_match.uf) from the national geocode, never an input arg — the all-Brazil geoId 294280 lane has no per-UF context"
    - "_ingest_one_bulk returns bool (True=row written, False=ibge_unmatched) so produce_paginated counts unmatched cards as live errors — reconciles the plan's MEDIUM-1 acceptance (error_count>0 on unmatched) with the internally-handled quarantine"
    - "Per-page durability: self._session.commit() BEFORE sweep_progress.record_page so last_completed_offset never points at rolled-back data (Pitfall 3)"
    - "SessionExpiredError raised by the client iterator is OUTSIDE the per-card try/except → propagates out of produce_paginated for the task-layer fail-fast"
    - "Logging discipline (T-15-06-02): the only logger call (ta_bulk_page_ingested) emits offset/ingested/errors — never name/address/cookies/user_agent/session_id"

key-files:
  created:
    - tests/unit/lanes/tripadvisor/test_atrativos_bulk.py
  modified:
    - brave/lanes/tripadvisor/atrativos.py

decisions:
  - "_ingest_one_bulk returns bool instead of the plan's literal `-> None` signature. Rationale: Task 2's binding acceptance criterion requires error_count>0 when a card hits ibge_unmatched, but that quarantine is handled INTERNALLY (no raise) mirroring _ingest_one. A bool return is the cleanest signal that lets produce_paginated call record_error for unmatched cards. The internal quarantine behaviour (Task 1 acceptance) is unchanged."
  - "Bulk path always geocodes nationally (geocode_national) — listing cards carry no lat/lng, so there is no card-coordinate fast path to reuse; resolution is geocode → resolve_municipio_national(50km) or ibge_unmatched."

# Metrics
metrics:
  duration: ~25m
  tasks_completed: 2
  files_created: 1
  files_modified: 1
  tests_added: 8
  completed: 2026-06-26
requirements-completed: [TA-12]
---

# Phase 15 Plan 06: Bulk Nascente Ingest Path Summary

Parent-less national attraction ingest reaches Nascente fully município-resolved (geocode_national + resolve_municipio_national), per-page-committed for resume safety, with a live error counter — resolving the operator-locked A1 blocker end-to-end offline.

## What was built

**Task 1 — `_ingest_one_bulk` (parent-less national ingest).** A NEW method on `TripAdvisorAtrativosIngest` that copies the `_ingest_one` §7.6 + store_raw body but BYPASSES the `parent_destino_absent` quarantine gate. It geocodes the card nationally via `self._geocoder.geocode_national(location_id, name)`, derives the município + `uf` from the nearest IBGE seat via `resolve_municipio_national(lat, lng, ibge_records, max_distance_km=50.0)`, and builds `TripAdvisorAtrativoPayload(..., parent_rio_id=None, parent_source_ref=None)`. A card that cannot be geocoded OR has no IBGE seat within 50 km is quarantined as `ibge_unmatched` (no Nascente row), never silently dropped. LGPD aggregate-only review signals (`review_count`/`rating`/`most_recent_review_at=None`) are preserved; `TripAdvisorReviewSignals` `extra="forbid"` blocks author/text drift.

**Task 2 — `produce_paginated` (per-page commit + progress + error counter).** Drives `async for offset, cards in self._client.fetch_attractions_paginated(geo_id, start_page, max_pages)`, ingests every card via `_ingest_one_bulk` inside a per-card try/except (raised failures → `quarantine_poison` + `record_error`), commits the session ONCE per page BEFORE calling `sweep_progress.record_page(redis, offset, ingested)` (Pitfall 3 resume integrity), and increments the live panel `error_count` for both raised failures and `ibge_unmatched` returns. `SessionExpiredError` from the client iterator propagates out (task-layer fail-fast).

`_ingest_one` (the per-UF destinos-driven parent-linkage path) is left byte-for-byte unchanged — verified by `git diff` showing pure additions with no removed lines inside its body.

## Tests (8, all offline, RUN_REAL_EXTERNALS unset)

`tests/unit/lanes/tripadvisor/test_atrativos_bulk.py`:
- `test_ingest_bulk_writes_parentless_nascente_row` — one Nascente row, `entity_type="attraction"`, `parent_rio_id`/`parent_source_ref` None, `uf="MG"` derived from the IBGE match, município resolved; national geocode used (not per-UF).
- `test_ingest_bulk_unresolvable_quarantines_ibge_unmatched` — geocoder miss → `ibge_unmatched` quarantine, zero rows, no `parent_destino_absent` path.
- `test_ingest_bulk_no_ibge_seat_within_radius_quarantines` — geocode succeeds but lands far from every seat → `ibge_unmatched`.
- `test_review_signals_reject_extra_fields_lgpd` — `TripAdvisorReviewSignals` rejects author/text (LGPD guard intact).
- `test_produce_paginated_ingests_all_pages` — 2 pages × 30 cards → 60 rows; `pages_done=2`, `attractions_ingested=60`, `current_offset=30`, `error_count=0`, resume offset 30.
- `test_produce_paginated_commits_per_page_before_record_page` — `commit` fires once per page and precedes `record_page` (mock-manager call ordering).
- `test_produce_paginated_session_expiry_propagates` — `SessionExpiredError` mid-iteration propagates out (not swallowed).
- `test_produce_paginated_unmatched_card_increments_error_count` — one resolvable + one unmatched card → `error_count > 0`, `attractions_ingested=1` (MEDIUM-1 fix verified).

Full file green (8/8); existing `test_atrativos.py` (10) + `test_sweep_progress.py` (10) unchanged and green (28 total).

## Acceptance gates verified

- `grep -q 'def _ingest_one_bulk'` and `grep -q 'def produce_paginated'` → both present.
- `_ingest_one` body unchanged — `git diff` shows no removed lines inside the method.
- Logging-discipline grep gate — the single `logger.info("ta_bulk_page_ingested", ...)` call carries offset/ingested/errors only; no `cookies`/`user_agent`/`session_id`/`name=`/`address` token in any logger call.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Plan inconsistency] `_ingest_one_bulk` returns `bool` instead of the literal `-> None`**
- **Found during:** Task 2
- **Issue:** Task 1's action specified `-> None`, but Task 2's binding acceptance criterion requires `error_count > 0` when a card hits `ibge_unmatched`. That quarantine is handled INTERNALLY in `_ingest_one_bulk` (no raise, mirroring `_ingest_one`), so the per-card `except` in `produce_paginated` would never see it and the counter would stay at 0 — contradicting the acceptance test.
- **Fix:** `_ingest_one_bulk` returns `True` (row written) / `False` (ibge_unmatched). `produce_paginated` treats `False` as a live error → `sweep_progress.record_error(redis)`. Internal quarantine behaviour (Task 1 acceptance) is unchanged.
- **Files modified:** brave/lanes/tripadvisor/atrativos.py
- **Commit:** feat(15-06) implementation commit.

## Self-Check

- `brave/lanes/tripadvisor/atrativos.py` — FOUND (modified; `_ingest_one_bulk` + `produce_paginated` present).
- `tests/unit/lanes/tripadvisor/test_atrativos_bulk.py` — FOUND (created; 8 tests green).
- Commits verified present on `worktree-agent-a04d35a914093e3b9`: test RED + feat GREEN + (this) docs.

## Self-Check: PASSED
