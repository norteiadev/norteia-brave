---
phase: 02-destinos-lane
plan: "09"
status: complete
completed: 2026-06-12
requirements: [DEST-01, DEST-02, DEST-03, DEST-04, DEST-05, TEST-02]
---

# 02-09 Summary — Destinos Lane End-to-End Acceptance Gate

## What was built

`tests/integration/test_destinos_lane.py` is the phase acceptance gate — the end-to-end
proof that a Destinos record flows seed → Nascente → Rio/score → DLQ → steward validate →
Mar → push (faked norteia-api), fully offline.

The four plan-specified behaviors are covered. Three were already authored by upstream plans
(02-06 wrote the validate / validate-batch endpoint tests; 02-07 wrote the corroboração-boost
test) into the same file. This plan added the missing headline test:

- **`test_mtur_lane_end_to_end`** (new, this plan) — `MturSeedIngest.produce('BA')` with a
  `FakeMturClient` (Porto Seguro, Oferta Principal). Asserts a `NascenteRecord` with
  `source='mtur'`, `origem_value=100`, `canonical.ibge_code='2927408'` is written, and the
  Rio record lands in `routing='dlq'` by default (cold start, no human validation — DEST-01,
  DEST-04). Score math: 30 + completude·0.2 + 10.5 ∈ [40.5, 85) → always `dlq`, never
  descarte, never unaided Mar.

Pre-existing in the file (verified passing as part of the acceptance gate):
- `test_validate_endpoint_promotes_to_mar_with_corroboration` — DB round-trip proving
  `flag_modified` persisted `validacao_humana_value=100.0` and routing crossed to `mar`
  (DEST-05, D-07).
- `test_validate_batch_returns_202` + batch audit/limit tests — batch-by-state validate (D-08).
- `test_notebooklm_corroboration_boosts_mtur` — DB-level proof the IBGE-exact-match boost
  raises `corroboracao_value` to 50.0 (DEST-02, D-02) — the load-bearing mechanism that lets
  a validated Mtur record cross the Mar threshold.
- `test_validate_endpoint_404_for_unknown_rio_id`, `..._stays_dlq_without_corroboration`,
  `..._writes_audit_row` — edge/audit coverage.

## Test results

- `tests/integration/test_destinos_lane.py`: 11 integration tests pass (docker-compose
  Postgres+Redis up).
- Full suite (`uv run pytest -q`): green — no Phase 1 regressions.
- `ruff check` + `ruff format --check`: clean.

## Key files

- `tests/integration/test_destinos_lane.py` — +`test_mtur_lane_end_to_end`

## Notes / deviations

- The plan assumed this plan would author all four tests in a fresh file; in practice 02-06
  and 02-07 had already created the file with their endpoint/boost tests (the plan's own
  env-note anticipated this and instructed "do NOT duplicate"). This plan added only the
  missing `test_mtur_lane_end_to_end` headline case, satisfying the `contains:
  test_mtur_lane_end_to_end` artifact requirement and the full DEST-01..05 + TEST-02 gate.
- All external dependencies (Mtur/NotebookLM/OpenRouter/norteia-api) are faked — offline,
  keyless, per the CLAUDE.md test mandate.
- Executed inline by the orchestrator after the assigned executor agent hit a session limit
  mid-run (it had committed nothing; clean working tree).
