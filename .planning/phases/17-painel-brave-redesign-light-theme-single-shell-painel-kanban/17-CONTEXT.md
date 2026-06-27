# Phase 17: Painel Brave redesign — shell + Painel Kanban (slice 1) — Context

**Gathered:** 2026-06-27
**Status:** Ready for planning
**Source:** Claude Design import (`Painel Brave.dc.html`) + operator scope decisions

<domain>
## Phase Boundary

First slice of the Painel Brave CMS redesign. Build the **light-theme single-shell** + the
**Painel (Kanban)** view, at a NEW route `/painel`, ALONGSIDE the existing 10 dark routes
(non-breaking). Wire to EXISTING API clients — not a static mock. The design contract is the
imported canvas at `design/Painel-Brave.dc.html` (a `.dc.html` with `{{ }}` bindings + `<sc-if>`/
`<sc-for>` directives — reference only; we reimplement in React/Next, not port the canvas runtime).

In scope (slice 1):
- The shell: 232px white sidebar (Geist logo `public/logo-norteia-brave.svg`, 6 nav items in two
  groups "Processamento"/"Operação", operator footer "OP / Operador Brave / CMS Territorial") +
  58px topbar (page title+subtitle, TripAdvisor session pill, "Origem {source}" modal trigger,
  motor on/off switch) + a client-side view-switcher (only Painel active this slice; the other 5
  nav items render a "Em breve" placeholder).
- The Painel (Kanban) view: 2 metric cards (Destinos/Atrativos), type-filter segmented control,
  UF-scope dropdown, horizontal-scroll stage columns of record cards.
- Light theme scoped to `/painel` (do NOT flip the global dark theme — see decisions).

Out of scope (later slices): full replace of the dark routes; the Duplicados, Mapeamento,
Conversas, Custo, Varreduras views; the record-edit drawer (Dados/Conversa tabs); the full
source/depth modal body; new backend endpoints; a dark/light toggle.
</domain>

<decisions>
## Implementation Decisions

### Layout & shell
- New route `dashboard/app/painel/page.tsx`. Single-shell SPA-style: sidebar + topbar + a
  view-switcher driven by local state (`useState<'painel'|'duplicados'|'mapeamento'|'varreduras'|
  'conversas'|'custo'>`), NOT nested Next routes (mirrors the mockup's `setView`). Only `painel`
  is implemented; the other five render a centered "Em breve" placeholder card.
- Reuse the existing BFF + API clients (`lib/engine-api`, `lib/destinos-api`, `lib/atrativos-api`,
  `lib/ta-sweep-api`/`taSessionKeys`) and TanStack Query. Do NOT add backend endpoints.

### Theme (light, scoped — do NOT flip global dark)
- The app is dark-by-default (`.dark` on `<html>`, `globals.css`). This slice introduces a LIGHT
  surface ONLY for `/painel`. Implement light styling LOCAL to the painel subtree (a wrapper that
  sets the light tokens), so the other 10 routes stay dark and their Vitest suites stay green.
- Design tokens (from the canvas inline styles — use these exact values):
  - bg `oklch(0.98 0.01 90)` (cream); card/white `#fff`; text `#18181b`.
  - brand/primary navy `#15315e`; muted text `#6b7280` / `#9ca3af`; hint `#94a3b8`.
  - borders `#e6e4e0` (outer) / `#f0eee9` (inner); chip/panel bg `#f0eee9`.
  - status: green `oklch(0.55 0.15 150)` (sincronizado/Mar), red `oklch(0.55 0.20 27)` (falha/
    descarte), yellow `oklch(0.72 0.15 75)` (duplicado/dlq).
  - radius ~8–13px; font Geist (UI) + Geist Mono (numbers/IDs/UF) — already loaded in the app.
- Prefer Tailwind v4 utilities + CSS vars over inline styles. Define the painel light tokens once
  (scoped class or CSS-var block) and reference them; do NOT hardcode hex across components.

### Topbar wiring (real)
- Page title/subtitle: static per active view (Painel → "Painel" / "Quadro de processamento").
- TripAdvisor session pill: from `fetchTASessionStatus()` (present/expires_in/reason). Click =
  no-op placeholder this slice (re-inject flow is later) — render the pill + state only.
- "Origem {source}" trigger: shows the current `source` from `fetchEngineStatus()`; clicking opens
  a minimal popover/modal to switch source (default/tripadvisor) via `startEngine` source param —
  OR, to keep slice 1 tight, render the trigger + current source read-only and defer the switch
  modal to a later slice. (Planner: pick read-only if the modal balloons the slice.)
- Motor switch: reflects `fetchEngineStatus().state` (idle/running/stopping); toggling calls
  `startEngine()` / `stopEngine()` (reuse EngineControl's exact mutation pattern). Confirm-before-
  start is acceptable.

### Painel (Kanban) view — data mapping
- **Metric cards (Destinos / Atrativos):** "total no escopo" + "sincronizados" (routing=mar) +
  "falhas" + "progresso %". Derive from `fetchEngineStatus().counts` (it already exposes nascente,
  rio-by-routing, mar, atrativos-by-sub_state) and/or `fetchDestinoList`/`fetchAtrativoList` totals.
  Planner: prefer engine status counts (single call) if they cover total/mar/falha; else aggregate
  the list endpoints. "Falhas" = descarte + poison? Use routing=descarte for slice 1.
- **Type filter** (Tudo / Destinos / Atrativos): client-side filter over the loaded cards.
- **UF scope dropdown:** multi-select of the 27 UFs (reuse the UF list/constant from EngineControl).
  Filters the board AND is the intended sweep scope (display only this slice; do not re-trigger sweep).
- **Stage columns:** map to the canonical pipeline taxonomy the app already uses (StageBadge):
  `Nascente` → `Em processamento` (rio/in_progress) → `Sincronizado` (mar); plus `Revisão` (dlq)
  and `Descarte`. Cards = destinos + atrativos in scope, placed by their routing/stage. Card fields:
  chip (Destino/Atrativo), score band (≥85 green / 40–84.9 amber / <40 red — reuse StageBadge bands),
  name, UF (mono chip), município, source label, "Possível duplicado" flag (validation_pending or a
  dedup hint if present), and on failed cards a `⚠ {reason}` + `↺ Reprocessar` button.
- **Drag-and-drop (riskiest — scope as its own plan):** cards are draggable between columns. A drop
  fires the REAL mutation when the target maps to an existing action: → Sincronizado = promote
  (`promoteDestino`/`promoteAtrativo`), → Descarte = descarte, → Revisão/reprocess = reprocess.
  Drops with no matching real action REVERT with a toast "Ação não disponível neste estágio" — do
  NOT invent transitions or endpoints. The `↺ Reprocessar` button on falha cards calls reprocess.
  Optimistic update + invalidate query keys on success; revert on error (sonner toast).

### Testing
- Vitest + MSW, mirroring existing component tests. Add MSW handlers as needed (reuse
  `mocks/handlers/engine`, `destinos`, `atrativos`). New handlers for any new shape.
  Keep the suite offline; do not regress the existing 25 suites / 164 tests.
- Light-theme scoping must not change the existing dark routes' snapshots/tests.

### Claude's Discretion
- Exact component decomposition under `dashboard/components/painel/`, the precise metric-source
  choice (engine-status counts vs list aggregation), whether the source-switch modal ships this
  slice or read-only, the drag-drop library (native HTML5 DnD like the mockup, vs a lib) — provided
  the locked tokens, the route-`/painel`-alongside constraint, real-data wiring, and the non-
  regression of existing routes hold.
</decisions>

<canonical_refs>
## Canonical References

- `design/Painel-Brave.dc.html` — the imported design contract (this phase dir). Reference for
  layout, tokens, copy (pt-BR), and the 6-view structure. Reimplement in React; do not port `<sc-*>`.
- `dashboard/app/processo/page.tsx`, `dashboard/components/engine/EngineControl.tsx` — closest
  analogs for engine status/start/stop/source/UF + the motor switch + TA session pill.
- `dashboard/components/cms/StageBadge.tsx` — the canonical routing/sub_state/score-band taxonomy +
  source chips the Kanban cards must reuse.
- `dashboard/lib/engine-api.ts`, `destinos-api.ts`, `atrativos-api.ts`, `ta-sweep-api.ts` — the
  API clients + query keys + types to consume.
- `dashboard/lib/api-client.ts` (`bff`, `apiFetch`, Bearer), `dashboard/app/api/[...path]/route.ts`
  (BFF proxy), `dashboard/mocks/` + `vitest.setup.ts` — transport + test infra to mirror.
- `dashboard/app/globals.css` — current dark token definitions; add the scoped light tokens here.
- `dashboard/public/logo-norteia-brave.svg` — the sidebar logo (already downloaded).
</canonical_refs>

<specifics>
## Specific Ideas
- Sidebar nav (exact labels + groups): **Processamento** → Painel (Kanban), Duplicados (badge =
  count), Mapeamento, Varreduras · **Operação** → Conversas WhatsApp, Custo & LLM.
- Topbar order (right): TA session pill · "Origem {source}" trigger · divider · motor label +
  switch.
- Operator footer: round "OP" avatar (navy bg) · "Operador Brave" · "CMS Territorial".
- pt-BR copy throughout (match the mockup).
</specifics>

<deferred>
## Deferred Ideas (later slices / phases)
- Views: Duplicados (dedup pairs — needs a pgvector dedup-pairs endpoint), Mapeamento (data-mapper
  field mapping — needs a mapping endpoint/config), Conversas (wire `conversations-api`), Custo
  (wire `cost-api`), Varreduras (needs a runs-history endpoint — engine status is current-only).
- The record-edit drawer (Dados/Conversa tabs) opened from a card click.
- The full source/depth modal body.
- Replacing the 10 dark routes / global light theme + a theme toggle.
- The other two logos (`logo-brave-mark.svg`, `logo-norteia-horizontal.svg`) — fetch when needed.
</deferred>

---

*Phase: 17-painel-brave-redesign-light-theme-single-shell-painel-kanban*
*Context gathered: 2026-06-27 via Claude Design import + operator scope decisions*
