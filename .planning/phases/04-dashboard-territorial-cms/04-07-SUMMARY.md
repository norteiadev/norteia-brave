---
phase: 04-dashboard-territorial-cms
plan: 07
subsystem: dashboard-cost
tags: [dash-04, cost, llm, usd, tokens, recharts, tanstack-query, msw, vitest, tdd, d-01, d-04, d-05]

# Dependency graph
requires:
  - phase: 04-dashboard-territorial-cms
    plan: 02
    provides: "providers (TanStack Query), lib/api-client (BFF apiFetch + ApiError), MSW+Vitest harness, shadcn new-york preset"
  - phase: 04-dashboard-territorial-cms
    plan: 05
    provides: "read-only Bearer-guarded dashboard.py router (require_bearer); shadcn chart wrapper + recharts; useMonitor/ThroughputChart slice patterns mirrored here"
  - phase: 01-brave-core
    plan: "*"
    provides: "LLMGeneration model (lane, model_slug, prompt/completion_tokens, usd_cost, created_at); metrics.py group_by idiom"
provides:
  - "GET /api/v1/cost?group_by=lane|model&since= — spend/tokens/count aggregated over llm_generations (DASH-04 backend)"
  - "Cost & LLM view (/cost): CostSummary + CostByLaneChart + CostByModelChart with a window selector"
  - "useCost — shared TanStack query hook keyed by (groupBy, window)"
  - "lib/cost-api — cost query keys + typed BFF fetcher + USD/token total helpers; mocks/handlers/cost — full-view-state MSW handlers"
affects:
  - "later funnels/conversations slices reuse the same window-selector + chart/summary patterns"
  - "the app nav shell (later) links to /cost"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Straight GROUP BY over llm_generations: col = lane if group_by=='lane' else model_slug; func.sum(usd_cost) + func.sum(prompt+completion) + func.count(id); optional created_at >= since"
    - "Single shared CostBarChart body wrapped by CostByLaneChart/CostByModelChart (pin groupBy + testId) — one component, two dimensions"
    - "group_by-aware MSW handler: one http.get on the path switches lane vs model payload off the query param (path matcher ignores query)"
    - "USD/tokens render in Geist Mono tabular-nums (UI-SPEC: USD/tokens are monospace data); total-spend Display readout assertable independent of the jsdom-unmeasured SVG"
    - "Window selector (24h/7d/30d/Tudo) → relative since computed at fetch time; 'Tudo' sends no since (all-time)"

key-files:
  created:
    - dashboard/lib/cost-api.ts
    - dashboard/components/cost/useCost.ts
    - dashboard/components/cost/CostBarChart.tsx
    - dashboard/components/cost/CostByLaneChart.tsx
    - dashboard/components/cost/CostByModelChart.tsx
    - dashboard/components/cost/CostSummary.tsx
    - dashboard/components/cost/__tests__/CostByLaneChart.test.tsx
    - dashboard/components/cost/__tests__/CostSummary.test.tsx
    - dashboard/app/cost/page.tsx
    - dashboard/mocks/handlers/cost.ts
  modified:
    - brave/api/routers/dashboard.py  # get_cost added (file pre-existed from plans 03/05)
    - tests/integration/test_dashboard_endpoints.py

decisions:
  - "D-01: get_cost is a read-only GROUP BY on the existing Bearer-guarded dashboard.py — no pipeline logic, no writes; 401 fires before any DB work"
  - "group_by accepts 'lane' (→ LLMGeneration.lane) or anything-else (→ model_slug); the UI only sends 'lane'/'model'. since is an optional ISO timestamp on created_at; empty window → rows == []"
  - "D-04/D-05: useCost (TanStack useQuery, no polling — cost is a historical aggregate) feeds CostByLaneChart/CostByModelChart/CostSummary; Recharts bars via the shadcn chart wrapper (reused from plan 05)"
  - "Two named chart files per the plan share one CostBarChart body — avoids duplicating the four view-states (loading/empty/401/error) twice"
  - "CostSummary reads group_by=lane and sums across rows (totals are dimension-independent, so one fetch suffices)"

requirements-completed: [DASH-04]

# Metrics
duration: ~15min
completed: 2026-06-16
---

# Phase 4 Plan 07: Cost & LLM View (DASH-04) Summary

**The Cost & LLM slice end-to-end: a read-only Bearer-guarded `GET /api/v1/cost?group_by=lane|model&since=` that aggregates `llm_generations` (`func.sum(usd_cost)` + token sums + call count grouped by `lane` or `model_slug`, optionally windowed on `created_at`), rendered as a `/cost` view — a USD/tokens/calls summary in Geist Mono tabular-nums above spend-per-lane and spend-per-model Recharts bars, driven by a 24h/7d/30d/Tudo window selector through one shared `useCost` hook — proven offline with 5 pytest + 10 Vitest/MSW tests.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2 (Task 1 `tdd`, Task 2 `auto`)
- **Files created:** 10 · modified: 2

## Accomplishments

### Task 1 — GET /api/v1/cost (TDD: RED `1a44f8b` → GREEN `6051ebf`)
- **RED:** wrote 5 failing tests in `test_dashboard_endpoints.py` (no-Bearer 401 before DB; `group_by=lane` row shape `{key, usd_cost, tokens, count}`; `group_by=model` groups by `model_slug`; `since` restricts the window; future-`since` empty window → `rows == []`). Confirmed RED (404 — endpoint absent).
- **GREEN:** added `get_cost` to the existing `dashboard.py` router (Bearer-guarded, read-only D-01):
  - `col = LLMGeneration.lane if group_by == "lane" else LLMGeneration.model_slug`;
  - `select(col, func.sum(usd_cost), func.sum(prompt_tokens + completion_tokens), func.count(id)).group_by(col)` with an optional `created_at >= since` filter;
  - returns `{"group_by": group_by, "rows": [{"key", "usd_cost": float(cost or 0), "tokens": int(tok or 0), "count": int(n)}]}`.
- All 5 cost tests pass; the full 34-test dashboard file stays green. `ruff` clean.

### Task 2 — Cost & LLM view: summary + charts (commit `3f13f7b`)
- **`lib/cost-api.ts`** — `CostData`/`CostRow` types, `costKeys`, `fetchCost(groupBy, windowHours)` (resolves the relative window into a `since` ISO at fetch time; "Tudo" sends none), and `totalUsd/totalTokens/totalCalls/formatUsd` helpers.
- **`useCost`** — shared `useQuery` keyed by `(groupBy, windowHours)`; no polling (cost is a historical aggregate, refetched on the controls).
- **`CostBarChart`** — the shared body: a Recharts USD-per-group bar via the shadcn `chart` wrapper (primary-blue series, the one large-fill use UI-SPEC allows) with the total-spend `formatUsd` headline in Geist Mono tabular-nums, plus the four view-states. `CostByLaneChart` / `CostByModelChart` are thin wrappers pinning `groupBy` + the test id.
- **`CostSummary`** — total USD / tokens / calls, USD + tokens in Geist Mono tabular-nums (UI-SPEC monospace-data rule); same four view-states.
- **`/cost` page** — window selector (24h/7d/30d/Tudo) drives all three surfaces; summary above a two-column lane/model chart grid.
- **`mocks/handlers/cost.ts`** — a group_by-aware success handler (one `http.get` switches lane vs model payload off the query param) + empty/error/401 factories.

## Verification

- `pytest tests/integration/test_dashboard_endpoints.py -k cost` → **5 passed** (29 deselected); full dashboard file → **34 passed**.
- `cd dashboard && bunx vitest run components/cost` → **2 files, 10 tests passed** (CostByLaneChart 5, CostSummary 5 — success/empty/error/401 each).
- `cd dashboard && bunx vitest run` (full suite) → **11 files, 63 tests passed** (no regression to the 53 prior tests).
- `cd dashboard && bunx tsc --noEmit` → **clean (exit 0)**.
- `cd dashboard && bunx next build` → all routes compiled, including `/cost` (static).
- Acceptance greps: `def get_cost(` + `func.sum` present in `dashboard.py`; `useQuery` reaches `/api/v1/cost` via `useCost`; `CostSummary` renders total USD in `font-mono`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] `since` query param URL-encoding in my own RED tests**
- **Found during:** Task 1 (GREEN run — two `since` tests returned 422).
- **Issue:** the RED tests passed `since` via a raw f-string query (`?since={iso}`); the `+00:00` offset in the ISO timestamp was decoded as a space, corrupting the datetime → FastAPI 422. This was a test-construction bug, not an endpoint bug.
- **Fix:** switched those two tests to `client.get(path, params={...})` so the timestamp is properly URL-encoded. The endpoint itself parses a valid ISO `since` correctly.
- **Files:** `tests/integration/test_dashboard_endpoints.py`.
- **Commit:** `6051ebf` (committed with GREEN).

### Adjustments (not deviations)

- **One shared `CostBarChart` body** behind the two plan-named chart files (`CostByLaneChart`, `CostByModelChart`) — keeps the four view-states (loading/empty/401/error) in one place instead of duplicating them. Both required files exist and are independently importable/testable.
- **Charts are bar charts** (mirroring plan 05's `ThroughputChart`) rather than pie; the plan offered "stacked bar / pie" — bars read better for USD magnitude comparison and reuse the existing shadcn chart wrapper without a new block (no shadcn `add`, honoring the no-new-deps threat note).

All else executed as written.

## Threat Model Compliance

- **T-04-21 (Spoofing / get_cost):** `dependencies=[Depends(require_bearer)]` — the no-Bearer 401 fires before any DB work, proven by `test_cost_no_bearer_returns_401` (offline, no DB).
- **T-04-22 (Information Disclosure):** accepted — the endpoint returns aggregate USD/token sums grouped by lane/model only; no per-record content, no PII, no secrets.
- **T-04-23 (Tampering / read endpoint):** accepted — read-only `select … group_by`; no writes, no pipeline mutation.
- **T-04-SC (Tampering / deps):** accepted and held — **no new packages** added (backend or frontend); the charts reuse the existing recharts + shadcn chart wrapper from plan 05.

## Known Stubs

None — the cost view is fully wired to the real `GET /api/v1/cost` aggregate (USD/token/count sums over `llm_generations`) through the BFF. The MSW handlers are test-only and never imported by app code. The `/cost` route is complete and reachable but not yet linked from a global nav shell (that shell lands in a later slice).

## Self-Check: PASSED

All 10 created files exist (cost-api lib + useCost + 4 components + 2 test files + page + MSW handler); the endpoint addition + both task commits plus the RED commit (`1a44f8b` test, `6051ebf` feat, `3f13f7b` feat) present in git history; backend cost suite (5) + full dashboard file (34) + frontend cost suite (10) + full frontend suite (63) + tsc + next build all green.

---
*Phase: 04-dashboard-territorial-cms*
*Completed: 2026-06-16*
