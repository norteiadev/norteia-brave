---
phase: quick-260630-jbt
plan: "01"
subsystem: brave/lanes/tripadvisor
tags: [ibge, fuzzy-match, accent-fold, ta-03, unicode]
dependency_graph:
  requires: []
  provides: [accent-fold-resolve-municipio]
  affects: [brave/lanes/tripadvisor/ibge.py, tests/unit/lanes/tripadvisor/test_ibge.py]
tech_stack:
  added: [unicodedata (stdlib)]
  patterns: [NFKD accent-fold before rapidfuzz fuzzy match]
key_files:
  modified:
    - brave/lanes/tripadvisor/ibge.py
    - tests/unit/lanes/tripadvisor/test_ibge.py
decisions:
  - "Use unicodedata.normalize('NFKD') + strip Mn category instead of unidecode (stdlib, no new dependency)"
  - "Pre-fold both query and choices before extractOne; return uf_records[index] (original accented record)"
  - "Separate PR_ROWS_CSV / _make_pr_records() from MINIMAL_CSV to preserve len==5 assertion"
metrics:
  duration: "~5 minutes"
  completed: "2026-06-30T17:12:13Z"
  tasks_completed: 2
  files_modified: 2
---

# Phase quick-260630-jbt Plan 01: Accent-fold resolve_municipio ASCII-to-IBGE fix Summary

**One-liner:** unicodedata NFKD accent-fold helper wired into resolve_municipio Step 2 so ASCII TA city names (Maringa, Carambei) score 100 against accented IBGE records (Maringá, Carambeí) instead of 85.7.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add _fold_accents helper and patch resolve_municipio Step 2 | 66e5397 | brave/lanes/tripadvisor/ibge.py |
| 2 | Add 4 accent-fold regression tests to test_ibge.py | 5e9196e | tests/unit/lanes/tripadvisor/test_ibge.py |

## What Was Built

**Task 1 — ibge.py:**
- Added `import unicodedata` to the stdlib imports block (no new package dependency).
- Added `_fold_accents(s: str) -> str` module-level helper: NFKD-decomposes the string then filters out characters in Unicode category Mn (combining marks), stripping diacritics without changing the base letters.
- Replaced Step 2 in `resolve_municipio`: now pre-folds both `name` (query) and each `r.nome` (choices) via `_fold_accents` before calling `process.extractOne`. The return is still `uf_records[index]` — the original accented record, not the folded string.
- Fixed the false module docstring claim ("handles accent-agnostic comparison") to correctly describe the explicit NFKD fold step.
- Corrected the Step 2 inline comment to document that `default_process` does NOT fold diacritics.

**Task 2 — test_ibge.py:**
- Added `PR_ROWS_CSV` constant (Curitiba, Carambeí, Maringá — 3 Paraná rows) as a separate constant from `MINIMAL_CSV`, so `TestLoadIbgeCsv::test_load_ibge_csv_from_file len(records)==5` is never disturbed.
- Added `_make_pr_records()` builder for the PR accent-fold tests.
- Added 4 test methods inside `TestResolveMunicipio`:
  - `test_ibge_accent_fold_maringa`: "Maringa" PR → nome "Maringá", ibge_code "4115200"
  - `test_ibge_accent_fold_carambei`: "Carambei" PR → nome "Carambeí", ibge_code "4104659"
  - `test_ibge_exact_match_still_works_curitiba`: "Curitiba" PR → ibge_code "4106902"
  - `test_ibge_accent_fold_no_overmatch`: "ZZZFantasia" PR → None

## Verification Results

```
218 passed, 32 warnings in 9.59s
```

Full TA unit suite: 218 passed (214 pre-existing + 4 new), 0 failed.
`_fold_accents('Maringá') == 'Maringa'` — OK
`_fold_accents('Carambeí') == 'Carambei'` — OK
`grep "accent-agnostic" ibge.py` — empty (false comment removed)

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Threat Flags

None — changes are pure in-process string transformation; no new network surface, auth paths, or file access patterns.

## Self-Check: PASSED

- [x] brave/lanes/tripadvisor/ibge.py modified and committed (66e5397)
- [x] tests/unit/lanes/tripadvisor/test_ibge.py modified and committed (5e9196e)
- [x] 66e5397 exists in git log
- [x] 5e9196e exists in git log
- [x] 218 TA unit tests passed
- [x] MINIMAL_CSV and len==5 assertion untouched
- [x] unicodedata is the only new import (stdlib)
- [x] "accent-agnostic" claim removed from ibge.py
