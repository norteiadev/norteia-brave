---
phase: 08-ops-cms-destinos-atrativos-crud-process-observability-cores
plan: "05"
subsystem: dashboard
tags: [atrativos, cms, frontend, tanstack-table, msw, vitest, pii]
dependency_graph:
  requires: [08-01, 08-03, 08-04]
  provides: [D-04-atrativos-crud-frontend]
  affects: [dashboard/app/atrativos, dashboard/lib/atrativos-api]
tech_stack:
  added: []
  patterns:
    - TanStack Table v8 master-detail with sub_state + score StageBadge columns
    - FSM-guided advance mutation (discovered→contacts_found→signals_gathered→aguardando_consulta_whatsapp)
    - MSW double-prefix BASE pattern (http://localhost:3000/api/api/v1/atrativos)
    - DetailPanel<T> reuse with entityType="attraction" (no modifications)
key_files:
  created:
    - dashboard/lib/atrativos-api.ts
    - dashboard/mocks/handlers/atrativos.ts
    - dashboard/components/cms/AtrativoList.tsx
    - dashboard/app/atrativos/page.tsx
    - "dashboard/app/atrativos/[id]/page.tsx"
    - dashboard/components/cms/__tests__/AtrativoList.test.tsx
  modified: []
decisions:
  - D-04 atrativos CMS reuses DetailPanel<T> with entityType="attraction" without modification — panel handles JourneyStepper step set switch internally
  - FSM advance next_state computed inline in AtrativoActions via progression map (discovered→contacts_found→signals_gathered→aguardando_consulta_whatsapp); terminal states (aguardando_consulta_whatsapp, whatsapp_in_progress) show no advance button
  - 409 conflict from advance handled in explainError → toast.error to prompt user to reload; no silent retry
  - phone_masked only in all interfaces and sample data (never phone_e164); enforced via Vitest test 3 and T-08-14 mitigations
metrics:
  duration: "~30 min"
  completed: "2026-06-19"
  tasks: 2
  files: 6
---

# Phase 08 Plan 05: Atrativos CMS Frontend Summary

Atrativos CMS frontend with typed API client, TanStack Table v8 list component, master-detail page with FSM-guided advance actions, full-detail page with parent destino link, MSW handlers (phone_masked only), and 3 Vitest tests — all with PII contract enforced.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | atrativos-api.ts + MSW handlers + AtrativoList test scaffold | 5775c5a | dashboard/lib/atrativos-api.ts, dashboard/mocks/handlers/atrativos.ts, dashboard/components/cms/__tests__/AtrativoList.test.tsx |
| 2 | AtrativoList component + /atrativos pages + Vitest tests | cf382a2 | dashboard/components/cms/AtrativoList.tsx, dashboard/app/atrativos/page.tsx, dashboard/app/atrativos/[id]/page.tsx |

## What Was Built

**atrativos-api.ts** — Typed API client mirroring destinos-api.ts. Defines `AtrativoListItem` (with `phone_masked` in `contacts_summary`, never `phone_e164`), `AtrativoDetail`, `AuditLogRow`. Exports `atrativoKeys`, `fetchAtrativoList`, `fetchAtrativoDetail`, `advanceAtrativo` (PATCH with JSON body `{expected_state, next_state}`), and `descartarAtrativo`.

**MSW handlers (atrativos.ts)** — Double-prefix BASE per Pitfall 5. Sample data uses `phone_masked: "**1234"` only. Handler factories: `atrativosListSuccess` (with sub_state + uf + parent_mar_id filter logic), `atrativosListEmpty`, `atrativosListError`, `atrativoDetailSuccess`, `atrativoAdvanceSuccess` (200 `{status: "ok", sub_state: "contacts_found"}`), `atrativoDescarteSuccess` (200 `{status: "ok", routing: "dlq"}`). Default `atrativoHandlers` barrel.

**AtrativoList.tsx** — TanStack Table v8 with columns: name, uf, sub_state (`<StageBadge subState=...>`), score (`<StageBadge score=...>`), routing (`<StageBadge routing=...>`), validation_pending chip. Filter bar: UF select, sub_state select (Todos/Descoberto/Contatos/Sinais/Aguardando WA/Em outreach), parent_mar_id text input. Loading/empty/error/401 states.

**/atrativos/page.tsx** — Master-detail layout. AtrativoActions injects advance mutation (FSM-guided next_state map) + descartar (AlertDialog confirm). 409 conflict toast: "Estado já avançado — recarregue a página". invalidateQueries on atrativoKeys.all after mutation.

**/atrativos/[id]/page.tsx** — Full-detail with DetailPanel entityType="attraction", parent_destino link (`/destinos/{parent_mar_id}`), back link to /atrativos.

## Verification Results

```
grep phone_e164 in all atrativo files → 0 data definitions (only in comments)
grep double-prefix BASE → confirmed "http://localhost:3000/api/api/v1/atrativos"
bun run test → 98 tests pass (3 new AtrativoList + 95 existing)
tsc --noEmit → 0 errors
```

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all data flows are wired to real MSW handlers and typed API client functions.

## Threat Flags

No new threat surface beyond what the plan's threat model covers. The T-08-14 (phone_e164 disclosure) and T-08-15 (advance tampering) mitigations are fully implemented:
- T-08-14: `phone_masked` only in all interfaces, sample data, and rendering; Vitest test 3 asserts no `phone_e164` in DOM
- T-08-15: `expected_state` passed from `detail.sub_state`; `next_state` constrained by FSM progression map; 409 handled with toast

## Self-Check: PASSED

- [x] dashboard/lib/atrativos-api.ts — created
- [x] dashboard/mocks/handlers/atrativos.ts — created
- [x] dashboard/components/cms/AtrativoList.tsx — created
- [x] dashboard/app/atrativos/page.tsx — created
- [x] dashboard/app/atrativos/[id]/page.tsx — created
- [x] dashboard/components/cms/__tests__/AtrativoList.test.tsx — created
- [x] Commit 5775c5a — exists
- [x] Commit cf382a2 — exists
- [x] 98 tests pass (3 new AtrativoList)
- [x] TypeScript: 0 errors
