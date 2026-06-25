---
phase: "13"
plan: "01"
subsystem: "lanes/tripadvisor"
tags: ["tripadvisor", "graphql", "session", "fetch_attractions", "AttractionsFusion"]
dependency_graph:
  requires: ["Phase 12 session-injection seam (12-02)"]
  provides: ["fetch_attractions with real AttractionsFusion qid + variables", "session_id in session model", "ta_bootstrap TASID extraction + qid reject list"]
  affects: ["brave/lanes/tripadvisor/client.py", "brave/api/routers/tripadvisor_session.py", "scripts/ta_bootstrap.py", "brave/clients/base.py", "brave/clients/null_tripadvisor.py", "tests/fakes/fake_tripadvisor.py"]
tech_stack:
  added: []
  patterns: ["preRegisteredQueryId hardcoded to prevent qid injection", "SingleFlexCardSection parser with safe .get() fallback", "TASID-derived session_id threaded into variables.sessionId"]
key_files:
  created: []
  modified:
    - "brave/lanes/tripadvisor/client.py"
    - "brave/api/routers/tripadvisor_session.py"
    - "brave/clients/base.py"
    - "brave/clients/null_tripadvisor.py"
    - "scripts/ta_bootstrap.py"
    - "tests/fakes/fake_tripadvisor.py"
    - "tests/unit/api/test_tripadvisor_session.py"
    - "tests/unit/clients/test_null_tripadvisor.py"
    - "tests/unit/lanes/tripadvisor/test_client.py"
decisions:
  - "Hardcoded qid a5cb7fa004b5e4b5 in fetch_attractions (not read from session) — prevents injection of stale/telemetry qid via session payload"
  - "Single-page default (PAGINATION GAP) — AttractionsFusion page/offset param unconfirmed; looping with identical payload would duplicate page 1"
  - "session_id derived from cookies[TASID] when SessionInjectBody.session_id omitted — preserves backwards compatibility, no breaking change"
  - "fetch_attractions signature: offset removed, max_pages added — updated all protocol/stub/test files for consistency"
metrics:
  duration: "9m"
  completed_at: "2026-06-25T14:42:00Z"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 9
  tests_added: 14
  tests_passing: 100
---

# Phase 13 Plan 01: TripAdvisor AttractionsFusion Rewire Summary

**One-liner:** Rewired `fetch_attractions` to the live-validated AttractionsFusion listing query (qid `a5cb7fa004b5e4b5`) with real `request.routeParameters` variables shape, threaded `session_id` (TASID) through the session model, and extended `ta_bootstrap` with a listing-qid reject list and TASID extraction.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | Rewire fetch_attractions + add session_id to session model | `d06ffe0` | client.py, tripadvisor_session.py, base.py, null_tripadvisor.py, fake_tripadvisor.py, test_tripadvisor_session.py, test_null_tripadvisor.py, test_client.py |
| 2 | Extend ta_bootstrap + add offline AttractionsFusion + Bootstrap tests | `d3692c7` | ta_bootstrap.py, test_client.py |

## What Was Built

### Task 1: fetch_attractions rewire + session_id

**`brave/lanes/tripadvisor/client.py`:**
- Added `import uuid` (for `pageviewUid` generation)
- Added `_parse_attractions_page(raw_sections)` static method: filters `__typename == "WebPresentation_SingleFlexCardSection"`, extracts `singleFlexCardContent` per card, returns normalized dicts with keys `name`, `locationId`, `rating`, `review_count`, `category`. Uses `.get()` with safe defaults; skips malformed cards with debug log.
- Rewrote `fetch_attractions(geo_id, max_pages=None)`: removed `offset` param, added `max_pages`; hardcoded qid `a5cb7fa004b5e4b5`; builds full `AttractionsFusion` variables shape; parses via `_parse_attractions_page`; single-page default with PAGINATION GAP comment.
- Updated module docstring to document `session_id` field and T-13-01-01 security note.

**`brave/api/routers/tripadvisor_session.py`:**
- Added `session_id: str | None = Field(default=None, ...)` to `SessionInjectBody`.
- `inject_session` now derives `session_id = body.session_id or body.cookies.get("TASID") or ""` and stores it in the Redis session dict.
- Audit log extended with `session_id_present: bool` (T-13-01-01: never the value).

**`brave/clients/base.py`:**
- Updated `TripAdvisorClientProtocol.fetch_attractions` signature: `offset` removed, `max_pages: int | None = None` added.

**`brave/clients/null_tripadvisor.py`** and **`tests/fakes/fake_tripadvisor.py`:**
- Updated `fetch_attractions` to use `max_pages` parameter.

**`tests/unit/api/test_tripadvisor_session.py`:**
- Added `test_inject_session_stores_session_id`: auto-derives `session_id` from `TASID` cookie (BLOCKER-2).
- Added `test_inject_session_explicit_session_id_wins`: explicit `session_id` field wins over cookie.

### Task 2: ta_bootstrap extend + new offline tests

**`scripts/ta_bootstrap.py`:**
- Added `KNOWN_NON_LISTING_QIDS` frozenset (5 qids: telemetry, pixel, aux, ads, trips).
- Added `LISTING_QID = "a5cb7fa004b5e4b5"` constant.
- `parse_curl`: reject-list check before qid classification — emits stderr warning and skips rejected qids.
- `parse_curl`: extracts `session_id = cookies.get("TASID", "")` and includes it in returned dict.
- `main()`: prints `session_id: found` or `NOT FOUND — TASID cookie missing…`.

**`tests/unit/lanes/tripadvisor/test_client.py`:**
- `TestTripAdvisorAttractionsFusionContract` (4 tests): qid + variables shape, Iguazu Falls parse, empty sections stops pagination, partial page stops pagination.
- `TestBootstrapQueryIdRejectList` (3 tests): rejects ad qid, extracts TASID, empty when no TASID.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated all fetch_attractions callers when removing offset param**
- **Found during:** Task 1 — removing `offset` from `fetch_attractions` required updating all call sites and tests.
- **Fix:** Updated `TripAdvisorClientProtocol.fetch_attractions`, `NullTripAdvisorClient.fetch_attractions`, `FakeTripAdvisorClient.fetch_attractions`, `test_null_tripadvisor.py`, and the existing `test_fetch_attractions_payload_shape` / `test_fake_records_attractions_calls` tests to use the new `max_pages` signature.
- **Files modified:** `brave/clients/base.py`, `brave/clients/null_tripadvisor.py`, `tests/fakes/fake_tripadvisor.py`, `tests/unit/clients/test_null_tripadvisor.py`, `tests/unit/lanes/tripadvisor/test_client.py`
- **Commit:** `d06ffe0`

**2. [Rule 2 - Security] Added `session_id_present: bool` to audit log**
- **Found during:** Task 1 — T-13-01-01 requires session_id is never logged; added presence-as-boolean to audit `after_state`.
- **Fix:** `session_id_present: bool(derived_session_id)` added to audit dict.
- **Files modified:** `brave/api/routers/tripadvisor_session.py`
- **Commit:** `d06ffe0`

## Verification Results

```
.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ tests/unit/api/test_tripadvisor_session.py
100 passed, 15 warnings
```

Spot checks:
- `grep -c "a5cb7fa004b5e4b5" brave/lanes/tripadvisor/client.py` → 3 (>=1 ✓)
- `grep -c "SingleFlexCardSection" brave/lanes/tripadvisor/client.py` → 2 (>=1 ✓)
- `grep -c "session_id" brave/api/routers/tripadvisor_session.py` → 6 (>=1 ✓)
- `grep -c "KNOWN_NON_LISTING_QIDS" scripts/ta_bootstrap.py` → 2 (>=1 ✓)

## Known Stubs

None — the plan's goals are fully achieved. The PAGINATION GAP (single-page only) is intentional and documented in a code comment; multi-page pagination via `PaginationLinksList` is an explicit follow-up in Phase 13 plans.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: information_disclosure | `brave/api/routers/tripadvisor_session.py` | `session_id` field added to `SessionInjectBody` and Redis session dict — mitigated per T-13-01-01: value never logged, only boolean presence in audit. |

## Self-Check: PASSED

- `brave/lanes/tripadvisor/client.py` — exists, contains `a5cb7fa004b5e4b5` and `SingleFlexCardSection`
- `brave/api/routers/tripadvisor_session.py` — exists, contains `session_id`
- `scripts/ta_bootstrap.py` — exists, contains `a5cb7fa004b5e4b5` and `KNOWN_NON_LISTING_QIDS`
- `tests/unit/lanes/tripadvisor/test_client.py` — exists, contains `TestTripAdvisorAttractionsFusionContract` and `TestBootstrapQueryIdRejectList`
- Task 1 commit `d06ffe0` — exists in git log
- Task 2 commit `d3692c7` — exists in git log
