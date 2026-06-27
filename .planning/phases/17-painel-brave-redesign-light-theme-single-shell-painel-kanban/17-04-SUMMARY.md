---
phase: 17-painel-brave-redesign-light-theme-single-shell-painel-kanban
plan: 04
subsystem: dashboard-painel
tags: [ui, kanban, drag-and-drop, react, vitest]
requirements-completed: [UI-PAINEL-1]
requires:
  - "dashboard/lib/painel-data.ts (PainelCard type, buildColumns, COLUMN_DEFS — 17-02)"
  - "dashboard/components/cms/StageBadge.tsx (score-band taxonomy — reused)"
  - "dashboard/app/globals.css (.painel-light tokens — 17-01)"
provides:
  - "dashboard/components/painel/RecordCard.tsx (draggable record card)"
  - "dashboard/components/painel/PainelBoard.tsx (5-column Kanban board, drag-event contract)"
affects:
  - "17-05 (container wires the drop mutations + drag handlers into PainelBoard/RecordCard)"
tech-stack:
  added: []
  patterns:
    - "Native HTML5 drag-and-drop (draggable + onDragStart/onDrop) — no drag library"
    - "Presentational components forward drag/retry/click events to handler props"
    - "Score bands reused from StageBadge (no re-derived thresholds/colors)"
key-files:
  created:
    - "dashboard/components/painel/RecordCard.tsx"
    - "dashboard/components/painel/PainelBoard.tsx"
    - "dashboard/components/painel/__tests__/RecordCard.test.tsx"
    - "dashboard/components/painel/__tests__/PainelBoard.test.tsx"
  modified: []
decisions:
  - "Component named RecordCard (not PainelCard) to avoid colliding with the PainelCard data TYPE"
  - "source label hidden entirely when null (no — placeholder, L-2)"
  - "Nascente column count sourced from nascenteCount prop; other columns count own cards"
metrics:
  duration: ~6m
  completed: 2026-06-27
  tasks: 2
  files: 4
---

# Phase 17 Plan 04: Painel Kanban board + draggable RecordCard Summary

Built the presentational Painel (Kanban) surface: a draggable `RecordCard` that reuses the canonical StageBadge score-band taxonomy, and a 5-column horizontal-scroll `PainelBoard` exposing a clean native-HTML5 drag-event contract for the 17-05 container to wire mutations into.

## What was built

- **RecordCard.tsx** — single draggable card showing the design fields: type chip (Destino/Atrativo), StageBadge score band (`score.toFixed(1)`, e.g. "91.0"), name, UF mono chip, município, source label (hidden when null — no "—"), "Possível duplicado" flag, and on `column==="descarte"` a `⚠ {error ?? "Falha no processamento"}` line + a `↺ Reprocessar` button (`data-testid=record-card-retry`) that `stopPropagation`s and calls `onRetry(card)`. Root is `draggable`, fires `onDragStart(card)`, carries `data-id`/`data-testid="record-card"`. Painel CSS vars only.
- **PainelBoard.tsx** — 5 columns in `COLUMN_DEFS` order (Nascente → Em processamento → Sincronizado → Revisão → Descarte) via `buildColumns(cards)`. Each header: dot + label + mono count pill (`painel-col-count-{key}`). Nascente count from the `nascenteCount` prop; other columns count their own cards. Each body (`painel-col-{key}`) is a drop target: `onDragOver` preventDefault + `onDrop` → `onDropToColumn(key)`; maps `RecordCard` per card; horizontal scroll; optional "Carregando…" placeholder when `isPending` and empty.

## Tests

- `RecordCard.test.tsx` (8 tests): Destino + "91.0" score band, atrativo chip, duplicate flag, no "—" placeholder when source null, source label when set, descarte ⚠ + working retry → `onRetry(card)`, generic falha fallback, draggable + `onDragStart(card)`.
- `PainelBoard.test.tsx` (5 tests): all 5 column testids + correct counts, `nascenteCount=9` overrides Nascente count, one RecordCard per card, `drop` on `painel-col-descarte` → `onDropToColumn("descarte")`, empty array renders 5 columns with no crash.
- Target files: 13/13 green. Full dashboard suite: **30 files / 206 tests green** (no regression).

## Deviations from Plan

None — plan executed exactly as written.

## TDD Gate Compliance

Both tasks followed RED → GREEN: a `test(17-04)` commit (failing) precedes each `feat(17-04)` commit (passing). No REFACTOR commits needed.

## Known Stubs

None functional. The board is presentational by design — `onDropToColumn`/`onCardDragStart`/`onCardRetry`/`onCardClick` are handler props the 17-05 container will bind to real mutations. This is the planned drag-event contract, not a stub.

## Self-Check: PASSED

- FOUND: dashboard/components/painel/RecordCard.tsx
- FOUND: dashboard/components/painel/PainelBoard.tsx
- FOUND: dashboard/components/painel/__tests__/RecordCard.test.tsx
- FOUND: dashboard/components/painel/__tests__/PainelBoard.test.tsx
- Commits: 64a61c0 (RecordCard feat), 1724af6 (PainelBoard feat) + their preceding test commits — verified in git log.
