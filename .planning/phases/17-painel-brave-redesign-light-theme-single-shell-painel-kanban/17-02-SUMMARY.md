---
phase: 17-painel-brave-redesign-light-theme-single-shell-painel-kanban
plan: 02
subsystem: ui
tags: [tanstack-query, vitest, msw, kanban, painel, lgpd, selectors]

# Dependency graph
requires:
  - phase: D-03 Destinos / D-04 Atrativos data layers
    provides: fetchDestinoList/fetchAtrativoList list endpoints + envelope {items,total}, query keys, list-safe types
  - phase: engine data layer
    provides: fetchEngineStatus().counts.nascente (Nascente column count source)
provides:
  - PainelCard model + pure, unit-tested selectors (routingToColumn/toPainelCards/filterCards/buildColumns/computeMetric)
  - usePainelBoard hook (unified destinos+atrativos card[])
  - usePainelMetrics hook (truthful per-entity total/mar/falha from envelope total + nascente from engine counts)
  - BR_UFS (27 codes) + COLUMN_DEFS exported for the filters/board plans
affects: [17-03 painel filters/segmented control, 17-04 RecordCard + Kanban columns, 17-05 painel shell wiring]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-selector-first data layer: React-free selectors + thin hooks at the bottom of one tested module"
    - "Truthful metrics via list ENVELOPE total (server count) + limit:1 count queries, never loaded-array length"
    - "LGPD allow-list mapping at the trust boundary (toPainelCards) — PII fields never copied"

key-files:
  created:
    - dashboard/lib/painel-data.ts
    - dashboard/lib/__tests__/painel-data.test.ts
  modified: []

key-decisions:
  - "Metrics are whole-base per-entity totals (UF scope filters the board only this slice); documented for the filters plan"
  - "Nascente column count comes from engine counts.nascente — rio-backed lists don't surface nascente-only records, so the column has a count but no draggable cards this slice"
  - "duplicate = validation_pending for BOTH entity types (destinos have no atrativo-style dedup flag; validation_pending IS the slice-1 'possível duplicado' hint)"
  - "source = null and error = null this slice (no list field today); RecordCard hides the source label when null and supplies a generic ⚠ falha label for descarte cards"

patterns-established:
  - "Pattern: per-filtered-count distinct query keys (destinoKeys.list({count:'mar'})) so total/mar/descarte counts cache independently"
  - "Pattern: limit:1 payloads for count-only metric queries to keep transfer tiny while reading the server-side envelope total"

requirements-completed: [UI-PAINEL-1]

# Metrics
duration: ~10min
completed: 2026-06-27
---

# Phase 17 Plan 02: Painel data layer Summary

**A single tested module that turns the existing destinos/atrativos list endpoints into a unified PainelCard model, five Kanban stage columns, and truthful per-entity metrics (server-side envelope totals + engine Nascente count) — with an LGPD allow-list that keeps all PII out of the board.**

## Performance

- **Duration:** ~10 min
- **Tasks:** 1 TDD feature (RED → GREEN)
- **Files created:** 2

## Accomplishments
- Pure, React-free selectors (`routingToColumn`, `toPainelCards`, `filterCards`, `buildColumns`, `computeMetric`) fully unit-covered.
- `usePainelBoard` fetches both lists through the BFF and builds the unified `PainelCard[]`.
- `usePainelMetrics` derives `{ total, mar, falha, pct }` per entity from the list **envelope `total`** (server count, not loaded-array length) plus `nascenteCount` from `fetchEngineStatus().counts.nascente`.
- LGPD allow-list at `toPainelCards`: no `phone_e164` / `phone_masked` / `contacts_summary` ever enters a card (asserted, incl. a defensive whole-board serialise check).
- Exported `BR_UFS` (27 codes) + `COLUMN_DEFS` so the filters/board plans import the taxonomy from here.

## Task Commits

1. **RED — failing tests for selectors + hooks** — `b7d20db` (test)
2. **GREEN — implement painel-data.ts** — `4f003db` (feat)

## Files Created/Modified
- `dashboard/lib/painel-data.ts` — PainelCard model, pure selectors, `usePainelBoard` + `usePainelMetrics` hooks, `BR_UFS`, `COLUMN_DEFS`.
- `dashboard/lib/__tests__/painel-data.test.ts` — 17 tests: every pure selector + both hooks over MSW.

## Truthfulness / scope notes (per success criteria)
- **Metrics = whole base.** `usePainelMetrics` issues `limit:1` count queries against the destinos/atrativos list endpoints and reads the envelope `total` (the server-side filtered count). It does NOT apply the UF scope — the UF scope filters the **board** (`filterCards`) only this slice. The filters plan should surface metrics as "total no escopo = whole base" until a later slice scopes counts by UF.
- **Nascente column.** The list endpoints only surface rio-backed routings (`in_progress|mar|dlq|descarte`); nascente-only records are not returned. The Nascente column therefore shows a real **count** (`counts.nascente`) but has **no draggable cards** this slice.
- **Why list envelopes, not engine counts, for the metric split:** engine `counts` expose atrativos only by FSM sub_state (no routing=mar/descarte), so they cannot split sincronizados/falhas for the atrativos card. The list envelope `total` is per-entity and supports a `routing` filter — the uniform truthful source for both cards. `counts.nascente` remains the truthful source for the Nascente column.

## Deviations from Plan
None — plan executed exactly as written (TDD RED→GREEN, two atomic commits, no new packages).

## Known Stubs
- `PainelCard.source` and `PainelCard.error` are `null` this slice (no list field exists today). Intentional and documented in the plan: RecordCard (17-04) hides the source label when null and supplies a generic ⚠ falha label for descarte cards. Resolved when detail-backed source/reason fields land in a later slice.

## Threat Flags
None — no new network surface; all fetches route through `lib/*-api` (apiFetch/BFF Bearer). The PII allow-list (T-17-02-01) is implemented and unit-asserted.

## Verification
- `cd dashboard && bun run test lib/__tests__/painel-data.test.ts` → 17 passed.
- `cd dashboard && bun run test` (full suite) → 26 files / 181 tests passed (no regressions).

## Self-Check: PASSED
- `dashboard/lib/painel-data.ts` — FOUND
- `dashboard/lib/__tests__/painel-data.test.ts` — FOUND
- Commit `b7d20db` (RED test) — FOUND
- Commit `4f003db` (GREEN feat) — FOUND
