---
phase: 02-destinos-lane
plan: "01"
subsystem: contract
tags: [pact, contract, ibge, municipality, d-10, breaking-change]
dependency_graph:
  requires: [01-03]
  provides: [updated-pact-contract-with-ibge-code]
  affects: [norteia-api-provider-verification]
tech_stack:
  added: []
  patterns: [pact-consumer-contract, ibge-passthrough]
key_files:
  modified:
    - tests/contract/test_pact_norteia_api.py
  generated_at_test_runtime:
    - tests/contract/pacts/norteia-brave-norteia-api.json
decisions:
  - "D-10 IBGE passthrough: ibge_code added to canonical dict in Pact contract; source_ref updated to use IBGE code format mtur:{uf}:{ibge_code}"
  - "Breaking Pact contract change: coordinate with norteia-api Laravel team (Trilha 5) to update provider verification once"
  - "source_ref format standardized to mtur:BA:2927408 (IBGE code replaces sequential id)"
metrics:
  duration_minutes: 3
  completed_date: "2026-06-12"
  tasks_completed: 1
  files_modified: 1
---

# Phase 2 Plan 01: Pact Contract ibge_code Update Summary

Pact DESTINATION_PAYLOAD updated with `ibge_code: "2927408"` in the canonical dict, satisfying D-10 (IBGE passthrough to norteia-api for IBGE→municipality_id resolution). source_ref format standardized to use IBGE code.

## What Was Built

Updated the frozen Phase 1 Pact consumer contract to carry `ibge_code` inside the `canonical` dict of every destination push payload. This is a breaking contract change — norteia-api previously had only a name string (`municipio: "Porto Seguro"`) to identify a municipality, which is ambiguous for Brazilian municipalities (multiple "Santa Cruz" exist across states).

**Changes made to `tests/contract/test_pact_norteia_api.py`:**
- Added `"ibge_code": "2927408"` to `DESTINATION_PAYLOAD.canonical`
- Updated `source_ref` from `"mtur:BA:123"` to `"mtur:BA:2927408"` (IBGE code in source_ref matches canonical.ibge_code — consistent format per RESEARCH.md Pattern 2)
- Updated response body assertions in both destination test interactions to match new source_ref
- Added module-level coordination comment above `DESTINATION_PAYLOAD` documenting the breaking change and flagging the norteia-api Laravel team (Trilha 5) coordination requirement
- All 4 Pact contract tests pass (3 mock-server interactions + 1 file-validation test)
- Pact JSON artefact regenerated at test runtime with `ibge_code` in canonical

## Key Decisions Made

| Decision | Rationale |
|----------|-----------|
| `ibge_code` in `canonical` dict (not top-level) | Keeps municipality metadata co-located; canonical is the authoritative identifier dict; matches how norteia-api would consume it |
| source_ref format: `mtur:{uf}:{ibge_code}` | IBGE code is the canonical municipality ID in Brazil — more stable and unique than sequential integers; consistent with D-10 and RESEARCH.md Pattern 2 |
| Breaking change in plan 02-01 (Wave 0) | Cheapest moment: before any real push is built; the Laravel team needs to update provider verification exactly once; deferred would mean regression in an already-shipping path |

## Deviations from Plan

None — plan executed exactly as written.

The plan specified updating `source_ref` to `"mtur:BA:2927408"` and that is what was implemented. Both destination test interactions (test_push_destination_contract and test_push_destination_idempotent_contract) use the shared DESTINATION_PAYLOAD constant, so both automatically received the update.

## Verification Results

```
4 passed in 0.19s
PASS: ibge_code in pact artefact
```

- DESTINATION_PAYLOAD.canonical has `"ibge_code"` key: PASS
- All 4 Pact tests pass (pytest exit 0): PASS
- tests/contract/pacts/norteia-brave-norteia-api.json contains "ibge_code": PASS (2 matches, one per destination interaction)
- Module-level coordination comment present: PASS
- NorteiaApiClient unchanged: PASS
- _build_push_payload unchanged: PASS

## Coordination Note

The norteia-api Laravel team (Trilha 5) must update their Pact **provider** verification to accept `canonical.ibge_code` (string, 7-digit IBGE code) and update their ingestion endpoint to use `ibge_code` for IBGE→`municipality_id` resolution instead of attempting name-based lookup. This is the single breaking Pact change for Phase 2.

## Known Stubs

None — this plan contains no stub data. The IBGE code `"2927408"` is the real IBGE code for Porto Seguro, BA, Brazil (consistent with Trancoso being a district/locality within Porto Seguro).

## Threat Flags

None — `ibge_code` is public Brazilian government open data (IBGE), no PII, no secrets. The Pact JSON artefact is gitignored (generated at test runtime) and only the test fixture is committed.

## Self-Check: PASSED

- `tests/contract/test_pact_norteia_api.py` modified: FOUND
- Commit `97d0dd6` exists: FOUND
- All 4 tests pass: VERIFIED
- `ibge_code` in pact artefact: VERIFIED
