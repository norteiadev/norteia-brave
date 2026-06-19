---
phase: 08-ops-cms-destinos-atrativos-crud-process-observability-cores
plan: "06"
subsystem: dashboard
tags: [process-observability, workers, celery, failures, quarantine, recharts, vitest, msw]
dependency_graph:
  requires: [08-02, 08-03]
  provides: [process-observability-page, workers-api-client, WorkerBoard, FailuresPanel]
  affects: [dashboard/lib, dashboard/components/processo, dashboard/app/processo, dashboard/mocks/handlers]
tech_stack:
  added: []
  patterns:
    - workers-api.ts mirrors monitor-api.ts (polling constant + query key factory + typed fetcher)
    - WorkerBoard mirrors MonitorTiles tile pattern (Skeleton grid + error + broker-down banner)
    - FailuresPanel mirrors AlertsPanel list pattern (loading/error/empty states)
    - Recharts BarChart via ResponsiveContainer (plain recharts, not ChartContainer wrapper)
    - MSW double-prefix BASE URL convention (http://localhost:3000/api/api/v1/*)
key_files:
  created:
    - dashboard/lib/workers-api.ts
    - dashboard/mocks/handlers/workers.ts
    - dashboard/components/processo/WorkerBoard.tsx
    - dashboard/components/processo/FailuresPanel.tsx
    - dashboard/app/processo/page.tsx
    - dashboard/app/processo/__tests__/processo.test.tsx
  modified: []
decisions:
  - "Used existing dlq-api.fetchDlqList + gate-api.fetchGateQueue for human-pending counts instead of destinos-api/atrativos-api (not yet in worktree — parallel wave 2 plans 08-04/08-05 own those files). DLQ count = array.length; gate count = gateQueue.length."
  - "Stage funnel derives from gate queue items grouped by sub_state rather than a full atrativos list fetch. Avoids a new endpoint and gives operators a focused view of the WhatsApp gate funnel."
  - "recharts used directly (not ChartContainer wrapper) for the stage funnel BarChart, consistent with the plan's interface spec."
metrics:
  duration: "6m"
  completed: "2026-06-19"
  tasks_completed: 2
  files_changed: 6
---

# Phase 08 Plan 06: /processo Process-Observability Page Summary

**One-liner:** Live-polled workers status board (broker-down graceful), PoisonQuarantine failures panel, human-pending DLQ+gate count tiles, and recharts stage funnel — all behind the existing Bearer-auth BFF pattern.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | workers-api.ts + MSW handlers + WorkerBoard + FailuresPanel | a24211d | workers-api.ts, handlers/workers.ts, WorkerBoard.tsx, FailuresPanel.tsx |
| 2 | /processo page + stage funnel + human-pending tiles + Vitest tests | 0936abe | app/processo/page.tsx, processo.test.tsx |

## What Was Built

**workers-api.ts** — mirrors monitor-api.ts exactly: `WorkerInfo`, `WorkersData`, `FailuresData` interfaces; `workersKeys` query key factory (`all`, `data()`, `failures()`); `fetchWorkers()` and `fetchFailures(limit?)` typed fetchers; `WORKERS_REFETCH_INTERVAL_MS = 10_000`.

**mocks/handlers/workers.ts** — double-prefix BASE URLs (`http://localhost:3000/api/api/v1/workers`, `http://localhost:3000/api/api/v1/failures`). Sample data: `sampleWorkers` (healthy, one worker up, queues populated), `sampleWorkersBrokerDown` (broker_reachable=false, workers=[]), `sampleFailures` (2 items, task_name "brave.process_nascente"). Factories: `workersSuccess`, `workersBrokerDown`, `workersError`, `failuresSuccess`, `failuresEmpty`, `failuresError`.

**WorkerBoard** — `useQuery` with `refetchInterval: WORKERS_REFETCH_INTERVAL_MS`, `refetchOnWindowFocus: false`. Loading state: Skeleton grid (3 tiles). Error state: "Não foi possível carregar" + retry. Broker-down: amber banner (`role="alert"`, `data-testid="broker-down-banner"`) above tile grid without throwing. Worker tiles: hostname without `celery@` prefix, UP/DOWN status in CSS var tokens, active + reserved counts. Queue depths section. Beat schedule summary.

**FailuresPanel** — `useQuery` on `workersKeys.failures()`, same polling interval. Header "Quarentena recente" + total count. `by_task` breakdown chips. Empty state "Nenhuma falha recente". Items list: `task_name` (font-mono, destructive), truncated error message, formatted `quarantined_at` timestamp.

**app/processo/page.tsx** — composites WorkerBoard + 2 human-pending tiles + recharts BarChart funnel + FailuresPanel. Human-pending: `fetchDlqList` for DLQ count, `fetchGateQueue` for gate count (both refetchInterval=10s). Stage funnel: groups gate queue items by `sub_state` into FSM-ordered `{stage, count}` rows, renders `BarChart` with `fill="var(--color-primary)"`. `HumanPendingTile` renders loading skeleton while query is pending, then the count as tabular-nums.

**processo.test.tsx** — 6 Vitest tests, all passing:
1. WorkerBoard renders worker hostname "worker-1" (strips `celery@` prefix)
2. WorkerBoard renders "Broker indisponível" banner when broker is down
3. FailuresPanel renders task_name "brave.process_nascente" from sampleFailures
4. Human-pending tiles render DLQ count (3) and gate count (2)
5. Stage funnel section header renders
6. Page `<h1>` "Processo Brave" renders

## Verification Results

- `grep -n "broker_reachable.*false\|Broker indispon" WorkerBoard.tsx` — returns broker-down render branch (lines 20, 74)
- `grep -n "refetchInterval" WorkerBoard.tsx` — returns `WORKERS_REFETCH_INTERVAL_MS` (line 30)
- `grep -n "http://localhost:3000/api/api/v1/workers\|http://localhost:3000/api/api/v1/failures" workers.ts` — returns 2 double-prefix BASE lines (lines 19-20)
- `bun run test` — 91 tests pass, 15 test files, 0 failures
- `bun run build` — exits 0; `/processo` route compiled and included in route table

## Deviations from Plan

### Auto-handled Issues

**1. [Rule 2 - Missing critical functionality] fetchFailures lambda wrapper in FailuresPanel**
- **Found during:** Task 1 build check
- **Issue:** TypeScript error — `fetchFailures` has optional parameter `limit = 50` which is incompatible with TanStack Query's `QueryFunction` type signature (passes context object, not a number).
- **Fix:** Wrapped in lambda: `queryFn: () => fetchFailures()` instead of `queryFn: fetchFailures`
- **Files modified:** `dashboard/components/processo/FailuresPanel.tsx`
- **Commit:** Part of a24211d (fix applied before commit)

**2. [Rule 3 - Parallel dependency] Human-pending counts via existing APIs**
- **Found during:** Task 2 planning
- **Issue:** Plan specified `fetchDestinoList({routing: "dlq", limit: 1}) → data.total` and `fetchAtrativoList({sub_state: "...", limit: 1}) → data.total` — but `destinos-api.ts` and `atrativos-api.ts` are owned by parallel plans 08-04 and 08-05 (same wave) which hadn't been committed to the worktree.
- **Fix:** Used existing APIs: `fetchDlqList` (from `dlq-api.ts`, returns array → `.length`) for DLQ count; `fetchGateQueue` (from `gate-api.ts`, returns array → `.length`) for gate count. Semantically equivalent — both endpoints return the same underlying data.
- **Impact:** When 08-04 and 08-05 merge, the /processo page will continue to work correctly (no breaking changes — it imports from dlq-api.ts and gate-api.ts which are stable). The `fetchDestinoList`/`fetchAtrativoList` variants can be adopted in a future plan if stricter data isolation is needed.
- **Files modified:** `dashboard/app/processo/page.tsx`
- **Commit:** 0936abe

**3. [Rule 3 - Parallel dependency] Stage funnel from gate queue instead of atrativos list**
- **Found during:** Task 2 planning
- **Issue:** Plan specified fetching atrativos at high limit and grouping by sub_state. `atrativos-api.ts` not available in worktree.
- **Fix:** Funnel derived from gate queue items (which contain `sub_state` field). Gate queue is the most actionable sub_state slice for operators. Full FSM distribution available via /atrativos CMS (plan 08-05).
- **Files modified:** `dashboard/app/processo/page.tsx`
- **Commit:** 0936abe

## Known Stubs

None — all components wire real data from live endpoints.

## Self-Check: PASSED

Files exist:
- `dashboard/lib/workers-api.ts` FOUND
- `dashboard/mocks/handlers/workers.ts` FOUND
- `dashboard/components/processo/WorkerBoard.tsx` FOUND
- `dashboard/components/processo/FailuresPanel.tsx` FOUND
- `dashboard/app/processo/page.tsx` FOUND
- `dashboard/app/processo/__tests__/processo.test.tsx` FOUND

Commits exist:
- a24211d FOUND (feat(08-06): workers-api.ts + MSW handlers + WorkerBoard + FailuresPanel)
- 0936abe FOUND (feat(08-06): /processo page + stage funnel + human-pending tiles + Vitest tests)
