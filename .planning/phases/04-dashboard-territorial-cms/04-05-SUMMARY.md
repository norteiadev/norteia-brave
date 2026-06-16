---
phase: 04-dashboard-territorial-cms
plan: 05
subsystem: dashboard-monitor
tags: [dash-02, monitor, audit-rates, throughput, alerts, recharts, tanstack-query, polling, msw, vitest, tdd, d-01, d-04]

# Dependency graph
requires:
  - phase: 04-dashboard-territorial-cms
    plan: 02
    provides: "providers (TanStack Query), lib/api-client (BFF apiFetch + ApiError), MSW+Vitest harness, shadcn new-york preset"
  - phase: 04-dashboard-territorial-cms
    plan: 03
    provides: "read-only Bearer-guarded dashboard.py router (require_bearer, get_dlq_detail) extended here"
  - phase: 01-brave-core
    plan: "*"
    provides: "AuditLog / RioRecord / PoisonQuarantine / MarRecord models; metrics.py per-layer count idiom"
  - phase: 03
    plan: "*"
    provides: "wa:quality_red Redis flag + is_quality_red() reader (compliance.quality_rating)"
provides:
  - "GET /api/v1/monitor — volume + AuditLog-derived rates + throughput + alerts (DASH-02 backend)"
  - "Brave monitor view (/monitor): MonitorTiles + ThroughputChart + AlertsPanel, live-polled"
  - "useMonitor — shared TanStack query hook (one poll feeds all monitor surfaces)"
  - "lib/monitor-api — monitor query key + typed BFF fetcher; mocks/handlers/monitor — full-view-state MSW handlers"
  - "shadcn chart wrapper (components/ui/chart.tsx) + recharts 3.8.0 (reusable by cost/funnel charts)"
affects:
  - "later cost/funnel slices reuse the shadcn chart wrapper + the rate/throughput patterns"
  - "the app nav shell (later) links to /monitor"

# Tech tracking
tech-stack:
  added:
    - "recharts 3.8.0 (monitor throughput bar chart; via official shadcn chart block)"
    - "shadcn chart primitive (components/ui/chart.tsx — ChartContainer/ChartTooltip/ChartTooltipContent)"
  patterns:
    - "AuditLog action group_by over a created_at window → approval/rejection/DLQ proportions = the DASH-02 audit coverage (folded into rates, not a separate raw audit feed)"
    - "Single shared useMonitor hook (monitorKeys.data(sinceHours)) → one network poll dedupes across tiles/chart/alerts"
    - "TanStack refetchInterval (10s) for liveness; no WebSocket this milestone (CONTEXT deferred)"
    - "Recharts SVG has no measured size in jsdom → assert on a deterministic Display-size readout the component also renders, not on internal SVG geometry"
    - "Status colors (green/amber/red) are data-encoding on small numerals/captions only; destructive red reserved for the failure-alert tile (UI-SPEC)"

key-files:
  created:
    - brave/api/routers/dashboard.py  # get_monitor added (file pre-existed from plan 03)
    - dashboard/lib/monitor-api.ts
    - dashboard/mocks/handlers/monitor.ts
    - dashboard/components/monitor/useMonitor.ts
    - dashboard/components/monitor/MonitorTiles.tsx
    - dashboard/components/monitor/ThroughputChart.tsx
    - dashboard/components/monitor/AlertsPanel.tsx
    - dashboard/components/monitor/__tests__/MonitorTiles.test.tsx
    - dashboard/components/monitor/__tests__/ThroughputChart.test.tsx
    - dashboard/app/monitor/page.tsx
    - dashboard/components/ui/chart.tsx
  modified:
    - brave/api/routers/dashboard.py
    - tests/integration/test_dashboard_endpoints.py
    - dashboard/package.json
    - dashboard/bun.lock

decisions:
  - "D-01: get_monitor is a read-only aggregation on the existing Bearer-guarded dashboard.py — no pipeline logic, no writes; 401 fires before any DB work"
  - "DASH-02 audit element folded into AuditLog-derived rates (dlq_validated/dlq_rejected/dlq_reprocessed proportions over the window) per the plan INFO note — NOT a separate raw audit-feed surface"
  - "D-04: liveness via TanStack refetchInterval (10s) through a single shared useMonitor hook; WebSocket deferred"
  - "ThroughputChart asserts on a Display-size throughput readout (jsdom can't measure the Recharts SVG); chart still renders the per-routing series for the real UI"
  - "lucide-react re-pinned to 0.544.0 after the shadcn CLI silently floated it to ^1.20.0 (kept the package boundary tight; only recharts was intentionally added)"

requirements-completed: [DASH-02]

# Metrics
duration: ~20min
completed: 2026-06-16
---

# Phase 4 Plan 05: Brave Monitor (DASH-02) Summary

**The Brave monitor end-to-end: a read-only Bearer-guarded `GET /api/v1/monitor` aggregating per-layer volume + AuditLog-derived approval/rejection/DLQ rates (the DASH-02 audit coverage) + windowed throughput + failure alerts (PoisonQuarantine count, RED WhatsApp quality flag), rendered as a live-polling `/monitor` view — Display-size volume tiles with rate captions, a Recharts throughput chart, and a destructive failure-alerts panel — all driven by one shared 10s TanStack poll and proven offline with 6 pytest + 13 Vitest/MSW tests.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 2 (Task 1 `tdd`, Task 2 `auto`)
- **Files created:** 11 · modified: 4

## Accomplishments

### Task 1 — GET /api/v1/monitor (TDD: RED `61f57ee` → GREEN `3814792`)
- **RED:** wrote 6 failing tests in `test_dashboard_endpoints.py` (no-Bearer 401 before DB; volume/rates/throughput/alerts shape; empty-window pre-seeds zeros; rates derive from AuditLog action counts; `alerts.failures` ← PoisonQuarantine; `alerts.quality` ← RED flag). Confirmed RED (404 — endpoint absent).
- **GREEN:** added `get_monitor` to the existing `dashboard.py` router (Bearer-guarded, read-only D-01):
  - **volume** — per-layer counts mirroring `metrics.py` (`nascente_count`, `rio_count` grouped by routing pre-seeded to 0, `mar_count`).
  - **rates** — `AuditLog.action` group_by over a `created_at` window (default 24h, capped 30d) for `dlq_validated`/`dlq_rejected`/`dlq_reprocessed`, computed as proportions in `[0,1]`, pre-seeded to `0.0`. **This is the DASH-02 audit coverage** — folded into rates, not a separate feed (plan INFO).
  - **throughput** — `func.count(RioRecord.id)` over `processed_at >= window_start`.
  - **alerts** — `{failures: PoisonQuarantine count, quality: is_quality_red(redis)}` via the existing compliance reader through `Depends(get_redis)`.
- All 6 monitor tests pass; the full 29-test dashboard file stays green. `ruff` clean.

### Task 2 — Monitor view: tiles + Recharts + polling (commit `0e09bdf`)
- Added the official shadcn **chart** block (pulled **recharts 3.8.0**); re-pinned `lucide-react` back to `0.544.0` after the CLI floated it.
- **`useMonitor`** — one shared `useQuery` (`monitorKeys.data(sinceHours)`, `refetchInterval: 10s`) so tiles/chart/alerts dedupe onto a single network poll (D-04).
- **`MonitorTiles`** — per-layer volume as 28px Display tabular numerals + the AuditLog-derived approval/rejection/reprocess rate captions; status colors as small data-encoding tints (off the accent budget). Skeleton / "Sem dados no período" / error+retry / 401 states.
- **`ThroughputChart`** — Recharts bar of the per-routing distribution via the shadcn `chart` wrapper (primary-blue series) plus a Display-size windowed throughput readout. Same four view states + the "Ajuste a janela de tempo…" empty copy.
- **`AlertsPanel`** — turns **destructive** (red border/bg, `role="alert"`) when `failures > 0` OR `quality` RED, surfacing the poison count and the "Qualidade WhatsApp RED — envios pausados" line; calm "Sem falhas no período" otherwise (UI-SPEC reserves destructive for failure alerts).
- **`/monitor` page** wires the three; `lib/monitor-api.ts` (key + typed BFF fetcher) and `mocks/handlers/monitor.ts` (success/empty/error/401 + alerting/empty payloads).

## Verification

- `pytest tests/integration/test_dashboard_endpoints.py -k monitor` → **6 passed**; full file → **29 passed**.
- `cd dashboard && bunx vitest run components/monitor` → **2 files, 13 tests passed** (MonitorTiles 8 incl. AlertsPanel cases, ThroughputChart 5 — success/empty/error/401 each).
- `cd dashboard && bunx vitest run` (full suite) → **7 files, 39 tests passed** (no regression to the 26 prior DLQ/login/BFF tests).
- `cd dashboard && bunx tsc --noEmit` → **clean (exit 0)**.
- `cd dashboard && bunx next build` → all 6 routes compiled, including `/monitor` (static).
- Acceptance greps: `def get_monitor(` + `PoisonQuarantine` + `AuditLog` present in `dashboard.py`; `refetchInterval` present in `components/monitor` (`useMonitor.ts`); AlertsPanel renders the destructive tile.

## Deviations from Plan

### Auto-fixed / minor adjustments

**1. [Rule 3 — Blocking] shadcn CLI silently floated `lucide-react` to `^1.20.0`**
- **Found during:** Task 2 (post-`shadcn add chart` package.json diff review).
- **Issue:** `npx shadcn add chart` (run for the official chart block) rewrote the pinned `lucide-react@0.544.0` to `^1.20.0` — an unrequested major-version float outside the plan's package boundary (threat model: only recharts intended).
- **Fix:** re-pinned `lucide-react` to its original exact `0.544.0` in `package.json`, re-ran `bun install` to restore the lockfile, re-verified tsc + tests. Only **recharts 3.8.0** + the shadcn chart wrapper were intentionally added.
- **Files:** `dashboard/package.json`, `dashboard/bun.lock`.

**2. [Adjustment] ThroughputChart asserts on a Display-size readout, not the SVG**
- **Reason:** Recharts' `ResponsiveContainer` has zero measured dimensions under jsdom, so internal chart geometry isn't reliably assertable offline. The component renders the windowed `throughput` as a prominent Display numeral (real UI value), and the test asserts on that + the per-layer series data — keeping the offline test deterministic without weakening the rendered chart.

All else executed as written.

## Threat Model Compliance

- **T-04-15 (Spoofing / get_monitor):** `dependencies=[Depends(require_bearer)]` — the no-Bearer 401 fires before any DB work, proven by `test_monitor_no_bearer_returns_401` (offline, no DB).
- **T-04-16 (Information Disclosure):** accepted — the endpoint returns aggregate counts/rates/throughput/alert-counts only; no PII, no record-level rows.
- **T-04-17 (DoS / polling):** accepted — single internal operator, 10s interval, no external exposure; all aggregate queries are window-bounded.
- **T-04-SC (Tampering / deps):** only **recharts** (+ its shadcn chart wrapper) added intentionally; the CLI's stray `lucide-react` major float was reverted and re-pinned, keeping the dependency boundary tight. No third-party registries.

## Known Stubs

None — the monitor is fully wired to the real `GET /api/v1/monitor` aggregate (volume from the medallion tables, rates from `AuditLog`, throughput from `RioRecord.processed_at`, alerts from `PoisonQuarantine` + the `wa:quality_red` Redis flag) through the BFF. The MSW handlers are test-only and never imported by app code. The `/monitor` route is complete and reachable but not yet linked from a global nav shell (that shell lands in a later slice).

## Self-Check: PASSED

All 11 created files exist (endpoint + 10 frontend slice files); both task commits plus the RED commit (`61f57ee` test, `3814792` feat, `0e09bdf` feat) present in git history; backend monitor suite (6) + full dashboard file (29) + frontend monitor suite (13) + full frontend suite (39) + tsc + next build all green.

---
*Phase: 04-dashboard-territorial-cms*
*Completed: 2026-06-16*
