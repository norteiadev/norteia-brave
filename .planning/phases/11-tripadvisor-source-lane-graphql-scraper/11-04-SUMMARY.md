---
phase: 11-tripadvisor-source-lane-graphql-scraper
plan: "04"
subsystem: dashboard-mar-ready-ui
tags: [dashboard, tripadvisor, mar-ready, engine-source, msw, vitest, tdd]
dependency-graph:
  requires:
    - "11-03" (atrativos API endpoints: GET /mar-ready + PATCH promote + POST promote-batch + engine /start source field)
  provides:
    - dashboard/lib/engine-api.ts (EngineSource type + SOURCE_LABELS + source in EngineStatus + startEngine body extended)
    - dashboard/lib/mar-ready-api.ts (marReadyKeys + MarReadyItem + fetchMarReadyList + promoteAtrativo + promoteAtrativoBatch)
    - dashboard/components/engine/EngineControl.tsx (source radiogroup + UF chips when tripadvisor + active-source read-back)
    - dashboard/components/mar-ready/MarReadyList.tsx (list + optimistic single promote + bulk multi-select via AlertDialog)
    - dashboard/components/mar-ready/MarReadyActions.tsx (usePromoteMarReadyRecord + usePromoteMarReadyBatch hooks)
    - dashboard/app/mar-ready/page.tsx (/mar-ready route with optional UF filter from searchParams)
    - dashboard/app/page.tsx (SURFACES nav entry for /mar-ready)
    - dashboard/mocks/handlers/engine.ts (source field in engineStatus + engineStartSuccess fixtures)
    - dashboard/mocks/handlers/mar-ready.ts (MSW handlers for mar-ready list + single + batch promote endpoints)
  affects:
    - dashboard/components/mar-ready/MarReadyList.test.tsx (7 tests — list render, Promover button, empty state, optimistic remove)
    - dashboard/components/mar-ready/MarReadyActions.test.tsx (4 tests — optimistic remove, 409 rollback, WR-05 multi-cache rollback, batch dispatch)
tech-stack:
  added: []
  patterns:
    - EngineSource radiogroup mirrors EngineDepth radiogroup pattern exactly
    - marReadyKeys / fetchMarReadyList mirrors dlqKeys / fetchDlqList pattern
    - usePromoteMarReadyRecord mirrors useValidateDlqRecord (optimistic remove + snapshot rollback)
    - MSW double-prefix rule: BASE = "http://localhost:3000/api/api/v1/atrativos" (Pitfall 5)
    - WR-05 multi-cache snapshot rollback covers ALL cached ['mar-ready','list',...] keys
    - AlertDialog confirm before POST promote-batch (mirrors DLQ batch gate)
key-files:
  created:
    - dashboard/lib/mar-ready-api.ts
    - dashboard/mocks/handlers/mar-ready.ts
    - dashboard/components/mar-ready/MarReadyActions.tsx
    - dashboard/components/mar-ready/MarReadyList.tsx
    - dashboard/app/mar-ready/page.tsx
    - dashboard/components/mar-ready/MarReadyList.test.tsx
    - dashboard/components/mar-ready/MarReadyActions.test.tsx
  modified:
    - dashboard/lib/engine-api.ts (EngineSource type + SOURCE_LABELS + source in EngineStatus + startEngine body)
    - dashboard/mocks/handlers/engine.ts (source: null in fixture + source in engineStartSuccess)
    - dashboard/components/engine/EngineControl.tsx (source radiogroup + UF chips + active-source read-back)
    - dashboard/app/page.tsx (SURFACES /mar-ready entry)
decisions:
  - "usePromoteMarReadyRecord uses the same WR-05 multi-cache snapshot pattern as useValidateDlqRecord — cancels ALL ['mar-ready'] queries, snapshots ALL ['mar-ready','list'] entries, and restores ALL on error"
  - "UF chips default to all 27 BR states selected when source='tripadvisor' — operator deselects to narrow; when source='default', ufs param is omitted from startEngine body"
  - "promoteAtrativoBatch takes ufs[] and sends the first UF as the qs 'uf' param (mirrors the backend's single-UF batch endpoint signature)"
  - "app/mar-ready/page.tsx uses Suspense wrapper for useSearchParams per Next.js App Router requirement"
  - "BR_UFS array defined inline in EngineControl.tsx — no existing constant found in codebase"
metrics:
  duration: "~22min"
  completed: "2026-06-23"
  tasks: 2
  files: 11
requirements_completed:
  - TA-06
  - TA-07
---

# Phase 11 Plan 04: Dashboard Mar-Ready UI Summary

**One-liner:** TripAdvisor source radiogroup + UF chip multi-select in EngineControl, plus the /mar-ready dashboard route with optimistic single promote (409 rollback), bulk AlertDialog confirm, and 153 passing Vitest/MSW tests.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | engine-api.ts source extension + mar-ready-api.ts + MSW handlers | 069e41f | engine-api.ts, mar-ready-api.ts, mocks/handlers/engine.ts, mocks/handlers/mar-ready.ts |
| 2 | EngineControl source selector + /mar-ready route + Vitest tests | 4f1ffa0 | EngineControl.tsx, MarReadyActions.tsx, MarReadyList.tsx, app/mar-ready/page.tsx, app/page.tsx, *.test.tsx |

## What Was Built

### engine-api.ts Extensions
```typescript
export type EngineSource = "default" | "tripadvisor";
export const SOURCE_LABELS: Record<EngineSource, string> = {
  default: "Padrão",
  tripadvisor: "TripAdvisor",
};
// EngineStatus.source?: EngineSource | null (optional — server echoes it)
// startEngine body: source?: EngineSource
```

### mar-ready-api.ts
- `marReadyKeys` — `{ all: ["mar-ready"], list: (uf?) => ["mar-ready","list",{uf}] }`
- `MarReadyItem` interface — `{ id, canonical_key, uf, score, source }`
- `fetchMarReadyList(uf?)` — GET `/api/v1/atrativos/mar-ready`
- `promoteAtrativo(rioId)` — PATCH `/api/v1/atrativos/{id}/promote`
- `promoteAtrativoBatch(ufs, limit)` — POST `/api/v1/atrativos/promote-batch`

### EngineControl.tsx Additions
- Source radiogroup `data-testid="engine-source"` with Padrão / TripAdvisor options
- UF chip list `data-testid="engine-uf-chips"` — 27 BR states, all pre-selected, toggleable — shown only when `selectedSource === "tripadvisor"`
- `startEngine({ depth, source, ufs })` — ufs included only when source='tripadvisor'
- Active-source read-back `data-testid="engine-active-source"` when engine running

### MarReadyActions.tsx
```typescript
export function usePromoteMarReadyRecord(): UseMutationResult<...>
// onMutate: cancelQueries(['mar-ready']) + snapshot ALL ['mar-ready','list'] entries + optimistic filter
// onError: restore ALL snapshot entries (WR-05 multi-cache rollback)
// onSuccess: toast.success("Atrativo promovido → Mar")
// onSettled: invalidateQueries(['mar-ready'])

export function usePromoteMarReadyBatch(): UseMutationResult<...>
// mutationFn: promoteAtrativoBatch(ufs, limit)
// onSuccess: toast.success(`${res.promoted} atrativos de ${res.uf} promovidos → Mar`)
```

### MarReadyList.tsx
- `useQuery(marReadyKeys.list(uf), fetchMarReadyList)` with loading/empty/error states
- Table with canonical_key, UF, score columns
- "Promover" button per row → `usePromoteMarReadyRecord`
- Checkbox per row for multi-select (`useState<Set<string>>`)
- "Promover selecionados" bulk button → AlertDialog confirm → `usePromoteMarReadyBatch`
- Empty state: "Nenhum atrativo pronto para promoção"

### MSW mar-ready.ts Handlers
- `BASE = "http://localhost:3000/api/api/v1/atrativos"` (double-prefix enforced)
- `marReadyList(items?)` — GET /mar-ready with optional uf filter
- `promoteSuccess(rioId?)` — PATCH /:id/promote → 202
- `promoteFailure()` — PATCH /:id/promote → 409
- `promoteBatchSuccess(promoted)` — POST /promote-batch → 202

## Test Results

| Suite | Tests | Result |
|-------|-------|--------|
| MarReadyList.test.tsx | 7 | PASS |
| MarReadyActions.test.tsx | 4 | PASS |
| All existing dashboard tests | 142 | PASS |
| **Total** | **153** | **PASS** |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all data is fetched from MSW-mocked endpoints in tests and from the live FastAPI BFF in production. No hardcoded placeholder data flows to the UI.

## Threat Flags

All threats from the plan's threat register are mitigated:

| Flag | File | Status |
|------|------|--------|
| T-11-04-01 (EoP: Bearer auth on promote) | mar-ready-api.ts | Mitigated — apiFetch attaches Bearer header (existing pattern); BFF validates before forwarding to FastAPI |
| T-11-04-02 (Tampering: optimistic remove before 409) | MarReadyActions.tsx | Mitigated — onError restores snapshot; MarReadyActions.test.tsx asserts rollback on 409 |
| T-11-04-03 (InfoDisc: source + UF in POST body) | engine-api.ts | Accepted — same risk profile as existing depth parameter |

No new threat surface introduced beyond what is in the plan's threat register.

## Self-Check: PASSED

Created files exist:
- [x] dashboard/lib/mar-ready-api.ts — FOUND
- [x] dashboard/mocks/handlers/mar-ready.ts — FOUND
- [x] dashboard/components/mar-ready/MarReadyActions.tsx — FOUND
- [x] dashboard/components/mar-ready/MarReadyList.tsx — FOUND
- [x] dashboard/app/mar-ready/page.tsx — FOUND
- [x] dashboard/components/mar-ready/MarReadyList.test.tsx — FOUND
- [x] dashboard/components/mar-ready/MarReadyActions.test.tsx — FOUND

Commits exist:
- [x] 069e41f — feat(11-04): Task 1
- [x] 4f1ffa0 — feat(11-04): Task 2

Key acceptance criteria verified:
- [x] bun run test --run: 153 passed, 0 failed (24 test files)
- [x] EngineControl.tsx: data-testid="engine-source" present
- [x] EngineControl.tsx: data-testid="engine-active-source" present
- [x] EngineControl.tsx: selectedSource === "tripadvisor" conditional present
- [x] dashboard/app/mar-ready/page.tsx exists
- [x] dashboard/app/page.tsx: /mar-ready SURFACES entry present
- [x] MarReadyList.test.tsx: "Promover" assertion present (7 matches)
- [x] MarReadyActions.test.tsx: 409 + rollback + snapshot assertions present (5 matches)
