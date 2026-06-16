---
phase: 04-dashboard-territorial-cms
plan: 04
subsystem: dashboard-frontend
tags: [dash-01, dlq, master-detail, tanstack-query, tanstack-table, shadcn, score-breakdown, optimistic, msw, vitest, d-04, d-05, d-06]

# Dependency graph
requires:
  - phase: 04-dashboard-territorial-cms
    plan: 02
    provides: "providers (TanStack Query), lib/api-client (BFF apiFetch + ApiError), MSW+Vitest harness, shadcn new-york preset"
  - phase: 04-dashboard-territorial-cms
    plan: 03
    provides: "GET /api/v1/dlq/{rio_id} detail contract (score_breakdown/normalized/nascente_payload/signals/whatsapp_log)"
provides:
  - "/dlq master-detail review surface (DASH-01 frontend half)"
  - "ScoreBreakdownPanel — the signature §7.6 per-criterion explainability component (reusable)"
  - "ReviewPanel + QueueList — action-agnostic master-detail scaffold (reused by the plan-05 WhatsApp gate)"
  - "StatusBadge — routing/sub_state semantic status badge"
  - "dlq-actions — TanStack useMutation hooks over the existing dlq.py mutations (validate/descarte/reprocess/validate-batch)"
  - "lib/dlq-api — DLQ query keys + typed BFF fetchers; mocks/handlers/dlq — full-view-state MSW handlers"
affects:
  - "plan-05 (WhatsApp gate) reuses ReviewPanel/QueueList/StatusBadge"
  - "the app nav shell (later) links to /dlq"

# Tech tracking
tech-stack:
  added:
    - "@tanstack/react-table 8.21.3 (DLQ queue table, row selection)"
    - "shadcn primitives: button/table/badge/progress/label/card/skeleton/alert-dialog/sonner/separator/scroll-area/input (official registry)"
    - "sonner 2.x (state-transition toasts via shadcn wrapper)"
  patterns:
    - "Shared ['dlq'] query-key prefix → one invalidateQueries(['dlq']) refetches list AND detail"
    - "Optimistic validate: onMutate drops the row from the visible queue, onError rolls back, onSettled invalidates"
    - "Action-agnostic ReviewPanel: actions injected as a render prop so the gate reuses the same panel"
    - "Quantitative score bars: neutral fill + green/amber/red THRESHOLD cap (≥85 / 51–84.9 / ≤50), never a rainbow"
    - "MSW base URL is http://localhost:3000 (jsdom location) + the BFF /api/api/v1 double-prefix"

key-files:
  created:
    - dashboard/components/dlq/ScoreBreakdownPanel.tsx
    - dashboard/components/dlq/StatusBadge.tsx
    - dashboard/components/dlq/ReviewPanel.tsx
    - dashboard/components/dlq/QueueList.tsx
    - dashboard/components/dlq/dlq-actions.ts
    - dashboard/lib/dlq-api.ts
    - dashboard/mocks/handlers/dlq.ts
    - dashboard/app/dlq/page.tsx
    - dashboard/components/dlq/__tests__/ScoreBreakdownPanel.test.tsx
    - dashboard/components/dlq/__tests__/ReviewPanel.test.tsx
    - dashboard/components/dlq/__tests__/QueueList.test.tsx
    - dashboard/components/dlq/__tests__/test-utils.tsx
    - dashboard/components/ui/{button,table,badge,progress,label,card,skeleton,alert-dialog,sonner,separator,scroll-area,input}.tsx
  modified:
    - dashboard/app/layout.tsx (Toaster mounted)
    - dashboard/package.json
    - dashboard/bun.lock

decisions:
  - "D-04: TanStack useMutation with optimistic validate (onMutate row-drop) + onSettled invalidateQueries(['dlq']); edit→re-score IS the validate/reprocess path, refetch falls out of the shared key"
  - "D-05: ScoreBreakdownPanel built from custom threshold-capped bars (NOT shadcn Progress, whose indicator color is fixed to --primary) so each bar carries its own green/amber/red cap per UI-SPEC"
  - "D-06: UF filter defaults to BA/RJ/SP/SC/CE/PE; row selection scopes the batch validate; ReviewPanel kept action-agnostic for gate reuse"
  - "score_breakdown read best-effort (PT/EN key aliases + nested {value}/_value forms), defaulting each criterion to 0 so the panel always renders all five rows"

requirements-completed: [DASH-01]

# Metrics
duration: ~25min
completed: 2026-06-16
---

# Phase 4 Plan 04: DLQ Review UI (DASH-01 frontend) Summary

**The highest-value dashboard surface: a `/dlq` master-detail review queue — a state-filtered `QueueList` (BA/RJ/SP/SC/CE/PE) beside a `ReviewPanel` rendering Nascente payload + Rio normalized + the signature §7.6 `ScoreBreakdownPanel` + signals + WhatsApp log, with approve/reject/edit→re-score/reprocess and batch-by-state — all over the EXISTING dlq.py mutations through the BFF, optimistic + queue-invalidating, proven offline with 18 DLQ Vitest+MSW tests (26 total).**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 (both `type=auto`)
- **Files created:** 24 (12 dlq slice + 12 shadcn ui) · modified: 3

## Accomplishments

### Task 1 — ScoreBreakdownPanel + StatusBadge + ReviewPanel (commit `9f6b625`)
- Installed the UI-SPEC-allowlisted shadcn primitives from the **official** registry and `@tanstack/react-table@8.21.3` (pinned, `bun.lock` committed).
- **`ScoreBreakdownPanel`** — the signature §7.6 component: the five criteria (origem 30% · completude 20% · corroboração 20% · atualidade 15% · validação-humana 15%) as labeled horizontal bars, each showing weight / raw value / weighted contribution, with the total as the Display-size readout; a green/amber/red THRESHOLD cap per bar (≥85 / 51–84.9 / ≤50), mono tabular numerals. Reads the loose `score_breakdown` dict best-effort (PT/EN aliases) and renders all five rows even on partial data.
- **`StatusBadge`** — routing/sub_state badge in the semantic status colors (Mar=green, DLQ=amber, descarte=red), never a large fill.
- **`ReviewPanel`** — the action-agnostic master-detail right pane: `useQuery` against `GET /api/v1/dlq/{rio_id}` through the BFF; renders Nascente raw payload (Geist Mono JSON), Rio normalized, the ScoreBreakdownPanel, signals, and the WhatsApp/steward log; Skeleton loading, "Não foi possível carregar" + "Tentar novamente" error, "Sessão expirada ou token inválido" on 401, and a no-selection hint. Actions are injected so plan-05's gate can reuse the panel.
- **`lib/dlq-api.ts`** — `dlqKeys` (shared `['dlq']` prefix) + typed fetchers for list/detail/mutations. **`mocks/handlers/dlq.ts`** — per-view-state MSW handlers (success/empty/error/401 for list + detail; mutation + batch handlers).

### Task 2 — QueueList + dlq-actions + /dlq page (commit `8334acc`)
- **`QueueList`** — TanStack Table v8 + shadcn `table`: UF filter pinned to the BA/RJ/SP/SC/CE/PE steward-priority order, per-row checkbox selection for batch, 36px dense rows, mono `canonical_key`/`score`, `StatusBadge` per row; row click drives the ReviewPanel; batch validate gated by the "Validar {n} registros de {UF} em lote?" AlertDialog before the high-impact POST. Loading/empty/error/401 states.
- **`dlq-actions.ts`** — TanStack `useMutation` over the **existing** dlq.py endpoints (no new mutations): `validate` (optimistic `onMutate` row-drop → "Registro validado → Mar"), `descarte` (→ descarte), `reprocess`, `validate-batch`; every hook `onSettled: invalidateQueries(['dlq'])`; state-transition-explicit sonner toasts; 401 surfaces the session-expired toast.
- **`/dlq` page** — the master-detail layout (xl gap) wiring QueueList ↔ ReviewPanel, injecting the DLQ action bar ("Validar e publicar" / "Salvar e reprocessar" / "Reprocessar" / "Rejeitar" behind the "Rejeitar registro?" AlertDialog) into the otherwise action-agnostic panel. `Toaster` mounted in the layout.

## Verification

- `cd dashboard && bunx vitest run components/dlq` → **3 files, 18 tests passed** (ScoreBreakdownPanel 5, ReviewPanel 6, QueueList 7 — success/empty/error/401 for queue + panel + score breakdown, plus optimistic validate→invalidate→refetch).
- `cd dashboard && bunx vitest run` (full suite) → **5 files, 26 tests passed** (no regression to the 04-02 BFF/login tests).
- `cd dashboard && bunx tsc --noEmit` → **clean (exit 0)**.
- `cd dashboard && bunx next build` → all 5 routes compiled, including `/dlq` (static).

## Deviations from Plan

### Auto-fixed / minor adjustments

**1. [Rule 3 — Blocking] MSW base URL is `http://localhost:3000` (jsdom location)**
- **Found during:** Task 1 (ReviewPanel error-state test surfaced "rede" — fetch threw, MSW didn't match).
- **Issue:** The browser client calls a relative `/api/api/v1/...` URL which jsdom resolves against `window.location.href` = `http://localhost:3000/` (Vitest/jsdom default), not `http://localhost/`.
- **Fix:** Set the DLQ handler `BASE` to `http://localhost:3000/api/api/v1/dlq` (the BFF `/api` mount + the FastAPI `/api/v1` path = the double prefix from plan 02). No behavior change to the app.
- **Files:** `dashboard/mocks/handlers/dlq.ts`.

**2. [Rule 1 — Bug] 401 handler must cover the bare list endpoint, and row-select must not swallow row clicks**
- **Found during:** Task 2 (QueueList 401 + onSelect tests).
- **Issue (a):** `dlqUnauthorized()` only matched `${BASE}/*` (sub-paths), so the list `GET …/dlq` (no trailing segment) escaped the 401 mock. (b) The canonical_key cell wrapped the checkbox in a `<label>` with `stopPropagation` on the whole label, which also swallowed the row's `onClick` → `onSelect` never fired.
- **Fix:** `dlqUnauthorized()` now returns both `http.all(BASE)` and `http.all(${BASE}/*)` (callers spread it); the checkbox `stopPropagation` moved onto the input itself so row clicks reach `onSelect`.
- **Files:** `dashboard/mocks/handlers/dlq.ts`, `dashboard/components/dlq/QueueList.tsx`, and the two test files that spread `dlqUnauthorized()`.

All else executed as written.

## Threat Model Compliance

- **T-04-12 (Elevation of Privilege / DLQ mutation via BFF):** all mutations go through `apiFetch` → the plan-02 BFF, which attaches the operator Bearer and injects the server-held service secret; no client-side bypass — the dashboard never calls FastAPI directly.
- **T-04-13 (Tampering / batch blast radius):** the batch validate is gated by the "Validar {n} registros de {UF} em lote?" `AlertDialog` before the POST; the server still caps `limit ≤ 1000`.
- **T-04-14 (Repudiation):** accepted per plan — the existing AuditLog records each action; no new per-actor surface added.
- **T-04-SC (npm deps):** only the UI-SPEC-allowlisted official shadcn blocks + the already-planned `@tanstack/react-table` were added, pinned, `bun.lock` committed. No third-party registries.

## Known Stubs

None — the slice is fully wired to the real list (`GET /api/v1/dlq`), detail (`GET /api/v1/dlq/{id}`), and mutation endpoints through the BFF. The MSW handlers are test-only and never imported by app code. The `/dlq` page is not yet linked from a global nav shell (that shell lands in a later slice), but the route itself is complete and reachable.

## Self-Check: PASSED

All 12 created slice files + the `/dlq` page exist; both task commits (`9f6b625`, `8334acc`) present in git history; full offline suite (26 tests) + tsc + next build all green.

---
*Phase: 04-dashboard-territorial-cms*
*Completed: 2026-06-16*
