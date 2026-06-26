---
phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
plan: 07
subsystem: tripadvisor-lane
tags: [tripadvisor, bulk-sweep, pagination, resume, fail-fast, celery]
requires:
  - "brave/lanes/tripadvisor/atrativos.py::produce_paginated (15-06)"
  - "brave/lanes/tripadvisor/sweep_progress.py (15-03)"
  - "brave/tasks/pipeline.py::sweep_tripadvisor fail-fast block (12-04)"
provides:
  - "sweep_tripadvisor bulk_national branch (geoId 294280, resume + progress + guarded fail-fast)"
  - "scripts/ta_bulk_sweep.py operator slice trigger (--start-page/--max-pages/--enqueue)"
affects:
  - "brave/tasks/pipeline.py"
  - "scripts/ta_bulk_sweep.py"
  - "tests/unit/tasks/test_sweep_tripadvisor.py"
tech-stack:
  added: []
  patterns:
    - "Distinct bulk branch reuses verbatim client/geocoder selection + shared fail-fast except"
    - "rc=None before try + guarded `if rc is not None` keeps the shared except UnboundLocalError-free"
    - "Resume computed from sweep_progress before start(): offset//30 + 2 (page AFTER last completed)"
key-files:
  created:
    - "scripts/ta_bulk_sweep.py"
  modified:
    - "brave/tasks/pipeline.py"
    - "tests/unit/tasks/test_sweep_tripadvisor.py"
decisions:
  - "Resume precedence over operator --start-page: when pages_done>0, the branch computes start_page from the resume offset and ignores the passed start_page (re-injection happy path)."
  - "pages_total seeded as 334 (full national) even for a slice — the panel always shows progress against the whole run."
  - "Bulk uf placeholder 'BR' in the CLI: the bulk lane derives UF per-attraction from the geocode; the task uf arg only feeds logging/quarantine payloads."
requirements: [TA-12]
metrics:
  duration: "~25m"
  completed: "2026-06-26"
  tasks: 2
  files: 3
---

# Phase 15 Plan 07: Bulk-national sweep branch + resume + operator slice CLI Summary

Added a DISTINCT `bulk_national` branch to `sweep_tripadvisor` that paginates the all-Brazil
AttractionsFusion listing (geoId 294280) through `produce_paginated`, reads the resume offset to
continue from the page after the last completed offset, seeds/finishes the live progress hash,
and on a mid-run 403/429 reuses the shared fail-fast block plus a GUARDED
`sweep_progress.stop_needs_bootstrap` — fixing the MEDIUM-2 UnboundLocalError risk on the shared
except. A thin operator CLI (`scripts/ta_bulk_sweep.py`) triggers a small slice (or the full run)
end-to-end. The per-UF destinos+atrativos path is byte-for-byte unchanged.

## What Was Built

### Task 1 — bulk branch + operator slice trigger (`feat`, commit 7c05ff1)
- `sweep_tripadvisor` signature extended with keyword-only `bulk_national=False`, `start_page=1`,
  `max_pages=None`, `geo_id=294280`. The per-UF call shape (`uf`, `depth`) is unchanged.
- `rc = None` initialized before the `try`; only the bulk branch assigns it (the sync Redis client,
  obtained the same way `_mark_needs_bootstrap` does).
- Bulk branch (taken before the destinos producer): reuses the verbatim client + geocoder selection
  (`run_real_externals` Null-vs-real), reads resume state, calls `sweep_progress.start(pages_total=334)`,
  builds `TripAdvisorAtrativosIngest(..., destino_rio_map=None)`, runs
  `asyncio.run(produce_paginated(geo_id, start_page, max_pages or 334, rc, run_rio=run_rio))`,
  then `mark_done` + terminal `commit`, then `return` (per-UF code never runs).
- Resume: `pages_done>0` → `start_page = get_resume_offset()//30 + 2`; else the operator `start_page`.
- Shared fail-fast except gains one GUARDED line: `if rc is not None: sweep_progress.stop_needs_bootstrap(rc)`.
  Rollback / `_mark_needs_bootstrap` / error-class-only log / no-retry-no-quarantine are unchanged.
- `scripts/ta_bulk_sweep.py`: argparse CLI with `--start-page` / `--max-pages` / `--geo-id` / `--depth`
  / `--enqueue`; default `--max-pages 5` (slice-first cap); lazy task import so `--help` exits 0 without
  loading Celery; logs only page range / counts / mode — never session material.

### Task 2 — resume + fail-fast + done-state tests (`test`, commit 4398c34)
- Happy path: 2 pages × 30 cards → `state="done"`, `pages_done=2`, `attractions_ingested=60`, 60 store_raw rows.
- Mid-run `SessionExpiredError` (page 2): `state="stopped_needs_bootstrap"`, needs_bootstrap marker set,
  page-1 record durable (per-page commit), no retry.
- Resume: pre-seeded `last_completed_offset=30` → `produce_paginated` invoked with `start_page==3`.
- Per-UF regression: `bulk_national=False` `SessionExpiredError` returns cleanly with `rc=None` — no
  UnboundLocalError, and the bulk progress hash is never written (`state` stays `idle`), proving the guard.

## Deviations from Plan

None — plan executed as written. The TDD task (Task 2) was authored against the implementation already
landed in Task 1; all four behaviours specified in `<behavior>` are asserted and green.

One pre-existing unused import (`pytest`) in the test file was removed while editing its import block
(now used by no test in the file). Pre-existing ruff findings outside the changed code (N806/E402/I001 in
unrelated blocks of `pipeline.py`, SIM105 in the unchanged per-UF helper) were left untouched (out of scope).

## Verification

- `pytest tests/unit/tasks/test_sweep_tripadvisor.py` → 9 passed (RUN_REAL_EXTERNALS unset, fakeredis + fakes).
- `pytest tests/unit/lanes/tripadvisor/test_atrativos_bulk.py` (dependency, 15-06) → still green (17 total with task tests).
- `python scripts/ta_bulk_sweep.py --help` → exits 0, documents `--start-page` / `--max-pages`.
- Acceptance greps: `bulk_national`, `produce_paginated`, `rc = None`, `if rc is not None` all present.
- Bulk-branch + CLI source carries no cookie/user_agent/session_id/datadome/proxy logging.

## Known Stubs

None. The offline default (`RUN_REAL_EXTERNALS` unset) uses `NullTripAdvisorClient` (yields nothing) and
`NullGeocoderClient` by design — the real slice runs are the operator Level-3 manual-only verification, as
the plan specifies. Full-run geocode-resilience batching remains deferred per 15-CONTEXT (not built here).

## Self-Check: PASSED
- brave/tasks/pipeline.py — FOUND (bulk branch + guarded fail-fast)
- scripts/ta_bulk_sweep.py — FOUND
- tests/unit/tasks/test_sweep_tripadvisor.py — FOUND (9 tests green)
- commit 7c05ff1 — FOUND (feat: bulk branch + CLI)
- commit 4398c34 — FOUND (test: resume/fail-fast/regression)
