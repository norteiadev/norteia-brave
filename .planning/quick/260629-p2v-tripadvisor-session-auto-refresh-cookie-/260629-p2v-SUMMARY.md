---
phase: quick-260629-p2v
plan: "01"
subsystem: tripadvisor-session
tags: [session, cookies, keep-alive, celery-beat, write-back, offline]
dependency_graph:
  requires: []
  provides: [ta-session-auto-refresh, ta-keepalive-beat, persist_rotated_cookies]
  affects: [brave/lanes/tripadvisor/client.py, brave/tasks/pipeline.py, brave/tasks/beat_schedule.py]
tech_stack:
  added: []
  patterns:
    - "Lazy import (noqa: PLC0415) for circular-import avoidance in session.py → client.py"
    - "best-effort / non-fatal pattern: try/except inside helper + belt-and-suspenders try/except in callers"
    - "Global redis.from_url monkeypatch for multi-site fakeredis sharing in tests"
key_files:
  created:
    - brave/lanes/tripadvisor/session.py
    - tests/unit/lanes/tripadvisor/test_session_writeback.py
    - tests/unit/tasks/test_ta_keepalive.py
  modified:
    - brave/lanes/tripadvisor/client.py
    - brave/config/settings.py
    - brave/tasks/pipeline.py
    - brave/tasks/beat_schedule.py
decisions:
  - "persist_rotated_cookies placed in session.py (not client.py) to avoid circular import; client lazy-imports it per existing PLC0415 pattern"
  - "Client wraps each persist_rotated_cookies call in try/except as belt-and-suspenders (in addition to helper's internal try/except), so test monkeypatching the whole function still can't abort the fetch"
  - "ta_keepalive uses asyncio.run(_ping()) matching sweep_tripadvisor pattern"
  - "beat_schedule.py re-applies app.conf.beat_schedule after adding ta-keepalive entry"
metrics:
  duration: "~25 minutes"
  completed: "2026-06-29"
  tasks_completed: 2
  files_changed: 7
---

# Quick Task 260629-p2v: TripAdvisor Session Auto-Refresh — Summary

**One-liner:** Single `persist_rotated_cookies()` helper + 3-transport client wiring + 10-min keep-alive Celery beat eliminate operator re-paste by sliding the Redis session TTL on every TA response.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | persist_rotated_cookies helper + wire to 3 client transports | c66e1b5 | session.py (new), client.py, test_session_writeback.py (new) |
| 2 | Keep-alive beat task + settings field + beat registration | 3bd1def | settings.py, pipeline.py, beat_schedule.py, test_ta_keepalive.py (new) |

## What Was Built

### A. Cookie Write-Back (Task 1)

**`brave/lanes/tripadvisor/session.py`** — new module exporting `persist_rotated_cookies(redis, response_cookies, ta_config)`:
- Merges `Set-Cookie` response headers into `brave:ta:session` (response wins on collision; long-lived cookies like `TAAUTHEAT` are preserved)
- Slides TTL to `ta_config.session_ttl` on every successful write (sliding window)
- Re-derives `session_id` from `TASID` when present in response
- Normalises Phase-11 list-form cookies before merge (backwards compat)
- Best-effort / non-fatal: internal `try/except` swallows Redis/parse errors; logs `error_type` only (T-p2v-01)

**`brave/lanes/tripadvisor/client.py`** — 3 transport methods wired:
- `fetch_destinations`: lazy-imports `persist_rotated_cookies` at method scope; updates local `cookies` var per page for next iteration
- `fetch_attractions`: single POST — calls helper, no local var update needed
- `fetch_attractions_paginated`: same pattern as fetch_destinations — updates local `cookies` var per page

All three wrap the call in `try/except Exception: pass` as belt-and-suspenders (helper itself never raises, but tests monkeypatch the whole function).

### B. Keep-Alive Beat (Task 2)

**`brave/config/settings.py`** — `TripAdvisorConfig.keepalive_interval_seconds: int = Field(default=600, ...)` (env: `BRAVE_TA_KEEPALIVE_INTERVAL_SECONDS`; CR-02: no alias)

**`brave/tasks/pipeline.py`** — `@shared_task(bind=False, name="brave.ta_keepalive", ignore_result=True)`:
- Skips when `run_real_externals=False` (offline/CI gate)
- Skips when `brave:ta:session` TTL ≤ 0 (no session present)
- Issues ONE HTML GET via `fetch_attractions_paginated(geo_id=294280, max_pages=1)` to re-mint `datadome`
- Write-back + TTL slide handled inside `fetch_attractions_paginated` (shared helper)
- On `SessionExpiredError`/`SessionMissingError`: calls `_mark_needs_bootstrap()` + `collection_engine.set_enabled(rc, False)` + `mark_idle(rc)` — same fallback as sweep
- On any other exception: logs `error_type` only; never crashes the beat (T-p2v-02)

**`brave/tasks/beat_schedule.py`** — `BRAVE_BEAT_SCHEDULE["ta-keepalive"]` registered with `timedelta(seconds=600)`, `queue="brave.sweep"`.

## Test Results

| Suite | Tests | Result |
|-------|-------|--------|
| test_session_writeback.py (new) | 11 | PASS |
| test_ta_keepalive.py (new) | 8 | PASS |
| tests/unit/lanes/tripadvisor/ (existing) | 150 | PASS |
| tests/unit/tasks/ + tests/unit/api/ (existing) | 60 | PASS |
| **Total** | **210** | **ALL GREEN** |

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

### Design Notes (not deviations)

1. The plan's `persist_rotated_cookies` placement note said "at method scope (not inside loop)". The lazy import is placed before the loop, the rotated-cookie capture + helper call is inside the loop — this matches the intent (import once per call, write-back per iteration).

2. Client `try/except` wrapper added around each `persist_rotated_cookies` call: the plan required `test_fetch_destinations_writeback_error_does_not_abort_fetch` to monkeypatch the WHOLE function to raise. Since the helper's own `try/except` would then be bypassed, the client-level guard was needed to satisfy the test without changing the test design.

## Threat Model Compliance

All T-p2v-* mitigations applied:

| Threat ID | Mitigation Applied |
|-----------|-------------------|
| T-p2v-01 | session.py logs only `rotated_cookie_count` (int) and `error_type` (class name) — no cookie names or values |
| T-p2v-02 | ta_keepalive logs only `error_type=type(exc).__name__` — never `str(exc)` |
| T-p2v-03 | Only tripadvisor.com Set-Cookie headers merged; same trust boundary as original session injection |
| T-p2v-04 | One HTML GET per interval; TTL check prevents HTTP when no session |

## Known Stubs

None — all wiring is live (read from and write to the same `brave:ta:session` Redis key used by the injection endpoint).

## Self-Check

- `brave/lanes/tripadvisor/session.py` exists with `persist_rotated_cookies` exported: FOUND
- `brave/lanes/tripadvisor/client.py` contains 3+ `persist_rotated_cookies` occurrences: FOUND (lines 381, 515, 631)
- `brave/config/settings.py` has `keepalive_interval_seconds`: FOUND
- `brave/tasks/pipeline.py` has `brave.ta_keepalive` task: FOUND
- `brave/tasks/beat_schedule.py` has `ta-keepalive` entry: FOUND
- Task 1 commit c66e1b5: FOUND
- Task 2 commit 3bd1def: FOUND
- 210 tests pass, 0 failed: CONFIRMED

## Self-Check: PASSED
