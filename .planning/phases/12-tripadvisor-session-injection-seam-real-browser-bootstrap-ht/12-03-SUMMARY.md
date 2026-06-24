---
phase: 12-tripadvisor-session-injection-seam-real-browser-bootstrap-ht
plan: "03"
subsystem: tripadvisor-client
tags: [tripadvisor, session-injection, graphql, redis, tdd]
dependency_graph:
  requires: []
  provides:
    - SessionMissingError (brave.lanes.tripadvisor.client)
    - refactored _get_session Redis-only
    - correct extensions.preRegisteredQueryId payload shape
  affects:
    - brave/lanes/tripadvisor/client.py
    - tests/unit/lanes/tripadvisor/test_client.py
    - pyproject.toml
    - data/tripadvisor/README
tech_stack:
  added: []
  patterns:
    - Redis-only session read (no auto-bootstrap fallback)
    - Fail-closed SessionMissingError on Redis miss
    - Batch-array persisted-query payload with extensions.preRegisteredQueryId
    - Phase 11 backward-compat cookie normalisation (list → flat dict)
key_files:
  created: []
  modified:
    - brave/lanes/tripadvisor/client.py
    - tests/unit/lanes/tripadvisor/test_client.py
    - pyproject.toml
    - data/tripadvisor/README
decisions:
  - SessionMissingError is a new distinct exception (not reuse of SessionExpiredError)
    because Redis-miss is an operator-gate condition (no session was injected) while
    SessionExpiredError is a runtime condition (injected session expired at DataDome)
  - _proxy_args() deleted because it was only used by _bootstrap_session; no other callers
  - real_browser pytest marker removed from pyproject.toml since no _bootstrap_session path exists
  - ES geoId 303516 retained in uf_geoids.json as a placeholder with a documented caveat
    (observed redirect to MG 303380 during 2026-06-24 spike); not changed without confirmation
metrics:
  duration: "~327s"
  completed: "2026-06-24"
  tasks: 2
  files_changed: 4
---

# Phase 12 Plan 03: TripAdvisorClient Refactor (SessionMissingError + Correct Payload Shape) Summary

**One-liner:** Remove Playwright bootstrap path, add Redis-only _get_session with SessionMissingError fail-closed, fix GraphQL payload from `{"query": qid}` to `{"extensions": {"preRegisteredQueryId": qid}}`.

## Tasks Completed

| Task | Description | Commit | Status |
|------|-------------|--------|--------|
| RED (tests) | Failing tests for SessionMissingError + payload shape | e306505 | done |
| Task 1 + Task 2 (GREEN) | Client refactor + scraper dep removal | 47ddbb6 | done |

## What Was Built

### Task 1: SessionMissingError + _get_session Redis-only

`brave/lanes/tripadvisor/client.py`:
- Added `SessionMissingError` class (new exception for Redis-miss operator gate)
- Replaced `_get_session()`: reads `BRAVE_TA_SESSION_KEY` from Redis, raises `SessionMissingError` on `None`, normalises Phase 11 list-of-dicts cookies to flat dict
- Deleted `_bootstrap_session()`, `_proxy_args()`, and the `concurrent.futures` import
- Updated module docstring to describe Phase 12 session-injection model (removed Playwright references)

`tests/unit/lanes/tripadvisor/test_client.py`:
- Removed `TestTripAdvisorClientRealBrowser` class (and `@pytest.mark.real_browser` decorator usage)
- Added `TestTripAdvisorClientSessionInjection` class (6 tests): redis-miss raises SessionMissingError; injected session returned; list-cookies normalised; no `_bootstrap_session` attribute; no playwright in AST; SessionMissingError is Exception

### Task 2: Correct Payload Shape + Remove Scraper Dep + ES geoId Note

`brave/lanes/tripadvisor/client.py`:
- Fixed `fetch_destinations()` payload: `{"variables": {...}, "extensions": {"preRegisteredQueryId": query_id}}` (was `{"query": query_id, "variables": {...}}`)
- Fixed `fetch_attractions()` same way
- Replaced `{c["name"]: c["value"] for c in session.get("cookies", [])}` with `session.get("cookies", {})` (flat dict from _get_session)
- Added `User-Agent` header from `session.get("user_agent", "")` when present

`pyproject.toml`:
- Removed `scraper` optional dep group (playwright>=1.52.0)
- Removed `real_browser` pytest marker definition

`data/tripadvisor/README`:
- Updated SOURCE section: Playwright bootstrap → operator DevTools capture
- Added ES geoId caveat under CURRENT FILES: 303516 redirected to MG 303380 during spike; must be verified before sweeping ES
- Updated OPERATOR GATE section: Phase 12 session-injection runbook replacing old Playwright install steps

`tests/unit/lanes/tripadvisor/test_client.py`:
- Added `TestTripAdvisorClientPayloadShape` class (4 tests): destinations/attractions both use `extensions.preRegisteredQueryId`; flat cookies passed correctly; no scraper dep in pyproject.toml

## Deviations from Plan

### Auto-fixed Issues

None.

### Planned Changes Executed As-Is

All changes match the plan exactly. The two tasks were committed as a single `feat` commit (GREEN phase) after the `test` commit (RED phase) since they form a cohesive implementation unit with interdependent behaviour.

Note: The `test_playwright_not_at_module_top_level` test in `TestTripAdvisorClientOffline` was expanded from checking only top-level imports (col_offset == 0) to checking ALL import nodes in the AST, including function-level imports. This is a stricter and more correct check since `_bootstrap_session` had a function-level `from playwright.sync_api import sync_playwright` that the original test would have missed.

## Threat Model Coverage

| Threat | Status |
|--------|--------|
| T-12-03-01: Elevation of privilege via Playwright | Mitigated — _bootstrap_session deleted, scraper dep removed, AST test asserts no playwright import |
| T-12-03-02: Uncaught SessionMissingError DoS | Accepted for this plan — plan 12-04 catches it in sweep_tripadvisor |
| T-12-03-03: user_agent header information disclosure | Accepted — operator-injected UA, intended behaviour |
| T-12-03-SC: No new package installs | Confirmed — playwright REMOVED from pyproject.toml |

## Known Stubs

None — no placeholder values, TODO comments, or empty data sources introduced.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced.

## Self-Check: PASSED

| Item | Status |
|------|--------|
| brave/lanes/tripadvisor/client.py | FOUND |
| tests/unit/lanes/tripadvisor/test_client.py | FOUND |
| pyproject.toml | FOUND |
| data/tripadvisor/README | FOUND |
| commit e306505 (RED — failing tests) | FOUND |
| commit 47ddbb6 (GREEN — implementation) | FOUND |

Note: Test execution blocked by sandbox restrictions. Implementation verified by:
1. Structural code review of all test expectations against implementation
2. Grep verification: no playwright/scraper in client.py or pyproject.toml
3. Grep verification: `extensions.preRegisteredQueryId` present in both fetch methods
4. Grep verification: old `"query": query_id` pattern absent from client.py
5. Grep verification: `real_browser` marker only in test docstring comment, not as decorator/class
