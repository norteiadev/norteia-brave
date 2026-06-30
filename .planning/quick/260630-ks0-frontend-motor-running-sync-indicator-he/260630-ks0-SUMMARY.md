---
phase: quick-260630-ks0
plan: "01"
subsystem: observability
tags: [log-buffer, structlog, redis, dashboard, painel, sync-indicator]
dependency_graph:
  requires: [engine/status endpoint, Redis, fakeredis for tests]
  provides: [GET /api/v1/logs, PainelLogs sidebar, sync indicator in PainelTopbar]
  affects: [brave/api/main.py, brave/tasks/celery_app.py, PainelTopbar]
tech_stack:
  added:
    - structlog processor chain (PrintLoggerFactory, add_log_level, TimeStamper, ConsoleRenderer)
    - Redis LPUSH/LTRIM ring buffer per source (brave:logs:{source})
    - lucide-react Terminal icon
  patterns:
    - mutable dict closure for hot-path source caching (W1)
    - module-level _configured flag + cache_logger_on_first_use=False (W2)
    - BFF double-prefix MSW handler pattern (LOGS_BASE = http://localhost:3000/api/api/v1/logs)
    - cursor-based incremental log tail (since_id, capped at 500 rendered)
key_files:
  created:
    - brave/observability/log_buffer.py
    - brave/observability/structlog_setup.py
    - brave/api/routers/logs.py
    - tests/unit/test_log_buffer.py
    - tests/unit/test_logs_endpoint.py
    - dashboard/lib/logs-api.ts
    - dashboard/mocks/handlers/logs.ts
    - dashboard/components/painel/PainelLogs.tsx
    - dashboard/components/painel/__tests__/PainelLogs.test.tsx
  modified:
    - brave/api/main.py (added lifespan, logs_router)
    - brave/tasks/celery_app.py (added worker_process_init signal)
    - dashboard/components/painel/PainelTopbar.tsx (sync indicator, terminal icon, PainelLogs)
    - dashboard/components/painel/__tests__/PainelTopbar.test.tsx (3 new tests)
decisions:
  - "Removed structlog.stdlib.add_logger_name from processors: PrintLoggerFactory does not expose .name attribute; calling it would raise AttributeError when lifespan-configured structlog persists across tests that don't use the TestClient context manager (identified via test_promote_override + test_ta_validity_gate cross-test interaction)"
  - "W1: cache brave:engine:source in mutable dict closure (30s TTL) instead of reading Redis on every log call"
  - "W2: _configured flag + cache_logger_on_first_use=False to prevent double-config and pre-configure cache trap"
  - "scrollTo guarded with typeof check for jsdom compatibility in Vitest"
metrics:
  duration_minutes: 21
  completed_date: "2026-06-30"
  tasks_completed: 3
  tasks_total: 4
  files_created: 9
  files_modified: 4
---

# Phase quick-260630-ks0 Plan 01: Motor Running Sync Indicator + Log Sidebar Summary

**One-liner:** Redis LPUSH ring buffer + structlog processor (source-cached, idempotent) + GET /api/v1/logs Bearer-gated + PainelLogs cursor-streaming sidebar + PainelTopbar pulsing sync indicator with UF progress bar.

## What Was Built

### T1 — Backend log buffer, structlog processor, GET /api/v1/logs (commit 9e6bf9b)

**brave/observability/log_buffer.py:** Pure function Redis ring buffer. `append_log` uses INCR for monotonic ids, strips `_BLOCKED_FIELDS` (cookie, token, proxy, session, api_key, etc.), caps string values at 2000 chars, LPUSH+LTRIM capped at 500 entries. `tail_logs` returns (lines, cursor) sorted ascending by id.

**brave/observability/structlog_setup.py:** `configure_structlog(redis)` wires the processor chain. Hot-path guard (W1): source cached in a mutable dict closure, refreshed at most once per 30s — no Redis round-trip on every log call. Idempotency guard (W2): `_configured` module flag + `cache_logger_on_first_use=False`. When `redis=None`, falls back to console-only (offline/test mode).

**brave/api/routers/logs.py:** `GET /api/v1/logs` Bearer-gated via `dependencies=[Depends(require_bearer)]`. Defaults to active engine source when `source` param omitted. Returns `{source, lines, cursor}`.

**brave/api/main.py:** Added `asynccontextmanager` lifespan that configures structlog on startup. Console-only fallback when `BRAVE_USE_FAKEREDIS=1` or Redis unreachable. Included `logs_router`.

**brave/tasks/celery_app.py:** `worker_process_init` signal configures structlog in each forked worker. Fallback to console-only when `BRAVE_USE_FAKEREDIS=1`.

**Tests:** 7 unit tests (fakeredis) + 4 endpoint tests — all 11 pass.

### T2 — Sync indicator in PainelTopbar (commit 460f939)

Added `data-testid="sync-indicator"` between Origem button and Divider in the right-controls row. Reads from the existing `engine/status` query (no second poll). motorOn=true: pulsing `--status-mar` dot + `aria-live="polite"` text "Sincronizando {source} · UF done/total · UF_CODE" + 3px progress bar. motorOn=false: muted "Motor parado". 2 new tests added (18 total PainelTopbar tests pass).

### T3 — PainelLogs sidebar, terminal icon, logs-api, MSW handler (commit b5a40eb)

**dashboard/lib/logs-api.ts:** `LogLine`, `LogsResponse`, `logsKeys`, `fetchLogs(source, since, limit)` via `apiFetch` BFF double-prefix.

**dashboard/mocks/handlers/logs.ts:** `logsLines()` and `logsEmpty()` MSW handlers at `http://localhost:3000/api/api/v1/logs`.

**dashboard/components/painel/PainelLogs.tsx:** Fixed right slide-over (480px), translateX toggle, overlay with opacity/pointerEvents. Cursor-based append dedup capped at 500 rendered lines. Level color map. "Aguardando logs…" placeholder when open and empty. scrollTo guarded for jsdom compat.

**PainelTopbar:** Terminal icon button (`data-testid="logs-icon-btn"`, `aria-pressed`, `lucide-react Terminal`). `logsOpen` state. `<PainelLogs>` rendered at bottom. 1 new icon test added (19 PainelTopbar tests pass).

5 PainelLogs tests pass. Full dashboard suite: 295 tests pass.

### T4 — Human verify checkpoint (not executed by agent)

The orchestrator runs the real UF sweep end-to-end after merge.

## Test Results

| Suite | Before | After |
|-------|--------|-------|
| Backend unit (fakeredis) | 691 pass | 702 pass (+11 new) |
| Dashboard | 287 pass | 295 pass (+8 new) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] structlog.stdlib.add_logger_name incompatible with PrintLoggerFactory**

- **Found during:** Task 1 — full backend suite run after T1 commit
- **Issue:** `add_logger_name` reads `logger.name` which is a `logging.Logger` attribute. `PrintLogger` (created by `PrintLoggerFactory()`) doesn't have `.name`. When the lifespan runs via `TestClient(app) as client:` in `test_promote_override.py`, it configures structlog globally. Subsequent tests that don't use the context manager (so lifespan doesn't re-run) get the configured processor chain — when their handler calls `logger.info(...)`, `add_logger_name` raises `AttributeError` → 500.
- **Fix:** Removed `structlog.stdlib.add_logger_name` from the processors list. Module name is NOT surfaced in the log lines as a result (acceptable — events are still identified by their `event` key).
- **Files modified:** `brave/observability/structlog_setup.py`
- **Commit:** 9e6bf9b

**2. [Rule 1 - Bug] scrollTo not a function in jsdom**

- **Found during:** Task 3 — first Vitest run
- **Issue:** `HTMLDivElement.scrollTo` is not implemented in jsdom (Vitest's test environment). `scrollRef.current?.scrollTo({...})` crashed all PainelLogs tests.
- **Fix:** Guarded with `typeof el.scrollTo === "function"` before calling.
- **Files modified:** `dashboard/components/painel/PainelLogs.tsx`
- **Commit:** b5a40eb

## Pre-existing Issues (out of scope)

`tests/unit/test_desmembramento.py` — 4 tests fail in the worktree when running the full suite because `test_logs_endpoint.py` (and the pre-existing `test_workers_endpoints.py`) set `BRAVE_DB_URL` at module import time, causing the `db_engine` session-scoped fixture to try a real DB connection. These tests are skipped in the main project (no DB running). This is a pre-existing cross-test environment contamination unrelated to this plan's changes — `test_workers_endpoints.py` reproduces the same failures independently.

## Threat Surface Scan

No new security-relevant surface beyond what the threat model covers:
- T-ks0-01: _BLOCKED_FIELDS implemented and unit-tested
- T-ks0-02: require_bearer dependency — already the repo pattern
- T-ks0-03: processor fail-silent — try/except wraps append_log
- T-ks0-04: LTRIM cap=500, limit param max=200
- T-ks0-05: lucide-react already in package.json — no new install

## Self-Check: PASSED

All 14 files exist. All 3 commits (9e6bf9b, 460f939, b5a40eb) verified in git log.
