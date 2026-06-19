---
phase: 08-ops-cms-destinos-atrativos-crud-process-observability-cores
plan: "04"
subsystem: dashboard-cms
tags: [destinos, cms, tanstack-table, msw, vitest, detailpanel, fastapi-routers]
dependency_graph:
  requires:
    - 08-01  # brand tokens + globals.css
    - 08-02  # cms.py + workers.py routers (Wave 1)
    - 08-03  # StageBadge + JourneyStepper components (Wave 1)
  provides:
    - main.py cms_router + workers_router registration
    - destinos-api.ts typed API client
    - DestinoList.tsx TanStack Table v8 component
    - DetailPanel.tsx generic action-agnostic detail panel
    - /destinos master-detail page + /destinos/[id] full-detail page
    - MSW handler factories for destinos endpoints
    - Vitest test suite (4 tests)
  affects:
    - 08-05  # atrativos page reuses DetailPanel
tech_stack:
  added: []
  patterns:
    - TanStack Table v8 (ColumnDef, useReactTable, getCoreRowModel, flexRender)
    - TanStack Query useMutation with invalidateQueries on destinoKeys.all
    - MSW double-prefix BASE URL pattern (Pitfall 5)
    - Generic DetailPanel<T extends RecordBase> with injected fetchDetail + actions render-prop
    - AlertDialog confirm for destructive Descartar mutation
key_files:
  created:
    - brave/api/main.py  # modified: cms_router + workers_router registered
    - dashboard/lib/destinos-api.ts
    - dashboard/mocks/handlers/destinos.ts
    - dashboard/components/cms/DestinoList.tsx
    - dashboard/components/cms/DetailPanel.tsx
    - dashboard/app/destinos/page.tsx
    - "dashboard/app/destinos/[id]/page.tsx"
    - dashboard/components/cms/__tests__/test-utils.tsx
    - dashboard/components/cms/__tests__/DestinoList.test.tsx
  modified: []
decisions:
  - "DetailPanel<T> uses generics bounded by RecordBase interface — accepts DestinoDetail or AtrativoDetail without type narrowing at call sites"
  - "DestinoActions placed inline in destinos/page.tsx (not a separate module) — same pattern as DlqActions in dlq/page.tsx"
  - "/destinos/[id] page is read-only (no actions) — deep-link for audit inspection; actions are on the master-detail /destinos page"
metrics:
  duration: "~7 min"
  completed: "2026-06-19"
  tasks: 2
  files: 9
---

# Phase 08 Plan 04: Destinos CMS — Router Registration + Frontend Summary

Registered cms_router + workers_router in main.py; built destinos API client, DestinoList (TanStack Table v8), generic action-agnostic DetailPanel (reused by atrativos in 08-05), /destinos master-detail page with promote/reprocess/descarte mutations, /destinos/[id] full-detail deep-link, MSW handler factories (double-prefix Pitfall 5), and 4 Vitest tests.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Register routers + destinos API client + MSW handlers | ce522c0 | brave/api/main.py, dashboard/lib/destinos-api.ts, dashboard/mocks/handlers/destinos.ts |
| 2 | DestinoList + DetailPanel + /destinos pages + Vitest tests | 6be256a | DestinoList.tsx, DetailPanel.tsx, app/destinos/page.tsx, app/destinos/[id]/page.tsx, DestinoList.test.tsx |

## Verification Results

- `from brave.api.main import app` succeeds; routes include `/api/v1/destinos`, `/api/v1/atrativos`, `/api/v1/workers`, `/api/v1/failures`
- `grep cms_router brave/api/main.py` returns 2 lines (import + include_router)
- `grep "http://localhost:3000/api/api/v1/destinos" dashboard/mocks/handlers/destinos.ts` returns BASE constant (Pitfall 5 double-prefix)
- Vitest suite: 89/89 tests passing (4 new DestinoList tests + 85 existing, zero regressions)

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all data in DestinoList and DetailPanel flows from server via TanStack Query; MSW fixtures are intentional offline test data, not stubs.

## Threat Flags

No new threat surface beyond the plan's threat model. cms_router and workers_router PATCH endpoints are guarded by `require_steward_or_bearer`; read endpoints by `require_bearer`. All PATCH actions write audit log via `write_audit`. No PII exposed in destinos endpoints (T-08-13: normalized contains only geographic/name data, no phone_e164).

## Self-Check: PASSED
