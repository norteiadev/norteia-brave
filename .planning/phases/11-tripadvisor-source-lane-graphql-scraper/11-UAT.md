---
status: testing
phase: 11-tripadvisor-source-lane-graphql-scraper
source:
  - 11-01-SUMMARY.md
  - 11-02-SUMMARY.md
  - 11-03-SUMMARY.md
  - 11-04-SUMMARY.md
  - 11-05-SUMMARY.md
started: 2026-06-23T21:47:38Z
updated: 2026-06-23T21:47:38Z
---

## Current Test
<!-- OVERWRITE each test - shows where we are -->

number: 1
name: Cold Start Smoke Test
expected: |
  With a fresh DB, `alembic upgrade head` applies migration 0006 and the
  FastAPI server boots without errors. A primary query (e.g. GET /engine/status)
  returns live data including a `source` field.
awaiting: user response

## Tests

### 1. Cold Start Smoke Test
expected: With a fresh DB, `alembic upgrade head` applies migration 0006 (rio_records.mar_ready) and the FastAPI server boots with no errors. GET /engine/status returns 200 with a `source` field present.
result: [pending]

### 2. Engine source selector (dashboard)
expected: On the dashboard EngineControl (/processo), a TripAdvisor source option appears in a source radiogroup. Selecting TripAdvisor reveals a UF chip multi-select (27 BR states). Selecting "default" hides the UF chips. The active source is read back/displayed.
result: [pending]

### 3. Start engine with TripAdvisor source
expected: Starting the engine with source=tripadvisor + selected UFs is accepted (200) and /engine/status then reports source="tripadvisor". An invalid source value is rejected with HTTP 422 before any state mutation.
result: [pending]

### 4. /mar-ready route lists attractions
expected: Navigating to /mar-ready (also linked from the dashboard nav/SURFACES) shows a table of mar_ready attractions. Empty state renders cleanly when none exist.
result: [pending]

### 5. Single promote (optimistic)
expected: Clicking "Promover" on a row optimistically removes it from the list immediately. On a 409 (not mar_ready) the row is rolled back (reappears) with an error surfaced.
result: [pending]

### 6. Bulk promote (multi-select)
expected: Selecting multiple rows and confirming the bulk promote (AlertDialog confirm) promotes all selected attractions and removes them from the list.
result: [pending]

### 7. Promote-override gate (backend)
expected: PATCH promote on a non-mar_ready attraction returns 409 (PromoteNotAllowed). On a mar_ready attraction it promotes to Mar bypassing the ≥85 gate, and a promotion_reason is recorded in MarRecord provenance. The canonical ≥85 gate is unchanged for everything else.
result: [pending]

### 8. TripAdvisor sweep produces scored records
expected: Running a TripAdvisor sweep for a UF ingests destinos + atrativos into Nascente→Rio, scores reviews via §7.6 (corroboração + atualidade), and records land in DLQ by score (never auto-Mar). Review-validated attractions get flagged mar_ready. (Requires real scraper / RUN_REAL_EXTERNALS — operator-gated.)
result: issue
reported: "Real ES sweep run end-to-end (worker + API + engine). Engine cycle visible (RUNNING→idle, sweep_tripadvisor dispatched), but 0 records ingested. Two code defects in the never-tested real path were found and fixed (commit 890b7da): (1) ibge.load_ibge_csv str.open AttributeError; (2) Playwright Sync API inside asyncio loop. After fixes the sweep runs clean but live capture is blocked by DataDome on direct IP (graphql requests=0, captcha body, cookie_count=2, query_ids=[]) → needs residential proxy (BRAVE_TA_PROXY_URL)."
severity: major
testing_infra: "Full real stack stood up — celery worker, fresh uvicorn (.env), Playwright+chromium installed, Postgres@0006, Redis. RUN_REAL_EXTERNALS=true."

## Summary

total: 8
passed: 0
issues: 0
pending: 8
skipped: 0

## Gaps

- truth: "TripAdvisor sweep ingests scored destinos/atrativos for a real UF (ES)"
  status: partial
  reason: "Code path fixed (2 bugs) and runs end-to-end, but live capture blocked by DataDome on direct IP — 0 records. Residential proxy required to validate real ingestion."
  severity: major
  test: 8
  fixed_in: 890b7da
  artifacts:
    - "brave/lanes/tripadvisor/ibge.py — load_ibge_csv str→Path coercion (FIXED)"
    - "brave/lanes/tripadvisor/client.py — _bootstrap_session runs sync_playwright in a thread, loop-safe (FIXED)"
  missing:
    - "Residential proxy (BRAVE_TA_PROXY_URL) to pass DataDome and capture queryIds — then re-run ES sweep to confirm Nascente→Rio→DLQ/mar_ready with real data"
    - "Consider an offline integration test that drives produce() through FakeTripAdvisorClient end-to-end (would have caught the sync/async + Path bugs the unit tests missed)"
