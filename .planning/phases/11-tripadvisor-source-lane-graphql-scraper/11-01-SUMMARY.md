---
phase: 11-tripadvisor-source-lane-graphql-scraper
plan: "01"
subsystem: tripadvisor-client-layer
tags: [tripadvisor, graphql, playwright, client-protocol, null-client, fake-client, geo, tdd]
dependency-graph:
  requires: []
  provides:
    - TripAdvisorClientProtocol (brave/clients/base.py)
    - NullTripAdvisorClient (brave/clients/null_tripadvisor.py)
    - TripAdvisorClient (brave/lanes/tripadvisor/client.py)
    - FakeTripAdvisorClient (tests/fakes/fake_tripadvisor.py)
    - TripAdvisorConfig (brave/config/settings.py)
    - geo.resolve_geo_id (brave/lanes/tripadvisor/geo.py)
    - data/tripadvisor/uf_geoids.json
  affects:
    - brave/config/settings.py (ScoreConfig, AppConfig)
    - brave/clients/base.py (9th protocol added)
    - pyproject.toml (scraper optional dep group)
tech-stack:
  added:
    - playwright>=1.52.0 (optional scraper dep group — not in CI)
    - httpx (already in stack; used for GraphQL POST)
    - fakeredis (dev; already in stack; used for geo tests)
    - respx (dev; already in stack; used for httpx mocking)
  patterns:
    - TDD (RED→GREEN per task)
    - Structural Protocol typing (_check_protocol_compliance)
    - Playwright lazy-import inside _bootstrap_session only (never at module top-level)
    - Redis-cached geo resolution with seed JSON fallback (fail-closed ValueError)
    - SessionExpiredError on 403/429 for re-bootstrap trigger
    - Call-recording Fake client pattern (mirrors fake_places.py)
key-files:
  created:
    - brave/clients/null_tripadvisor.py
    - brave/lanes/tripadvisor/__init__.py
    - brave/lanes/tripadvisor/client.py
    - brave/lanes/tripadvisor/geo.py
    - data/tripadvisor/uf_geoids.json
    - tests/fakes/fake_tripadvisor.py
    - tests/unit/clients/test_null_tripadvisor.py
    - tests/unit/lanes/tripadvisor/__init__.py
    - tests/unit/lanes/tripadvisor/test_client.py
    - tests/unit/lanes/tripadvisor/test_geo.py
  modified:
    - brave/clients/base.py (TripAdvisorClientProtocol added as 9th protocol)
    - brave/config/settings.py (TripAdvisorConfig, ScoreConfig fields, AppConfig nesting)
    - pyproject.toml (scraper optional dep group, real_browser marker)
decisions:
  - "TripAdvisorConfig has 5 fields (proxy_url, session_ttl, query_id_override, ibge_match_threshold, ibge_max_distance_km); env_prefix=BRAVE_TA_; no alias (CR-02)"
  - "ScoreConfig gains mar_ready_atualidade_bar=70.0 and mar_ready_corrob_bar=60.0 (not TripAdvisorConfig — shared with route_by_score)"
  - "Playwright lazy-imported inside _bootstrap_session only — never at module top-level; never in CI"
  - "geo.py resolves UF→geoId via Redis-first, seed-JSON fallback, ValueError fail-closed; TTL=86400s (24h)"
  - "uf_geoids.json seeds all 27 UFs with ASSUMED geoIds — must validate on first real_browser test run"
  - "FakeTripAdvisorClient records calls in destinations_calls/attractions_calls/resolve_calls lists"
  - "real_browser pytest marker registered; @pytest.mark.real_browser gating test that exercises _bootstrap_session"
  - "scraper optional dep group in [project.optional-dependencies] — NOT in dev or CI groups"
  - "fakeredis.setex() shows DeprecationWarning in fakeredis 2.x — real Redis 7+ still supports setex; acceptable"
metrics:
  duration: "~25min"
  completed: "2026-06-23"
  tasks: 2
  files: 13
requirements_completed:
  - TA-01
---

# Phase 11 Plan 01: TripAdvisor Client Layer Summary

**One-liner:** TripAdvisor GraphQL hybrid client with Playwright DataDome bootstrap seam, Redis-cached geo resolution, and offline-safe NullTripAdvisorClient + FakeTripAdvisorClient behind TripAdvisorClientProtocol.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | TripAdvisorConfig + TripAdvisorClientProtocol + NullTripAdvisorClient + geo.py | fdf8679 | brave/config/settings.py, brave/clients/base.py, brave/clients/null_tripadvisor.py, brave/lanes/tripadvisor/geo.py, data/tripadvisor/uf_geoids.json |
| 2 | TripAdvisorClient + FakeTripAdvisorClient + pyproject.toml scraper group | 2ea0e85 | brave/lanes/tripadvisor/client.py, tests/fakes/fake_tripadvisor.py, pyproject.toml |

TDD commits also recorded:
- `350feed` — test(11-01): RED tests Task 1
- `2d21e7f` — test(11-01): RED tests Task 2

## What Was Built

### TripAdvisorConfig (brave/config/settings.py)
New `BaseSettings` sub-config with `env_prefix="BRAVE_TA_"`:
- `proxy_url: str = ""` — residential proxy for DataDome bypass; never logged
- `session_ttl: int = 1800` — DataDome cookie TTL (30 min default)
- `query_id_override: dict[str, str] = {}` — escape hatch when queryId rotates
- `ibge_match_threshold: int = 88` — rapidfuzz cutoff for IBGE matching
- `ibge_max_distance_km: float = 15.0` — haversine fallback radius

### ScoreConfig additions (brave/config/settings.py)
Two new fields for TA-05 `route_by_score` integration (in ScoreConfig, not TripAdvisorConfig — shared threshold):
- `mar_ready_atualidade_bar: float = 70.0` (env: `BRAVE_SCORE_MAR_READY_ATUALIDADE_BAR`)
- `mar_ready_corrob_bar: float = 60.0` (env: `BRAVE_SCORE_MAR_READY_CORROB_BAR`)

### TripAdvisorClientProtocol (brave/clients/base.py)
9th protocol added to the existing 8-protocol inventory (CORE-11 + TA-01):
- `fetch_destinations(uf: str) -> list[dict]`
- `fetch_attractions(geo_id: int, offset: int = 0) -> list[dict]`
- `resolve_geo_id(uf: str) -> int`

### NullTripAdvisorClient (brave/clients/null_tripadvisor.py)
Production-safe offline stub:
- All methods return empty lists / 0
- Never imports Playwright
- `_check_protocol_compliance()` asserts structural typing at call time

### geo.py (brave/lanes/tripadvisor/geo.py)
UF → geoId resolution:
- `resolve_geo_id(uf, redis, config, *, seed_path=None)` — Redis first → seed JSON fallback → ValueError (fail-closed)
- `load_uf_geoids(path: Path) -> dict[str, int]` — reads the committed JSON
- `REDIS_GEO_TTL = 86400` (24h); `REDIS_GEO_KEY_PREFIX = "brave:ta:geo:"`
- `GEO_SEED_PATH` points to `data/tripadvisor/uf_geoids.json`

### data/tripadvisor/uf_geoids.json
All 27 Brazilian UFs seeded with ASSUMED integer geoIds (from RESEARCH.md §4). Must validate via typeahead query on first `@pytest.mark.real_browser` test run.

### TripAdvisorClient (brave/lanes/tripadvisor/client.py)
Real implementation:
- `_bootstrap_session()` — lazy-imports `playwright.sync_api.sync_playwright`; navigates TA; intercepts `page.on("request")` for `*/data/graphql/ids` POSTs; extracts queryIds + DataDome cookies; caches in Redis with TTL
- `_get_session()` — Redis cache hit → return; miss → `_bootstrap_session()`
- `fetch_destinations(uf)` — geo resolution + httpx persisted-query POST; 403/429 → `SessionExpiredError`; max 50 pages guard
- `fetch_attractions(geo_id, offset)` — same pattern for attractions queryId
- `resolve_geo_id(uf)` — delegates to geo module
- `BRAVE_TA_SESSION_KEY = "brave:ta:session"`
- `SessionExpiredError(Exception)` — raised on 403/429
- `_check_protocol_compliance()` at module bottom

### FakeTripAdvisorClient (tests/fakes/fake_tripadvisor.py)
Call-recording test client:
- `fixture_destinations: dict[str, list[dict]]` — keyed by UF
- `fixture_attractions: dict[int, list[dict]]` — keyed by geoId
- `geo_ids: dict[str, int]` — for `resolve_geo_id` stub
- `destinations_calls`, `attractions_calls`, `resolve_calls` — recording lists
- `_check_protocol_compliance()` asserts TripAdvisorClientProtocol

### pyproject.toml
- `[project.optional-dependencies] scraper = ["playwright>=1.52.0"]` — separate from dev/CI
- `real_browser` marker registered in `[tool.pytest.ini_options]` markers

## Test Results

| Suite | Tests | Result |
|-------|-------|--------|
| tests/unit/clients/test_null_tripadvisor.py | 4 | PASS |
| tests/unit/lanes/tripadvisor/test_geo.py | 6 | PASS |
| tests/unit/lanes/tripadvisor/test_client.py | 14 | PASS |
| Full unit suite (not real_browser) | 310+ | PASS (no regressions) |

All 24 new tests pass. No existing tests broken.

## Deviations from Plan

None — plan executed exactly as written.

The only minor note: `fakeredis` 2.x emits a DeprecationWarning on `setex` (recommends `set` with `ex=`). The real Redis 7+ library still supports `setex` natively. The warning does not affect test correctness. Deferring the fakeredis API migration to a future cleanup — out of scope for this plan.

## Known Stubs

| Stub | File | Reason |
|------|------|--------|
| uf_geoids.json all 27 values | data/tripadvisor/uf_geoids.json | ASSUMED values from RESEARCH.md §4 — must be validated via typeahead query on first real_browser test run. TA-01 documents this explicitly. |
| queryId extraction heuristic | brave/lanes/tripadvisor/client.py:_bootstrap_session | ASSUMED Shape A response format; live capture resolves the actual shape on first bootstrap. query_id_override config escape handles mis-captures. |

These stubs do not prevent the plan's goal (establishing the client protocol seam); subsequent plans consume TripAdvisorClientProtocol through Fake/Null until a real_browser run validates the live behavior.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| T-11-01-01 mitigated | brave/lanes/tripadvisor/client.py | proxy_url logged as "[redacted]" in structlog; never emits actual URL |
| T-11-01-02 mitigated | brave/lanes/tripadvisor/client.py | Session cookie jar stored in Redis with TTL=session_ttl (1800s default); never logged at INFO level |
| T-11-01-03 mitigated | brave/lanes/tripadvisor/client.py | Playwright only reachable via _bootstrap_session; never at module top-level; not importable from API path |
| T-11-01-SC mitigated | pyproject.toml | scraper dep group is separate optional group; not in dev/CI; slopcheck gate documented in pyproject.toml comment |

## Self-Check: PASSED

Verified created files exist:
- [x] brave/clients/null_tripadvisor.py — FOUND
- [x] brave/lanes/tripadvisor/__init__.py — FOUND
- [x] brave/lanes/tripadvisor/client.py — FOUND
- [x] brave/lanes/tripadvisor/geo.py — FOUND
- [x] data/tripadvisor/uf_geoids.json — FOUND (27 keys)
- [x] tests/fakes/fake_tripadvisor.py — FOUND
- [x] tests/unit/clients/test_null_tripadvisor.py — FOUND
- [x] tests/unit/lanes/tripadvisor/__init__.py — FOUND
- [x] tests/unit/lanes/tripadvisor/test_client.py — FOUND
- [x] tests/unit/lanes/tripadvisor/test_geo.py — FOUND

Verified commits exist:
- [x] 350feed — test(11-01): RED Task 1
- [x] fdf8679 — feat(11-01): Task 1 implementation
- [x] 2d21e7f — test(11-01): RED Task 2
- [x] 2ea0e85 — feat(11-01): Task 2 implementation
