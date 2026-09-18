---
phase: quick-260918-rh7
plan: 01
subsystem: painel / atrativos promotion
tags: [mar, dlq, painel, bulk-promote]
key-files:
  created:
    - tests/unit/test_atrativos_promote_bulk.py
    - dashboard/components/painel/__tests__/PainelPromoverLote.test.tsx
  modified:
    - brave/api/routers/atrativos.py
    - dashboard/lib/atrativos-api.ts
    - dashboard/mocks/handlers/atrativos.ts
    - dashboard/components/painel/PainelFilters.tsx
    - dashboard/components/painel/PainelView.tsx
    - dashboard/components/painel/__tests__/PainelFilters.test.tsx
decisions:
  - Bulk stamp of validacao_humana=100 is accepted as human validation (user decision, item 1)
  - norteia-api ingest throttle is out of scope; mitigation is operational (item 2)
completed: 2026-09-18
---

# Quick 260918-rh7: Bulk promote DLQ atrativos from the Painel

`POST /api/v1/atrativos/promote-bulk` (dry-run count, then per-record isolated promote
through the unchanged `validate_and_promote_rio` gate) plus a "Promover em lote" Painel
button that runs dry-run -> `window.confirm` -> real run scoped to the board's UF filter.

## What shipped

**Backend (`brave/api/routers/atrativos.py`)**
- `PromoteBulkBody` (`extra="forbid"`, `limit` 1-200, `dry_run` defaults to true).
- `_query_promote_bulk_candidates`: attraction + routing=dlq (+ UF), score desc, no SQL limit.
- `_bucket_promote_bulk_candidates`: pure; below_score -> no_description -> recency
  (reuses `_attraction_review_recent`), one bucket per row.
- `promote_bulk_atrativos`: same two dependencies, same order, as `transition_atrativo`.
  Dry-run never commits/audits/pushes. Real run: config loaded once, one commit per
  record, a raise rolls back only that record, one audit row per promoted/held record
  with `batch_id` + `actor="steward"`, `push_attraction_task.delay` per promoted record
  after the loop, dispatch failure collected in `push_failed` and never raised.

**Dashboard**
- `promoteBulkAtrativos` + request/result types; three MSW factories (not in the barrel).
- `PainelFilters`: presentational button (`onPromoverLote`, `promoverLoteDisabled`).
- `PainelView`: owns the flow, toasts, invalidates `["destinos"]`, `["atrativos"]`,
  `["engine","status"]`; 423 reuses the existing edit-lock copy.

No migration, no new Celery task, no new dependency, threshold untouched, description
lane untouched.

## Commits

- b27b9d5 docs(quick-260918-rh7): plan bulk promote
- 48e547b feat(quick-260918-rh7): bulk promote endpoint for DLQ atrativos
- a2b8b44 feat(quick-260918-rh7): Painel bulk promote control

## Tests (as observed)

| Command | Result |
|---|---|
| `env -u RUN_REAL_EXTERNALS -u BRAVE_DB_URL BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/unit/test_atrativos_promote_bulk.py -q` | 17 passed |
| `env -u RUN_REAL_EXTERNALS -u BRAVE_DB_URL BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/unit -o addopts="" -W ignore` | 1178 passed, 0 failed (62.56s) |
| `cd dashboard && bun run test -- PainelPromoverLote` | 5 passed |
| `cd dashboard && bun run test` | 28 files, 227 passed, 0 failed |
| `cd dashboard && bun run typecheck` | exit 0 |
| `bunx eslint` on the 6 touched dashboard files | exit 0 |
| `ruff check` on the 2 touched Python files | pass |
| `ruff format --check tests/unit/test_atrativos_promote_bulk.py` | pass |
| `ruff format --check brave/api/routers/atrativos.py` | FAILS, pre-existing (see below) |

`tests/integration` was never run; `BRAVE_DB_URL` was never set. RED was observed for
both tasks before implementation (ImportError on the backend; 5/5 failing on vitest).
The project's `addopts = "-q"` plus `-q` suppresses pytest's summary line, so the full
run used `-o addopts=""` to get a real count.

## Deviations from Plan

**1. [Rule 3] Existing `PainelFilters.test.tsx` updated**
The new required `onPromoverLote` prop broke `tsc --noEmit` on the existing test's
`render(...)`. Passed `onPromoverLote={vi.fn()}` there. File not in the plan's list.

**2. Held/promoted audit merged into one `write_audit` call**
The plan spells out two branches; the code builds `action`/`after_state` from
`rio.routing == "mar"` and calls `write_audit` once. Same rows, fewer lines.

**3. `db.get` sits inside the per-record `try`**
So a DB error on the fetch is isolated like any other per-record failure. A per-record
failure also logs `atrativo_promote_bulk_record_failed` (not in the plan).

**4. `promoteBulkZeroCandidates` delegates to `promoteBulkDryRunSuccess(overrides)`**
instead of duplicating the handler body.

**5. Full-flow test swaps the MSW handler inside the `window.confirm` mock**
(`server.use(promoteBulkRunSuccess())` right when the steward confirms) — the plan left
the sequencing technique open. Invalidation is asserted through the observable effect
(the atrativos list is refetched), since `renderWithClient` does not expose the client.

## Pre-existing issue (not fixed)

`ruff format --check brave/api/routers/atrativos.py` fails on `main` already: the four
hand-aligned trailing comments in `_ATRATIVO_ALLOWED_EDGES`. The plan says not to touch
that dict, so the alignment was kept byte-identical; `ruff format --diff` shows those
four lines as the only pending change.

## Open items for the user

- Item 2 of the plan stays open: nothing in this repo can tell whether norteia-api
  throttles a burst of up to 200 queued pushes. Start with a small `limit`.
- Nothing here was exercised against a live stack (no containers touched, no DB).
  First real use should be a dry-run on one UF.
- The button sends only `uf` + `dry_run`; `min_score` (65), `require_description`
  (true) and `limit` (200) are server defaults with no UI control yet.

## Self-Check: PASSED

All 8 files exist on disk; commits b27b9d5, 48e547b, a2b8b44 are in `git log`.
