---
phase: 17-painel-brave-redesign-light-theme-single-shell-painel-kanban
plan: 05
subsystem: dashboard / painel (Kanban)
tags: [ui, kanban, drag-drop, mutations, tanstack-query, msw, tdd]
requires:
  - "17-02 painel-data: usePainelBoard, usePainelMetrics, filterCards, PainelCard"
  - "17-03/17-04: PainelMetrics, PainelFilters, PainelBoard, RecordCard"
  - "destinos-api / atrativos-api / mar-ready-api mutation fns + query keys"
provides:
  - "lib/painel-actions.ts: mapDrop/mapRetry/runAction + usePainelMutations (drop/retry → real mutation OR null)"
  - "components/painel/PainelView.tsx: wired Painel (Kanban) container (replaces 17-01 stub)"
affects:
  - "components/painel/PainelShell.test.tsx (PainelView now needs a QueryClient + MSW handlers)"
tech-stack:
  added: []
  patterns:
    - "Closed allow-list drop→mutation mapping as the security boundary (no invented transitions/endpoints)"
    - "Optimistic column-override map in the container; onError clears it; onSettled invalidates to reconcile"
    - "MarReadyActions optimistic/rollback/invalidate + explainError pattern reused in usePainelMutations"
key-files:
  created:
    - dashboard/lib/painel-actions.ts
    - dashboard/lib/__tests__/painel-actions.test.ts
    - dashboard/components/painel/__tests__/PainelView.test.tsx
  modified:
    - dashboard/components/painel/PainelView.tsx
    - dashboard/components/painel/__tests__/PainelShell.test.tsx
decisions:
  - "mapDrop returns null for nascente/in_progress/same-column/atrativo→dlq → toast 'Ação não disponível neste estágio', no endpoint call"
  - "atrativo→mar uses the audited mar-ready promoteAtrativo; 409 surfaces as revert+toast (no §7.6 gate bypass)"
  - "runAction throws on reprocess+atrativo (impossible action never constructed by mapDrop/mapRetry)"
  - "Optimistic override persists past success; onSettled invalidation refetch reconciles with server truth"
metrics:
  duration: ~35m
  completed: 2026-06-27
requirements-completed: [UI-PAINEL-1]
---

# Phase 17 Plan 05: Painel drag-drop + real mutations + PainelView container Summary

Wired the Painel (Kanban) view end-to-end: a closed drop/retry → real-mutation allow-list (`lib/painel-actions.ts`) plus the `PainelView` container that loads real board data + truthful metrics, owns the type + UF-scope filter state, and turns drag-drops and the ↺ Reprocessar button into the exact mapped backend mutations — with optimistic moves, query-key invalidation, and revert-with-toast on unmapped drops or errors.

## What was built

### Task 1 — `lib/painel-actions.ts` (TDD)
- `mapDrop(card, target)` — closed allow-list: `→mar` = promote, `→descarte` = descarte, `→dlq` = reprocess (destino only). Returns `null` for same-column, `nascente`, `in_progress`, and `atrativo→dlq`. No invented transitions.
- `mapRetry(card)` — destino → reprocess; atrativo → null.
- `runAction(a)` — dispatches to existing API fns (`promoteDestino`/`promoteAtrativo` mar-ready/`descarteDestino`/`descartarAtrativo`/`reprocessDestino`); throws on the impossible reprocess+atrativo.
- `usePainelMutations({ onOptimistic, onRevert })` — single `useMutation` over `runAction`; null mappings never call the mutation (toast + return); mapped actions apply optimistically, invalidate `["destinos"]`/`["atrativos"]`/`["engine","status"]` on settle, and revert + `toast.error(explainError)` on error. `explainError` ported from `MarReadyActions` (401/409/message).
- 17 unit tests: every mapDrop row + null cases, mapRetry both entities, runAction dispatch per entity+kind, and the reprocess+atrativo throw.

### Task 2 — `components/painel/PainelView.tsx` (TDD)
- Replaced the 17-01 stub with the container: `usePainelBoard()` cards + `usePainelMetrics()` (Destinos/Atrativos `EntityMetric` + `nascenteCount`), `type: TypeFilter` + `ufs: string[]` state, and an optimistic `overrides` map (cardId → column).
- Composes `<PainelMetrics>` + `<PainelFilters>` + `<PainelBoard>`; the Nascente column count comes from `usePainelMetrics().nascenteCount`; metrics reflect the whole base while the UF scope filters the board only.
- Drag tracked via `onCardDragStart` (`useRef`); `onDropToColumn(target)` → `usePainelMutations().drop(draggedCard, target)`; `onCardRetry` → `retry(card)`.
- 5 integration tests (Vitest + MSW): board renders 4 sample cards; nascente count from metrics; mapped destino→Descarte fires the real `…/descarte` PATCH and optimistically moves the card; unmapped atrativo→Revisão fires NO PATCH and toasts the unavailable copy; a seeded `routing:"descarte"` destino's ↺ Reprocessar fires the `…/reprocess` PATCH.

## Verification

- `bun run test lib/__tests__/painel-actions.test.ts components/painel/__tests__/PainelView.test.tsx` → 2 files / 22 tests passed.
- `bun run test` full suite → **34 files / 236 tests passed** (baseline 32/214; +2 files, +22 tests; no regressions).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `PainelShell.test.tsx` regression from replacing the stub**
- **Found during:** Task 2 (full-suite run)
- **Issue:** `PainelShell.test.tsx` rendered `<PainelView />` with a plain `render()` and no MSW handlers. Once PainelView began calling `usePainelBoard`/`usePainelMetrics`, those three tests threw "No QueryClient set" — a direct consequence of replacing the stub.
- **Fix:** Wrapped the three shell renders in a `QueryClientProvider` (`renderShell` helper) and registered `destinosListSuccess()/atrativosListSuccess()/engineStatus()` in a `beforeEach`. The `painel-view` testid still renders synchronously, so the existing shell assertions are unchanged.
- **Files modified:** `dashboard/components/painel/__tests__/PainelShell.test.tsx`
- **Commit:** 6a16bf9

**2. [Rule 1 - Bug] PainelView test waited with `findByTestId("record-card")` (multiple matches)**
- **Found during:** Task 2 (first GREEN run)
- **Issue:** The board renders 4 `record-card` elements; `findByTestId` throws on multiple matches.
- **Fix:** Switched the load-wait to `findAllByTestId("record-card")` in the two affected tests.
- **Files modified:** `dashboard/components/painel/__tests__/PainelView.test.tsx`
- **Commit:** 6a16bf9

## Threat surface

No new endpoints, network paths, or trust boundaries introduced. All mutations route through the existing `lib/*-api` clients (BFF Bearer); `mapDrop` is a closed allow-list (T-17-05-01) and the atrativo→mar path uses the audited mar-ready promote with 409 → revert+toast (T-17-05-04). No new packages (T-17-05-SC).

## Known Stubs

None — the container is wired to real data and real mutations; no placeholder/empty-data flows remain in this slice.

## Self-Check: PASSED

All created files exist on disk; all 4 task commits (8ebe7ab, 6b5b2e3, 2da3d80, 6a16bf9) are present in git history.
