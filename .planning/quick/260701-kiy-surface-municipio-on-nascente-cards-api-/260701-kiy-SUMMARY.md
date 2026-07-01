---
phase: quick-260701-kiy
plan: 01
subsystem: painel-dashboard + nascente-api
tags: [nascente, painel, kanban, lgpd, windowing, municipio]
requires:
  - "payload.canonical.municipio + payload.municipio_id (resolved at ingest)"
provides:
  - "GET /api/v1/nascente item shape includes municipio + municipio_id (LGPD allow-list)"
  - "Nascente board cards display município (UF-only fallback preserved)"
  - "Per-column client-side render windowing (100 initial, +50 per scroll)"
affects:
  - brave/api/routers/engine.py
  - dashboard/lib/nascente-api.ts
  - dashboard/lib/painel-data.ts
  - dashboard/components/painel/PainelBoard.tsx
tech-stack:
  added: []
  patterns:
    - "Pure DB-free projection helper (_project_nascente_item) for offline unit testability"
    - "IntersectionObserver sentinel for per-column lazy render windowing"
key-files:
  created:
    - tests/unit/api/test_nascente_projection.py
  modified:
    - brave/api/routers/engine.py
    - dashboard/lib/nascente-api.ts
    - dashboard/lib/painel-data.ts
    - dashboard/lib/__tests__/painel-data.test.ts
    - dashboard/components/painel/__tests__/RecordCard.test.tsx
    - dashboard/components/painel/PainelBoard.tsx
    - dashboard/components/painel/__tests__/PainelBoard.test.tsx
decisions:
  - "Committed the backend projection unit test with its implementation (Task 1) as the RED/GREEN pair, keeping the backend change atomic; dashboard tests committed under Task 3 as planned."
  - "Render-window reset key is [column.key, column.cards.length] — length is the simple observable proxy for a data/filter change (no deep compare)."
  - "IntersectionObserver effect deps are [hasMore, total] (not visibleCount) so one observer persists across successive reveals rather than being recreated each step."
metrics:
  duration: ~20m
  completed: 2026-07-01
  tasks: 4
  files: 7
---

# Quick 260701-kiy Plan 01: Surface Município on Nascente Cards + Kanban Render Windowing Summary

Surfaced the já-resolved município (nome + IBGE id) end-to-end on Nascente board cards behind the LGPD field allow-list, and added client-side per-column render windowing (100 cards initial, +50 revealed per scroll-to-bottom via an IntersectionObserver sentinel) so heavy Kanban columns mount fast — display-only, no ingest/pipeline/endpoint changes.

## What Was Built

**Task 1 — Nascente API projection (e2de0fb, TDD).** Extracted the inline item dict in `list_nascente` into a module-level pure helper `_project_nascente_item(rec) -> dict`, added `municipio` (`payload.canonical.municipio`) and `municipio_id` (`payload.municipio_id`) to the LGPD allow-list, both null-safe (None payload / None canonical / absent field never raises). Updated the LGPD docstring to record municipio + municipio_id as APPROVED PUBLIC-GEO fields (not PII, same class as name/uf). Backing offline unit test (`tests/unit/api/test_nascente_projection.py`, 7 cases: TA atrativo, Mtur destino, missing canonical, missing municipio_id, empty payload, None payload, None canonical) — no DB, no respx, no network.

**Task 2 — Frontend card model (352a229).** Added `municipio` + `municipio_id` to `NascenteListItem` (flows automatically via `apiFetch` → TanStack passthrough). In `toPainelCards`, nascente cards now set `municipality: n.municipio` (mirroring destino's `municipalityFromCanonicalKey`). `RecordCard` already renders `card.municipality` next to the UF chip and hides it when null — no component change needed.

**Task 3 — Tests (14ffb99).** painel-data: nascente fixture now carries município; asserts `PainelCard.municipality` equals `n.municipio` (and stays null when the item's municipio is null). RecordCard: município renders next to the UF chip when set; UF-only (no município text) when null.

**Task 4 — Per-column render windowing (34dc1ea).** Extracted the inlined column body into a `PainelColumn` sub-component owning its own `visibleCount` (init 100). Renders `column.cards.slice(0, visibleCount)`; a bottom sentinel div (`data-testid="painel-col-sentinel-{key}"`) with an IntersectionObserver (root = the `overflow-y-auto` scroll body) bumps `visibleCount` by 50 (capped at total) on intersect. Window resets to 100 on `[column.key, column.cards.length]` change. Count pill still shows the true total (`column.cards.length`), never the window size. All cards stay in memory (display windowing only). Existing drop handlers, data-testids, count pill, and `isPending` "Carregando…" branch preserved.

## Verification

- Backend full unit suite: `BRAVE_USE_FAKEREDIS=1 env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest tests/unit -q ...` — exit 0, all pass (incl. 7 new projection cases).
- Dashboard full suite: `bun run test` (vitest run) — 43 files, 301 tests pass (incl. painel-data município, RecordCard município x2, PainelBoard windowing x4).
- No network hit by any test. No changes under brave/lanes, brave/core pipeline, Rio/Mar, or new routes.

## Environment Note

The worktree had no `dashboard/node_modules`; a temporary symlink to the main repo's install was used to run vitest, then removed. Working tree left clean.

## Deviations from Plan

**1. [Task attribution] Backend projection test committed with its implementation (Task 1) rather than under Task 3.**
- The plan lists `tests/unit/api/test_nascente_projection.py` under Task 3's files, but Task 1 is `tdd="true"` and its `<verify>` runs that exact test. To honor test-first TDD and keep the backend change atomic, the RED/GREEN pair (helper + its test) was committed together in Task 1 (e2de0fb). Task 3 then covers only the dashboard tests. No behavior difference; all planned tests exist and pass.

Otherwise the plan executed as written. No auth gates. No Rule 1/2/3 auto-fixes required.

## Known Stubs

None — the município data path is fully wired (ingest → API allow-list → card model → render). The plan intentionally leaves destino/atrativo LIST-endpoint município out of scope; only the Nascente lane surfaces município this plan (as specified).

## Self-Check: PASSED

All 5 key files present; all 4 task commits (e2de0fb, 352a229, 14ffb99, 34dc1ea) exist in the git log.
