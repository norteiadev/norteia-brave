---
phase: 09-close-gap-int-blocker-01-null-places-llm-apify-clients-for-o
plan: 01
subsystem: testing
tags: [null-clients, pipeline, celery, offline-testing, packaging, protocol]

# Dependency graph
requires:
  - phase: 03-atrativos-lane
    provides: brave/tasks/pipeline.py with the 8 offline-branch import sites that imported tests.fakes.*
  - phase: 01-brave-core-score-gate-boundary-contract
    provides: brave/clients/base.py Protocol interfaces that Null clients must satisfy
provides:
  - NullPlacesClient (brave/clients/null_places.py) — production-safe PlacesClientProtocol stub
  - NullLLMClient (brave/clients/null_llm.py) — production-safe LLMClientProtocol stub
  - NullApifyClient (brave/clients/null_apify.py) — production-safe ApifyClientProtocol stub
  - Rewired pipeline.py: 8 offline-branch sites use Null clients (no tests-tree import)
  - Regression guard: tests/unit/test_no_test_imports_in_brave.py
affects:
  - Any future brave/ module that adds a run_real_externals=False else-branch (must use Null clients, not tests.fakes)
  - CI: full offline suite now green with RUN_REAL_EXTERNALS unset

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "In-package Null client pattern: brave/clients/null_*.py mirrors null_mtur.py shape (docstring, no-op methods, _check_protocol_compliance())"
    - "Regression guard uses compiled regex anchored to import statements — not bare substring — to allow docstrings/comments mentioning tests/"

key-files:
  created:
    - brave/clients/null_places.py
    - brave/clients/null_llm.py
    - brave/clients/null_apify.py
    - tests/unit/test_no_test_imports_in_brave.py
  modified:
    - brave/tasks/pipeline.py

key-decisions:
  - "Null clients live in brave/ (not tests/) — production wheel ships only packages=['brave']; test fakes stay in tests/fakes/ for call-recording assertions in unit tests"
  - "Regression guard uses import-statement-aware regex (^\\s*(from|import)\\s+tests\\b) not bare substring to avoid false-positives on Null client docstrings that legitimately mention tests/fakes"
  - "Null client generate() returns exact FakeLLMClient default generate_result string for consistent offline behaviour across test and production-offline code paths"

patterns-established:
  - "Null client shape: from __future__ import annotations; from typing import Any; module docstring noting production-safety + NOT tests/ provenance; class with async protocol methods returning empty/no-op; _check_protocol_compliance() at module bottom"
  - "All run_real_externals=False else-branches in pipeline.py instantiate in-package Null clients, never tests.fakes.*"

requirements-completed:
  - ORCH-01
  - ORCH-02
  - ORCH-03
  - ORCH-04
  - ATR-01
  - ATR-02
  - ATR-03
  - ATR-04
  - DEST-01
  - DEST-02
  - DEST-03
  - DEST-04
  - DEST-05
  - CORE-10
  - CORE-11
  - TEST-03

# Metrics
duration: 8min
completed: 2026-06-19
---

# Phase 09 Plan 01: Close INT-BLOCKER-01 (Null clients) Summary

**Three in-package Null clients (NullPlacesClient, NullLLMClient, NullApifyClient) replace tests.fakes.* imports in all 8 offline-branch sites in pipeline.py, fixing ModuleNotFoundError under the default production config where the tests tree is not shipped.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-06-19T15:13:00Z
- **Completed:** 2026-06-19T15:20:58Z
- **Tasks:** 3
- **Files modified:** 5

## Accomplishments

- Created `brave/clients/null_places.py`, `null_llm.py`, `null_apify.py` — each satisfies its protocol via `_check_protocol_compliance()`, performs zero network I/O, and returns the behavior-preserving empty/no-op defaults
- Rewired all 8 offline-branch import sites in `brave/tasks/pipeline.py` (3x Places, 4x LLM, 1x Apify) from `tests.fakes.*` to the new in-package Null clients; real-client branches untouched
- Added `tests/unit/test_no_test_imports_in_brave.py` — import-statement-aware regression guard (compiled regex, not substring) that durably prevents reintroduction of the packaging break
- Full offline suite: 438 passed, 1 skipped with `RUN_REAL_EXTERNALS` unset — no ModuleNotFoundError

## Task Commits

1. **Task 1: Create the three in-package Null clients** — `e649b02` (feat)
2. **Task 2: Rewire the 8 offline-branch import sites in pipeline.py** — `bba889d` (fix)
3. **Task 3: Add regression-guard test + run the offline suite** — `27c4620` (test)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `brave/clients/null_places.py` — NullPlacesClient: text_search->[], place_details->{}
- `brave/clients/null_llm.py` — NullLLMClient: extract->None, generate->canned PT-BR string
- `brave/clients/null_apify.py` — NullApifyClient: scrape_ig->{}
- `brave/tasks/pipeline.py` — 8 offline-branch import sites rewired to Null clients (tests.fakes removed from brave/)
- `tests/unit/test_no_test_imports_in_brave.py` — regression guard: walks brave/**/*.py, fails on any `(from|import)\s+tests\b` import statement

## Decisions Made

- Null clients mirror `null_mtur.py` exactly (canonical pattern already established in the codebase) — no new structural patterns introduced
- `NullLLMClient.generate()` returns `"Olá! Da Norteia. Poderia confirmar mais detalhes?"` verbatim from `FakeLLMClient`'s default `generate_result` — preserves offline branch consistency between test and production-offline paths
- Pipeline.py comment at line 1069 (`# FakeLLMClient/FakeWhatsApp are test-only (tests/fakes/).`) was left as-is per plan instructions — prose in comments is not an import statement and must not be rewritten

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

- `.venv/bin/python` not available in worktree working directory (worktree shares the main repo's `.venv`); used absolute path `/Users/leandro/Projects/norteia/norteia-brave/.venv/bin/python`. All `env -u RUN_REAL_EXTERNALS` verify commands succeeded.

## User Setup Required

None — no external service configuration required. This is a bounded offline fix; no new env vars or infrastructure.

## Next Phase Readiness

- INT-BLOCKER-01 is closed: the default Celery task config (`RUN_REAL_EXTERNALS` unset/false) no longer raises `ModuleNotFoundError` for sweep/discovery/FSM tasks
- Regression guard in place — any future `brave/` file that reintroduces a `tests` import statement will fail CI immediately
- Full offline suite green (438 passed, 1 skipped) with `RUN_REAL_EXTERNALS` unset

---
*Phase: 09-close-gap-int-blocker-01-null-places-llm-apify-clients-for-o*
*Completed: 2026-06-19*
