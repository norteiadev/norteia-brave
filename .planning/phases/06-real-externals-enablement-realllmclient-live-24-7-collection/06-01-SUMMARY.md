---
phase: "06"
plan: "01"
subsystem: "clients"
tags: ["docfix", "d-06", "env-var", "footgun"]
dependency_graph:
  requires: []
  provides: ["correct-env-var-docs-rfe"]
  affects: ["brave/clients/places.py", "brave/clients/apify.py", "brave/clients/whatsapp.py", "tests/integration/test_atrativos_lane_e2e.py"]
tech_stack:
  added: []
  patterns: []
key_files:
  created: []
  modified:
    - brave/clients/places.py
    - brave/clients/apify.py
    - brave/clients/whatsapp.py
    - tests/integration/test_atrativos_lane_e2e.py
decisions:
  - "D-06 confirmed: env var is bare RUN_REAL_EXTERNALS (AppConfig env_prefix=''), not BRAVE_RUN_REAL_EXTERNALS"
metrics:
  duration: "~2min"
  completed: "2026-06-18"
  tasks_completed: 1
  files_modified: 4
---

# Phase 6 Plan 01: D-06 Footgun Fix (BRAVE_RUN_REAL_EXTERNALS → RUN_REAL_EXTERNALS) Summary

**One-liner:** Replace 7 wrong `BRAVE_RUN_REAL_EXTERNALS` occurrences in client error strings and docstrings with the correct bare `RUN_REAL_EXTERNALS` name as used by AppConfig (env_prefix="").

## Objective

Eliminate the D-06 operator footgun: docs/error strings across three client files and one integration test docstring incorrectly said `BRAVE_RUN_REAL_EXTERNALS`. The real env var is `RUN_REAL_EXTERNALS` (AppConfig uses `env_prefix=""`, so Pydantic reads the bare name). An operator following the wrong name silently gets fake clients even when intending to run real externals.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Fix BRAVE_RUN_REAL_EXTERNALS → RUN_REAL_EXTERNALS in all 7 occurrences | d99fe43 | brave/clients/places.py, brave/clients/apify.py, brave/clients/whatsapp.py, tests/integration/test_atrativos_lane_e2e.py |

## Changes Made

All 7 edits are string/docstring-only. No logic changed:

| File | Location | Old | New |
|------|----------|-----|-----|
| brave/clients/places.py | line 95 (RuntimeError message) | `BRAVE_RUN_REAL_EXTERNALS` | `RUN_REAL_EXTERNALS` |
| brave/clients/apify.py | line 89 (RuntimeError message) | `BRAVE_RUN_REAL_EXTERNALS` | `RUN_REAL_EXTERNALS` |
| brave/clients/whatsapp.py | line 13 (module docstring) | `requires BRAVE_RUN_REAL_EXTERNALS=true` | `requires RUN_REAL_EXTERNALS=true` |
| brave/clients/whatsapp.py | line 66 (class docstring) | `Requires BRAVE_RUN_REAL_EXTERNALS=true` | `Requires RUN_REAL_EXTERNALS=true` |
| brave/clients/whatsapp.py | line 121 (raises docstring) | `BRAVE_RUN_REAL_EXTERNALS is not True` | `RUN_REAL_EXTERNALS is not True` |
| brave/clients/whatsapp.py | line 129 (RuntimeError message) | `BRAVE_RUN_REAL_EXTERNALS=true` | `RUN_REAL_EXTERNALS=true` |
| tests/integration/test_atrativos_lane_e2e.py | line 7 (module docstring) | `BRAVE_RUN_REAL_EXTERNALS must be absent` | `RUN_REAL_EXTERNALS must be absent` |

## Verification

- `grep -r BRAVE_RUN_REAL_EXTERNALS brave/ tests/` returns **0 matches**
- `tests/integration/test_atrativos_lane_e2e.py` passes (8/8)
- Full offline suite: **388 passed, 0 failures** (21.99s)

## Deviations from Plan

None — plan executed exactly as written.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. Edits are string-only in docstrings and error messages.

## Self-Check: PASSED

- [x] d99fe43 exists in git log
- [x] `grep -r BRAVE_RUN_REAL_EXTERNALS brave/ tests/` returns 0
- [x] brave/clients/places.py contains "Set RUN_REAL_EXTERNALS=true to enable real API calls."
- [x] brave/clients/apify.py contains "Set RUN_REAL_EXTERNALS=true to enable real API calls."
- [x] brave/clients/whatsapp.py contains "requires RUN_REAL_EXTERNALS=true"
- [x] brave/clients/whatsapp.py contains "Requires RUN_REAL_EXTERNALS=true"
- [x] brave/clients/whatsapp.py contains "TwilioWhatsAppClient.send_template requires RUN_REAL_EXTERNALS=true."
- [x] tests/integration/test_atrativos_lane_e2e.py contains "RUN_REAL_EXTERNALS must be absent / False."
- [x] 388 passed, 0 failures (full offline suite)
