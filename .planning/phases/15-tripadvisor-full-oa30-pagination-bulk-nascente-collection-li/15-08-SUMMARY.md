---
phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
plan: 08
subsystem: dashboard
tags: [dashboard, tripadvisor, sweep, observability, msw, vitest]
requirements-completed: [TA-12]
requires:
  - "15-03 GET /api/v1/tripadvisor/sweep/progress (TASweepProgressResponse contract)"
provides:
  - "Live TripAdvisor national-sweep progress panel on /processo (10s poll)"
  - "dashboard/lib/ta-sweep-api.ts data layer (fetchTASweepProgress + taSweepKeys + TASweepProgress type)"
  - "dashboard/mocks/handlers/ta-sweep.ts MSW handler (double /api/api/ BFF prefix + 401 variant)"
affects:
  - "dashboard/app/processo/page.tsx (panel mounted beside EngineControl)"
tech-stack:
  added: []
  patterns:
    - "EngineControl mirror: 10s useQuery poll, apiFetch via BFF, data-testid discipline"
    - "MSW double /api/api/ BFF prefix (single /api/ 404s)"
    - "401-safe render: undefined data → idle fallback shell, no crash"
key-files:
  created:
    - dashboard/lib/ta-sweep-api.ts
    - dashboard/mocks/handlers/ta-sweep.ts
    - dashboard/components/engine/TASweepProgress.tsx
    - dashboard/components/engine/__tests__/TASweepProgress.test.tsx
  modified:
    - dashboard/app/processo/page.tsx
decisions:
  - "Pill content asserted via waitFor (the pill renders an immediate 'Parado' idle shell before the fetch resolves — findByTestId alone races the data load)"
  - "Reused ENGINE_REFETCH_INTERVAL_MS (10s) verbatim — no faster poll (RESEARCH anti-pattern T-15-08-02)"
metrics:
  tasks: 2
  files: 5
  commits: 2
  completed: 2026-06-26
---

# Phase 15 Plan 08: Live TripAdvisor Sweep-Progress Panel Summary

A real-time `/processo` panel mirrors EngineControl exactly — polling `GET /api/v1/tripadvisor/sweep/progress` every 10s through the BFF and rendering a pages/334 progress bar, attractions ingested, current offset, error count, and a terminal-state pill (running / done / stopped_needs_bootstrap / idle), 401-safe and fully offline-tested with MSW + Vitest.

## What Was Built

### Task 1 — data layer + MSW handler (commit f4b7653)
- `dashboard/lib/ta-sweep-api.ts`: `TASweepProgress` interface matching the 15-03 `TASweepProgressResponse` contract exactly (`state`, `pages_done`, `pages_total`, `attractions_ingested`, `current_offset`, `error_count`, `started_at?`), `taSweepKeys`, and `fetchTASweepProgress()` calling `apiFetch<TASweepProgress>("api/v1/tripadvisor/sweep/progress")` (bare FastAPI path — `bff()` adds the prefix). Re-exports `ENGINE_REFETCH_INTERVAL_MS` for the 10s cadence.
- `dashboard/mocks/handlers/ta-sweep.ts`: `taSweepProgress(overrides)` factory at the mandatory double `/api/api/v1/tripadvisor/sweep/progress` BFF prefix returning a default running snapshot (5/334), plus a `taSweepUnauthorized()` 401 variant.

### Task 2 — panel + test + mount (commit a4b7993)
- `dashboard/components/engine/TASweepProgress.tsx` ("use client"): `useQuery({ queryKey: taSweepKeys.status, queryFn: fetchTASweepProgress, refetchInterval: ENGINE_REFETCH_INTERVAL_MS, refetchOnWindowFocus: false })`. Mirrors EngineControl's progress-bar markup (swapped `ufs_*` → `pages_*`), `CountTile` (attractions / offset / errors), and the status-pill pattern driven by `state`. PT-BR labels, CSS utility color tokens (no hex), `data-testid` on every readable element.
- `dashboard/components/engine/__tests__/TASweepProgress.test.tsx`: 6 Vitest + MSW tests — 5/334 bar + computed %, count tiles, each terminal-state pill, and a 401-safe render (idle shell, no counts/bar, no crash).
- `dashboard/app/processo/page.tsx`: `<TASweepProgress />` mounted immediately after `<EngineControl />`.

## Verification

`cd dashboard && bun run test TASweepProgress` → 6 passed (1 file). Offline (MSW), no backend.

Note: `node_modules` was absent in the worktree, so dependencies were restored from the committed `bun.lock` via `bun install --frozen-lockfile` (frozen restore of the already-pinned set — no new packages added, consistent with threat-register T-15-SC `accept`).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Pill content assertions raced the data load**
- **Found during:** Task 2 (first test run — 3 of 6 failed)
- **Issue:** The terminal-state pill always renders (it shows an immediate "Parado" idle shell before the query resolves). `findByTestId(...)` resolved against that initial idle render, so `toHaveTextContent("Concluído" / "Precisa bootstrap")` saw "Parado".
- **Fix:** Wrapped the pill-content assertions in `waitFor(...)` (the established EngineControl-test pattern for the async-updating state line). No component change needed.
- **Files modified:** dashboard/components/engine/__tests__/TASweepProgress.test.tsx
- **Commit:** a4b7993

## Threat Surface

No new security surface beyond the plan's `<threat_model>`. The panel is read-only, routes through the BFF (operator Bearer validated before forward), renders no cookie/session/proxy data, and reuses the 10s poll cadence (no faster polling).

## Self-Check: PASSED

All created files present; both task commits (f4b7653, a4b7993) in history; `<TASweepProgress />` mounted on /processo.
