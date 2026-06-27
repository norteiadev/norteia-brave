---
phase: 17-painel-brave-redesign-light-theme-single-shell-painel-kanban
plan: 01
subsystem: dashboard
tags: [ui, painel, light-theme, shell, engine-api]
requirements-completed: [UI-PAINEL-1]
requires:
  - lib/engine-api (fetchEngineStatus/startEngine/stopEngine/fetchTASessionStatus)
  - app/globals.css (:root/.dark token base)
  - public/logo-norteia-brave.svg
provides:
  - "/painel route — light single-shell SPA with local view-switcher"
  - "scoped .painel-light CSS token block (no global dark-theme impact)"
  - "components/painel/* (PainelShell, PainelSidebar, PainelTopbar, PainelView, nav.ts)"
affects:
  - dashboard light-theme surface (scoped to /painel only)
tech-stack:
  added: []
  patterns:
    - "Scoped theme via descendant class re-declaring shadcn vars (mirrors .dark)"
    - "SPA view-switcher (useState) instead of nested Next routes"
    - "TanStack useQuery + useMutation reusing EngineControl's start/stop pattern"
key-files:
  created:
    - dashboard/app/painel/page.tsx
    - dashboard/components/painel/nav.ts
    - dashboard/components/painel/PainelShell.tsx
    - dashboard/components/painel/PainelSidebar.tsx
    - dashboard/components/painel/PainelTopbar.tsx
    - dashboard/components/painel/PainelView.tsx
    - dashboard/components/painel/__tests__/PainelShell.test.tsx
    - dashboard/components/painel/__tests__/PainelTopbar.test.tsx
  modified:
    - dashboard/app/globals.css
decisions:
  - "Source switch is read-only this slice (deferred per 17-CONTEXT to keep slice tight)"
  - "Light theme scoped via append-only .painel-light block; :root/.dark untouched"
metrics:
  tasks: 3
  files-created: 8
  files-modified: 1
  duration: ~14m
  completed: 2026-06-27
---

# Phase 17 Plan 01: Painel Brave light shell + topbar wiring Summary

A scoped light-theme single-shell at the new `/painel` route — 232px white sidebar +
58px topbar wired to the real engine-api + local view-switcher — built alongside the
existing 10 dark routes without touching the global dark theme.

## What was built

- **Task 1 — Scoped light tokens (`globals.css`).** Appended a `.painel-light` block
  that re-declares the shadcn base vars (`--background`, `--card`, `--border`,
  `--primary` navy `#15315e`, status colors) plus painel-only literals
  (`--painel-navy`, `--painel-cream`, borders, muted/hint) using the LOCKED
  17-CONTEXT values. Because `.painel-light` is a descendant class of
  `<html class="dark">`, it wins for the `/painel` subtree only — reused components
  (StageBadge `bg-[var(--status-mar)]`) resolve to light values automatically. The
  `:root`, `.dark`, `@theme inline`, and `@layer base` blocks are byte-for-byte
  unchanged (commit 1721c93).

- **Task 2 — Sidebar + nav config + shell (`nav.ts`, `PainelSidebar`, `PainelShell`,
  `PainelView`).** `nav.ts` exports `PainelViewKey` + `NAV_GROUPS`
  (Processamento → Painel (Kanban)/Duplicados/Mapeamento/Varreduras · Operação →
  Conversas WhatsApp/Custo & LLM) with exact pt-BR labels. `PainelSidebar` renders the
  232px white column (logo, grouped nav with the design's inline SVG glyphs, navy "OP"
  operator footer); the active item carries `aria-current="page"` + `data-active`.
  `PainelShell` composes sidebar + topbar slot + content slot. `PainelView` is the
  stub (`data-testid="painel-view"`) that plan 17-05 replaces (commit 0647b70).

- **Task 3 — Topbar wiring + `/painel` page (`PainelTopbar`, `app/painel/page.tsx`).**
  `PainelTopbar` polls `fetchEngineStatus` (10s) + `fetchTASessionStatus`; the motor
  switch (`role="switch"`, `aria-checked={state!=="idle"}`) toggles via start/stop
  mutations copying EngineControl's `onError(toast)`/`onSettled(invalidate)` pattern —
  confirm-before-start via `window.confirm`. The TA pill shows
  "Pronta"/"Precisa bootstrap"/"Expirada"; "Origem {source}" is read-only
  (`SOURCE_LABELS`). The page holds a local `useState<PainelViewKey>` switcher under
  `.painel-light`; non-painel views render a centered "Em breve" placeholder (commit
  7a6f02f).

## Verification

- `components/painel/__tests__/PainelShell.test.tsx` — 3 tests green (6 nav items, 2
  group headers, operator footer, active marking, onSelect).
- `components/painel/__tests__/PainelTopbar.test.tsx` — 9 tests green (idle/running
  switch state, start-on-confirm, stop, confirm-cancel no-op, TA pill states, source
  labels).
- Full suite: **27 files / 176 tests passing** (baseline 25/164 + 2 new painel suites /
  12 tests). The existing 10 dark-route suites are unaffected — confirms the scoped
  light tokens caused no regression (threat T-17-01-02 mitigated).

## Deviations from Plan

None — plan executed as written. The "logo missing" appeared during reads but was a
false alarm caused by an `ls`-output filter; `public/logo-norteia-brave.svg` exists and
is referenced as `/logo-norteia-brave.svg`. No new packages added (threat T-17-01-SC:
no install gate triggered).

## Deferred Issues

Two PRE-EXISTING `tsc --noEmit` errors surfaced (in `EngineControl.test.tsx` and
`mocks/handlers/mar-ready.ts`) — both in files this plan did not touch; the new
`components/painel/*` + `app/painel/page.tsx` type-check cleanly. Logged to
`deferred-items.md` (SCOPE BOUNDARY — not fixed here).

## Known Stubs

- `PainelView` is an intentional stub (`Painel — carregando…`) — plan 17-05 fills the
  Kanban body (metric cards, type filter, UF-scope dropdown, stage columns). Documented
  as in-scope-deferred by the phase plan; not a goal-blocking stub for slice 1 (this
  slice's goal is the shell + topbar wiring, both fully implemented and tested).
- The 5 non-painel views render an "Em breve" placeholder by design (later slices wire
  Duplicados/Mapeamento/Varreduras/Conversas/Custo).

## Self-Check: PASSED

- All 8 created files present on disk.
- All 3 task commits present in git history (1721c93, 0647b70, 7a6f02f).
- `globals.css` `:root`/`.dark` blocks untouched; `.painel-light` appended only.
