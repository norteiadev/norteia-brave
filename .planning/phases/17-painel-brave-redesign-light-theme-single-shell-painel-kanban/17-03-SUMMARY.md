---
phase: 17-painel-brave-redesign-light-theme-single-shell-painel-kanban
plan: 03
subsystem: dashboard-painel
tags: [ui, painel, light-theme, metrics, filters, react]
requirements-completed: [UI-PAINEL-1]
requires:
  - "lib/painel-data: EntityMetric, BR_UFS, TypeFilter (17-02)"
  - ".painel-light scoped tokens in globals.css (17-01)"
provides:
  - "components/painel/PainelMetrics: Destinos + Atrativos metric cards (presentational)"
  - "components/painel/PainelFilters: type segmented control + UF-scope dropdown (controlled)"
affects:
  - "PainelView container (17-05) wires these to usePainelMetrics + filter state"
tech-stack:
  added: []
  patterns:
    - "Presentational props-driven components; state/data live in the container"
    - "Scoped painel CSS vars (--card, --status-mar, --status-descarte, --painel-*) — no hardcoded hex"
    - "EngineControl UF chip multi-select toggle pattern (selected ? filter out : append)"
key-files:
  created:
    - dashboard/components/painel/PainelMetrics.tsx
    - dashboard/components/painel/PainelFilters.tsx
    - dashboard/components/painel/__tests__/PainelMetrics.test.tsx
    - dashboard/components/painel/__tests__/PainelFilters.test.tsx
  modified: []
decisions:
  - "PainelMetrics is purely presentational — no hook, no buildMetrics; the container supplies EntityMetric props from usePainelMetrics (17-02)"
  - "PainelFilters is fully controlled (value + onChange); only the UF popover open/close is local useState"
  - "UF chip selected style uses --painel-chip bg (no opacity-modifier on a CSS-var color, which Tailwind can't compose reliably)"
metrics:
  duration: ~10m
  completed: 2026-06-27
  tasks: 2
  files: 4
---

# Phase 17 Plan 03: Painel metric cards + filters Summary

Faithful, tested top-of-view chrome for the Painel (Kanban): two `EntityMetric`-driven metric cards (Destinos / Atrativos) and the controlled filter pair (type segmented control + UF-scope multi-select dropdown), both presentational and wired to real data by the container in 17-05.

## What was built

- **`PainelMetrics.tsx`** — a flex row of two cards via a private `MetricCard` subcomponent. Each card shows the dot + label, a Geist-Mono `total` + "no escopo", `Sincronizados` (green, `--status-mar`), `Falhas` (red, `--status-descarte`), and a `Progresso` block (label + `{pct}%` + a `--painel-chip` track with a `--status-mar` fill whose width = `pct%`). Props `{ destino, atrativo }: EntityMetric`. No hook / no `buildMetrics` call. data-testids: `metric-{destino|atrativo}-{total|mar|falha|pct}`.
- **`PainelFilters.tsx`** — controlled props `{ type, onTypeChange, ufs, onUfsChange, counts? }`. The segmented pill renders 3 buttons (`filter-type-{all|destino|atrativo}`) with `data-active`/`aria-pressed` and optional count badges. The UF dropdown trigger (`filter-uf-trigger`) shows "Escopo UF" + (`ufs.length===0 ? "Todas" : "{n} UF"`); a local `useState(open)` toggles the popover of 27 `BR_UFS` chips (`filter-uf-{uf}`) using the EngineControl toggle pattern, plus a `filter-uf-clear` ("Todas") that calls `onUfsChange([])`. Helper text from the design included.

## Tasks

| Task | Name | RED commit | GREEN commit |
| ---- | ---- | ---------- | ------------ |
| 1 | PainelMetrics — Destinos + Atrativos cards | 7cbf9f5 | 3028f2a |
| 2 | PainelFilters — type control + UF dropdown | 62dfee5 | 205e454 |

## Verification

- `bun run test components/painel/__tests__/PainelMetrics.test.tsx` → 3 passed.
- `bun run test components/painel/__tests__/PainelFilters.test.tsx` → 5 passed.
- Full suite: **30 files / 201 tests passed** — no regression to the existing dark-route suites.

## Deviations from Plan

None — plan executed as written. (The plan's "active styling" for selected UF chips suggested `bg-[var(--painel-navy)]/10`; used the locked `--painel-chip` token instead, since Tailwind cannot reliably apply an opacity modifier to a CSS-var color. Token-only, no behavior change.)

## Known Stubs

None. Both components are final presentational chrome; the data wiring is intentionally deferred to the 17-05 container (props-driven by design, per plan).

## TDD Gate Compliance

Both tasks followed RED → GREEN: a `test(...)` commit (failing — module not found) precedes each `feat(...)` commit. No REFACTOR commit needed.

## Self-Check: PASSED
