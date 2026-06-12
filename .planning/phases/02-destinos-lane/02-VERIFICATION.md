---
phase: 02-destinos-lane
verified: 2026-06-12T21:08:54Z
status: passed
score: 5/5
overrides_applied: 0
---

# Phase 2: Destinos Lane — Verification Report

**Phase Goal:** Destinos flow through the proven core from three producers (Mtur origem=100, NotebookLM origem=80, DesmembramentoAgent origem=40) into the DLQ, where a steward validates them batch-by-state (BA/RJ/SP/SC/CE/PE first) to set validação humana=100 and promote them to Mar and push to `destinations`.
**Verified:** 2026-06-12T21:08:54Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | MturSeedIngest ingests municipalities → Nascente (source=mtur, origem=100, municipality_id=IBGE); NotebookLMIngest ingests reports (source=notebooklm, origem=80) | VERIFIED | `brave/lanes/destinos/mtur.py`: `origem_value=100.0`, `source="mtur"`, `municipio_id=ibge_code` in payload. `brave/lanes/destinos/notebooklm.py`: `origem_value=80.0`, `source="notebooklm"`. Integration test `test_mtur_lane_end_to_end` passes (DB-level proof: NascenteRecord exists with correct fields). 14 Mtur unit tests pass. |
| 2 | DesmembramentoAgent lists destinos → Nascente (origem=40, flagged) behind mandatory Pydantic+instructor 2nd-layer validator that quarantines malformed output | VERIFIED | `brave/lanes/destinos/desmembramento.py`: `origem_value=40.0`, `source_note="LLM-generated, pending validation"`, `extract(prompt, DesmembramentoResult, mode="tools")`. Exception path → `quarantine_poison(task_name="brave.desmembramento")`. 4 unit tests pass: happy path, quarantine path, empty destinos, non-Oferta-Principal skip. |
| 3 | Destinos flow through Rio + §7.6 and land in DLQ by default; origem=40 firewall means no LLM-only destino reaches Mar unaided | VERIFIED | `ScoreConfig().threshold_dlq=40.0`, `threshold_mar=85.0`. Max score for origem=40 without validacao_humana=67.0 (→dlq). Max score for origem=40 with validacao_humana=100 is 82.0 (→dlq still). D-06 firewall is a pure scoring consequence, proven by `test_producer_score_boundaries` (7 cases). Integration test `test_mtur_lane_end_to_end` confirms cold-start routing="dlq". |
| 4 | Steward validates DLQ destinos batch-by-state, setting validação humana=100 → re-score → Mar → push to `destinations` | VERIFIED | `brave/api/routers/dlq.py`: `PATCH /api/v1/dlq/{rio_id}/validate` and `POST /api/v1/dlq/validate-batch` both present and wired. `flag_modified` correctly applied. `reprocess_record` (not `process_nascente_record`) used. `push_destination_task.delay()` dispatched on routing=="mar". Integration tests: `test_validate_endpoint_promotes_to_mar_with_corroboration` (flag_modified DB round-trip), `test_validate_batch_returns_202`, audit row tests — all 11 integration tests pass. |
| 5 | Score engine and DesmembramentoAgent have unit tests covering Mar/DLQ/descarte boundary cases, all offline | VERIFIED | `test_producer_score_boundaries`: 7 parametrized cases covering D-06 firewall, Mtur cold-start DLQ, Mtur descarte risk, NotebookLM DLQ, post-validation Mar, post-validation no-corroboration DLQ, Desmembramento post-validate DLQ — all pass. `test_desmembramento.py`: 4 unit tests covering happy path, quarantine, empty destinos, filter logic — all pass offline. Full suite: 191 passed, 0 failed. |

**Score:** 5/5 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/lanes/destinos/mtur.py` | MturSeedIngest, produce(uf), origem=100, ibge_code link | VERIFIED | 168 lines, substantive implementation with `_completude_from_fields`, `MTUR_ATUALIDADE_DEFAULT=70.0`, canonical dict with ibge_code |
| `brave/lanes/destinos/notebooklm.py` | NotebookLMIngest, produce(uf), origine=80, corroboration boost | VERIFIED | 228 lines, corroboration boost with `flag_modified`, `reprocess_record`, IBGE exact-match query |
| `brave/lanes/destinos/desmembramento.py` | DesmembramentoAgent, fan-out, validate-or-quarantine, origen=40 | VERIFIED | 231 lines, DESMEMBRAMENTO_PROMPT constant, quarantine_poison import from brave.core.quarantine (D-18 clean) |
| `brave/lanes/destinos/schemas.py` | DesmembramentoResult + DestinoItem Pydantic v2 | VERIFIED | Contains both models, IBGE code regex validator, DestinoItem Literal tipos |
| `brave/core/quarantine.py` | quarantine_poison extracted from tasks (D-18 boundary) | VERIFIED | 57 lines, re-exported in pipeline.py line 83, imported from brave.core.quarantine in desmembramento.py |
| `brave/api/routers/dlq.py` | validate + validate-batch endpoints | VERIFIED | Both routes present: `PATCH /api/v1/dlq/{rio_id}/validate` (line 94) and `POST /api/v1/dlq/validate-batch` (line 155); flag_modified used in both |
| `brave/tasks/pipeline.py` | push_destination_task (brave.push_destination) | VERIFIED | Task name confirmed: `brave.push_destination`, always calls push_destination never push_attraction |
| `brave/config/settings.py` | ScoreConfig.threshold_dlq=40.0, score_version=v1.1 | VERIFIED | `threshold_dlq: float = 40.0`, `score_version: str = "v1.1"` with calibration rationale comment |
| `brave/clients/mtur.py` | MturClient reading bundled CSV | VERIFIED | Real CSV reader with `_load_csv()`, `_map_categoria()`, `_check_protocol_compliance` |
| `brave/clients/notebooklm.py` | NotebookLMClient reading local JSON reports | VERIFIED | Reads from `data/notebooklm/`, handles missing gracefully, protocol compliance |
| `tests/fakes/fake_mtur.py` | FakeMturClient with call recording | VERIFIED | call recording via `self.calls`, configurable fixtures |
| `tests/fakes/fake_notebooklm.py` | FakeNotebookLMClient keyed by municipio | VERIFIED | reports dict keyed by municipio string, call recording |
| `data/mtur/municipios_mtur_2024.csv` | Bundled Mtur seed dataset | VERIFIED | Exists with BA municipalities including Porto Seguro (2927408), old+new category names |
| `tests/contract/test_pact_norteia_api.py` | ibge_code in DESTINATION_PAYLOAD + Pact JSON | VERIFIED | `canonical["ibge_code"]="2927408"` present in payload; Pact JSON interaction for destination push contains ibge_code; 4 Pact tests pass |
| `tests/integration/test_destinos_lane.py` | End-to-end Destinos lane integration tests | VERIFIED | 11 integration tests covering validate (single+batch), corroboration boost, end-to-end Mtur flow |
| `tests/unit/test_desmembramento.py` | DesmembramentoAgent unit tests (offline) | VERIFIED | 4 async tests: happy path, quarantine, empty destinos, non-Oferta filter; all pass with db_session |
| `tests/unit/test_score_engine.py` | test_producer_score_boundaries (7 cases) | VERIFIED | 7 parametrized cases with D-06 firewall; all pass |
| `scripts/calibrate_destinos.py` | Runnable calibration script | VERIFIED | Passes all 4 GATE checks including `GATE: Desmembramento lands in DLQ with threshold_dlq=40: PASS` |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `brave/lanes/destinos/mtur.py` | `brave/core/nascente/service.py` | `store_raw(session, source="mtur", ...)` | WIRED | Line 149-156 calls store_raw |
| `brave/lanes/destinos/mtur.py` | `brave/core/rio/routing.py` | `process_nascente_record(session, nascente, config)` | WIRED | Line 158-162 triggers Rio pipeline |
| `brave/lanes/destinos/notebooklm.py` | `brave/core/rio/routing.py` | `reprocess_record(session, existing.id, config)` | WIRED | Line 222 after corroboration boost |
| `brave/lanes/destinos/notebooklm.py` | `sqlalchemy.orm.attributes` | `flag_modified(existing, "normalized")` | WIRED | Line 220, top-level import (line 27) |
| `brave/lanes/destinos/desmembramento.py` | `brave/core/quarantine.py` | `quarantine_poison(session, nascente_id=None, task_name="brave.desmembramento", ...)` | WIRED | Line 176-186, imported line 25 |
| `brave/api/routers/dlq.py` | `brave/core/rio/routing.py` | `reprocess_record(db, rio_id, ScoreConfig())` | WIRED | Lines 129 and 194 (both endpoints) |
| `brave/api/routers/dlq.py` | `brave/tasks/pipeline.py` | `push_destination_task.delay(str(rio_id))` | WIRED | Lines 137 and 200 (both endpoints) |
| `brave/api/routers/dlq.py` | `sqlalchemy.orm.attributes` | `flag_modified(rio, "normalized")` | WIRED | Lines 122 and 189 (both endpoints) |
| `brave/tasks/pipeline.py` | `brave/core/quarantine.py` | `from brave.core.quarantine import quarantine_poison` (re-export) | WIRED | Line 83 |
| `tests/contract/test_pact_norteia_api.py` | `brave/clients/norteia_api.py` | `NorteiaApiClient.push_destination(payload)` with ibge_code | WIRED | DESTINATION_PAYLOAD.canonical contains ibge_code; Pact JSON interaction verified |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `MturSeedIngest.produce` | municipalities list | `MturClientProtocol.fetch_municipalities(uf)` — reads bundled CSV or fake | Yes (CSV read or fake fixtures) | FLOWING |
| `NotebookLMIngest.produce` | report dict | `NotebookLMClientProtocol.fetch_report(municipio_key)` — reads local JSON files | Yes (JSON file read or fake) | FLOWING |
| `DesmembramentoAgent.produce` | DesmembramentoResult | `LLMClientProtocol.extract(prompt, DesmembramentoResult, mode="tools")` | Yes (faked in tests; real in prod) | FLOWING |
| `validate_dlq_record` | rio.routing after re-score | `reprocess_record(db, rio_id, ScoreConfig())` → §7.6 engine | Yes (real DB computation) | FLOWING |
| `validate_batch` | validated count | loop over RioRecord query + reprocess per record | Yes (real DB query + scoring) | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| ScoreConfig.threshold_dlq=40.0 | `python -c "from brave.config.settings import ScoreConfig; c=ScoreConfig(); print(c.threshold_dlq)"` | `40.0` | PASS |
| ScoreConfig.score_version=v1.1 | Same import, print `c.score_version` | `v1.1` | PASS |
| push_destination_task registered | `from brave.tasks.pipeline import push_destination_task; print(push_destination_task.name)` | `brave.push_destination` | PASS |
| quarantine_poison importable from core | `from brave.core.quarantine import quarantine_poison` | No import error | PASS |
| D-06 firewall (origen=40, max score without validation) | `compute_score(ScoreInput(origem_value=40, completude_value=100, corroboracao_value=100, atualidade_value=100, validacao_humana_value=0), ScoreConfig())` | `score=67.0, routing='dlq'` | PASS |
| D-06 firewall (origen=40 even with validation) | Same with `validacao_humana_value=100` | `score=82.0, routing='dlq'` (never reaches Mar threshold=85) | PASS |
| Calibration GATE | `python scripts/calibrate_destinos.py` | `GATE: Desmembramento lands in DLQ with threshold_dlq=40: PASS` | PASS |
| Full test suite | `uv run pytest tests/ --tb=no` | `191 passed, 1 warning in 12.43s` | PASS |

### Probe Execution

Step 7c: SKIPPED — No `scripts/*/tests/probe-*.sh` probes declared. The acceptance gate is the pytest suite (191/191 passing).

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|---------------|-------------|--------|----------|
| DEST-01 | 02-01, 02-03, 02-05, 02-09 | MturSeedIngest ingests categorized municipalities → Nascente (source=mtur, origem=100, municipality_id=ibge_code) | SATISFIED | `mtur.py` origin=100, source="mtur", municipio_id=ibge_code. Integration test asserts NascenteRecord with correct payload fields. |
| DEST-02 | 02-03, 02-07, 02-09 | NotebookLMIngest ingests reports → Nascente (source=notebooklm, origem=80) | SATISFIED | `notebooklm.py` origin=80, source="notebooklm". `test_notebooklm_corroboration_boosts_mtur` verifies corroboration boost mechanism (load-bearing for Mar promotion). |
| DEST-03 | 02-03, 02-08, 02-09 | DesmembramentoAgent → Nascente (origen=40, flagged "LLM-generated") with Pydantic+instructor 2nd-layer validator + quarantine | SATISFIED | `desmembramento.py` origin=40, source_note="LLM-generated, pending validation", instructor Mode.TOOLS. Quarantine path unit-tested (4 tests pass). |
| DEST-04 | 02-02, 02-05, 02-09 | Destinos flow through Rio + score and land in DLQ by default | SATISFIED | threshold_dlq=40.0 calibrated. Integration test `test_mtur_lane_end_to_end` asserts routing=="dlq" after cold-start produce. 7 score boundary tests pass. |
| DEST-05 | 02-01, 02-04, 02-06, 02-07, 02-09 | Steward validates DLQ batch-by-state → validação humana=100 → Mar → push to destinations | SATISFIED | PATCH + POST validate endpoints wired with flag_modified + reprocess_record + push_destination_task. Integration tests cover single validate (flag_modified round-trip), batch, audit rows, Mar promotion. |
| TEST-02 | 02-02, 02-05, 02-08, 02-09 | Score engine and DesmembramentoAgent have unit tests covering Mar/DLQ/descarte boundary cases | SATISFIED | `test_producer_score_boundaries` (7 cases), `test_desmembramento.py` (4 cases). All pass offline. |

**All 6 phase requirements satisfied.**

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `brave/api/routers/dlq.py` (all 5 endpoints) | No authentication dependency on mutating endpoints | WARNING | CR-01 from 02-REVIEW.md — steward trust boundary unprotected. Tracked as security hardening item for Phase 4 (DASH-06 Bearer auth). Does NOT block goal: validate endpoints are functional and the phase is internal-only. |
| `brave/config/settings.py:59,72` | LLMConfig field alias without env_prefix — potential secret shadowing | WARNING | CR-02 from 02-REVIEW.md — alias "openrouter_api_key" overrides "BRAVE_LLM_OPENROUTER_API_KEY". Not a Phase 2 functionality blocker (LLM keys not used in Phase 2 destinos path). |
| `brave/api/routers/dlq.py` | Bare `except Exception` in dispatch fallback blocks | WARNING | CR-03 from 02-REVIEW.md — masks Celery broker errors. Not a Phase 2 test blocker (tests run with sync fallback intentionally). |
| `brave/api/routers/dlq.py:184-210` | `before_state.score` captured after flush in validate_batch | WARNING | CR-04 from 02-REVIEW.md — audit inconsistency, not a functional blocker. |
| `brave/lanes/destinos/desmembramento.py:191-196` | Slug does not sanitize accented chars or apostrophes | WARNING | CR-05 from 02-REVIEW.md — idempotency risk for Portuguese names. Affects correctness over time but not Phase 2 test coverage with ASCII fixture data. |
| `brave/api/routers/dlq.py:168` | Docstring claims batch-summary audit row but none is written | INFO | WR-01 from 02-REVIEW.md — documentation-code mismatch only. |
| `brave/lanes/destinos/mtur.py:165-167`, `notebooklm.py:226-227` | Commented-out LaneProtocol type annotation | INFO | IN-04 from 02-REVIEW.md — dead code style issue. |
| `data/mtur/municipios_mtur_2024.csv.sha256` | Contains "PLACEHOLDER" text | INFO | Explicitly planned (Plan 03 action): "Add a SHA-256 placeholder file ... with the text 'PLACEHOLDER — replace with real SHA-256 after downloading official Mtur dataset'". Not a debt marker. |

**Debt marker gate:** No TBD / FIXME / XXX markers found in Phase 2 modified files. PLACEHOLDER in the SHA-256 file is explicitly planned and self-documented. Zero blockers from debt markers.

**All CR-01 through CR-05 issues are tracked in 02-REVIEW.md as code-review findings.** The user instruction states: "Treat security-hardening items as follow-up (a /gsd:secure-phase pass), not as goal-blocking, UNLESS a finding means a success criterion is functionally unmet." None of the five critical findings prevent a success criterion from being observably true — the DLQ validate endpoints function correctly (SC-4 verified), the D-06 firewall holds (SC-3 verified), all tests pass (SC-5 verified).

### Human Verification Required

No items require human verification for this phase. All success criteria are observable in the codebase and verified by the passing test suite (191/191).

---

## Gap Analysis

No gaps found. All 5 ROADMAP success criteria are VERIFIED.

### Note on 02-09 Must-Have: "DesmembramentoAgent quarantine path is covered by the integration test"

The 02-09 PLAN must-have specifies coverage "in the integration test" (meaning `tests/integration/test_destinos_lane.py`). The actual quarantine coverage lives in `tests/unit/test_desmembramento.py`, which uses `db_session` (real PostgreSQL) and is substantively equivalent to an integration test. The ROADMAP SC-5 says "unit tests covering... all offline" — which is satisfied. This is a plan-wording discrepancy (file location), not a functional gap. The quarantine path is fully tested with DB-level assertions.

---

_Verified: 2026-06-12T21:08:54Z_
_Verifier: Claude (gsd-verifier)_
