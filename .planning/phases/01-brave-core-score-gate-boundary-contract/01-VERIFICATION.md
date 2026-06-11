---
phase: 01-brave-core-score-gate-boundary-contract
verified: 2026-06-11T12:00:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 4/5
  gaps_closed:
    - "Pushing the same source_ref twice is a no-op upsert — promote_to_mar now returns the existing active MarRecord unchanged when data is identical, and supersedes (D-03) only when data changed. The global UNIQUE(source_ref) was replaced by partial unique index uq_mar_active_source_ref (WHERE superseded_by_id IS NULL) via migration 0003. CLI run-fixture run TWICE produces the same Mar id and exits 0 on both invocations."
  gaps_remaining: []
  regressions: []
---

# Phase 1: Brave Core, Score Gate, Boundary & Contract — Verification Report

**Phase Goal:** A record can flow Nascente → Rio → Mar/DLQ/descarte through a pure, calibrable §7.6 score gate, with every external system behind a faked client interface, 24/7 Celery orchestration, full observability, and a frozen idempotent Mar→norteia-api contract — all validated by a 100%-offline keyless suite.
**Verified:** 2026-06-11T12:00:00Z
**Status:** passed
**Re-verification:** Yes — after gap closure (commit b8015f9)

---

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| SC1 | A raw payload ingested into Nascente is stored immutably; Celery processes it through Rio (territorial-key dedup, normalize, label) to a §7.6 score routing Mar/DLQ/descarte | ✓ VERIFIED | `brave/core/nascente/service.py` store_raw with SHA-256 content_hash; `brave/core/rio/routing.py` process_nascente_record; `brave/tasks/pipeline.py` process_nascente Celery task; 149 passing integration tests confirm the full flow |
| SC2 | §7.6 score engine is a pure zero-I/O function, unit-tested on boundary cases, stamps score_version, reprocessable idempotently | ✓ VERIFIED | `brave/core/score/engine.py` imports only ScoreConfig + schemas (zero I/O confirmed by grep); 18 parametrized boundary tests pass offline; reprocess_record resets and re-routes |
| SC3 | Mar record pushes to norteia-api idempotently keyed by source_ref (re-push no-op upsert), carries full per-criterion provenance, push shape frozen by Pact consumer test | ✓ VERIFIED | promote_to_mar returns existing active MarRecord unchanged on re-promote with same data (no-op). Supersedes only on changed data (D-03). Partial unique index uq_mar_active_source_ref (migration 0003) replaces the broken global UNIQUE. CLI run-fixture run twice: both exit 0 with identical Mar id 4fb6f420-e643-48c8-adc0-fe7db179bc25. New regression tests test_promote_to_mar_is_idempotent_on_stable_source_ref and test_promote_to_mar_supersedes_on_changed_score both pass. |
| SC4 | Every external system behind a Protocol interface with a fake; entire suite runs offline with no real network and no keys in CI | ✓ VERIFIED | 8 Protocol interfaces in `brave/clients/base.py`; FakeLLMClient, FakeNorteiaApiClient, FakePlacesClient in `tests/fakes/`; 99 tests pass offline with `--disable-socket` |
| SC5 | Pipeline records LLM calls with USD cost in llm_generations; enforcing cost guard halts on breach; per-layer metrics + audit logs + error-report webhook exposed via FastAPI | ✓ VERIFIED | `brave/observability/cost_guard.py` raises CostGuardError before dispatch; `brave/observability/llm_tracker.py`; all 5 FastAPI routers (health/metrics/dlq/audit/webhook) functional; webhook uses hmac.compare_digest with 401 before DB; 149/149 full suite passes |

**Score: 5/5 truths verified**

---

### Required Artifacts

| Artifact | Status | Details |
|----------|--------|---------|
| `brave/core/score/engine.py` | ✓ VERIFIED | Pure function, no I/O imports, all 5 §7.6 criteria, routing thresholds |
| `brave/core/score/schemas.py` | ✓ VERIFIED | ScoreInput (0-100 validated), ScoreBreakdown, ScoreResult |
| `brave/core/score/simulation.py` | ✓ VERIFIED | simulate_distribution + generate_cold_start_samples; cold-start origem=40 → 100% descarte |
| `brave/core/nascente/service.py` | ✓ VERIFIED | store_raw idempotent (same hash = same row), supersession (D-03), get_nascente |
| `brave/core/rio/dedup.py` | ✓ VERIFIED | Two-stage dedup: exact content_hash then territorial-key-blocked pgvector; never crosses UF |
| `brave/core/rio/routing.py` | ✓ VERIFIED | route_by_score, process_nascente_record (full pipeline), reprocess_record |
| `brave/core/mar/service.py` | ✓ VERIFIED | promote_to_mar: no-op upsert on stable source_ref (returns existing row unchanged); supersession path (D-03) via partial unique index uq_mar_active_source_ref and DEFERRABLE INITIALLY DEFERRED self-FK; reopen_from_error_report correct |
| `alembic/versions/0003_partial_unique_mar_source_ref.py` | ✓ VERIFIED | Drops uq_mar_source_ref UNIQUE constraint; creates partial unique index uq_mar_active_source_ref (WHERE superseded_by_id IS NULL); recreates fk_mar_superseded_by as DEFERRABLE INITIALLY DEFERRED |
| `brave/observability/cost_guard.py` | ✓ VERIFIED | CostGuardError, pre_dispatch_check raises before dispatch, record_spend INCRBYFLOAT |
| `brave/tasks/pipeline.py` | ✓ VERIFIED | process_nascente, push_mar (wired to NorteiaApiClient), reprocess_record_task; poison quarantine |
| `brave/api/main.py` | ✓ VERIFIED | FastAPI app with all 5 Phase 1 routers |
| `brave/clients/base.py` | ✓ VERIFIED | 8 Protocol interfaces (LLMClientProtocol, NorteiaApiClientProtocol, PlacesClientProtocol, OTAClientProtocol, ApifyClientProtocol, WhatsAppClientProtocol, MturClientProtocol, NotebookLMClientProtocol) |
| `brave/clients/norteia_api.py` | ✓ VERIFIED | httpx async context manager, Bearer auth, tenacity stop_after_attempt(3) wait_exponential |
| `tests/fakes/fake_llm.py` | ✓ VERIFIED | Structurally satisfies LLMClientProtocol |
| `tests/fakes/fake_norteia_api.py` | ✓ VERIFIED | Structurally satisfies NorteiaApiClientProtocol |
| `tests/contract/pacts/norteia-brave-norteia-api.json` | ✓ VERIFIED | consumer=norteia-brave, provider=norteia-api, 3 interactions (destination push, idempotent re-push, attraction push) |
| `tests/contract/test_pact_norteia_api.py` | ✓ VERIFIED | Uses `from pact import Pact` (3.4.0 top-level API); not deprecated 2.x Consumer/Provider |
| `tests/integration/test_end_to_end_pipeline.py` | ✓ VERIFIED | New regression tests: test_promote_to_mar_is_idempotent_on_stable_source_ref (stable key, no IntegrityError, same row returned) and test_promote_to_mar_supersedes_on_changed_score (changed data → new row, old row superseded_by_id set) |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `brave/core/score/engine.py` | `brave/core/rio/routing.py` | `route_by_score` calls `compute_score` | ✓ WIRED | Direct import and call confirmed in routing.py |
| `brave/tasks/pipeline.py` | `brave/core/rio/routing.py` | `process_nascente` task calls `process_nascente_record` | ✓ WIRED | Import confirmed; called inside process_nascente task |
| `brave/observability/cost_guard.py` | `brave/observability/llm_tracker.py` | `pre_dispatch_check` called before LLM dispatch | ✓ WIRED | LLMTracker.track_and_call calls pre_dispatch_check before call_fn |
| `brave/api/routers/webhook.py` | `brave/core/mar/service.py` | error-report endpoint calls `reopen_from_error_report` | ✓ WIRED | Direct import and call confirmed in webhook.py |
| `brave/clients/norteia_api.py` | `brave/tasks/pipeline.py` | `push_mar` task uses `NorteiaApiClient` | ✓ WIRED | Import confirmed; instantiated and used in push_mar task |
| `tests/contract/test_pact_norteia_api.py` | `brave/clients/norteia_api.py` | Pact test calls NorteiaApiClient against mock server URL | ✓ WIRED | Import confirmed; NorteiaApiClient(base_url=str(mock.url), ...) |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `brave/api/routers/metrics.py` | `nascente_count`, `rio_counts`, `mar_count` | SQLAlchemy SELECT COUNT + GROUP BY from DB | Yes | ✓ FLOWING |
| `brave/api/routers/dlq.py` | DLQ list | SELECT RioRecord WHERE routing='dlq' | Yes | ✓ FLOWING |
| `brave/api/routers/audit.py` | AuditLog entries | SELECT AuditLog paginated | Yes | ✓ FLOWING |
| `brave/core/score/engine.py` | ScoreResult | Pure computation from ScoreInput + ScoreConfig | Yes (deterministic) | ✓ FLOWING |
| `brave/core/mar/service.py` | MarRecord | Idempotent upsert: existing row returned on stable data; new row + supersession on changed data | Yes | ✓ FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Offline unit+contract suite | `uv run pytest tests/ --ignore=tests/integration --disable-socket` | 99 passed in 0.17s | ✓ PASS |
| Full suite (integration+contract, including new regression tests) | `uv run pytest tests/ -q` (with env) | 149 passed, 1 warning in 5.46s | ✓ PASS |
| Score engine pure (no I/O imports) | `grep "^from\|^import" brave/core/score/engine.py` | Only brave.config.settings and brave.core.score.schemas | ✓ PASS |
| Cold-start simulation DLQ landfill | `simulate_distribution(ScoreConfig(), generate_cold_start_samples(200))` | mar_pct=0.0, dlq_pct=0.0, descarte_pct=100.0 (landfill visible at origem=40) | ✓ PASS |
| Pact JSON artifact | `ls tests/contract/pacts/norteia-brave-norteia-api.json` | Exists, 4.7K, consumer=norteia-brave, provider=norteia-api, 3 interactions | ✓ PASS |
| FastAPI health endpoint | `TestClient(app).get('/api/v1/health')` | Requires BRAVE_DB_URL; integration test confirms 200 | ✓ PASS (integration) |
| CLI first run | `uv run python -m brave.cli run-fixture` (fresh DB) | Nascente: 606c9251 \| Score: 93.0 \| Routing: mar \| Mar: 4fb6f420-e643-48c8-adc0-fe7db179bc25 \| Push: recorded (exit 0) | ✓ PASS |
| CLI second run (idempotency proof) | `uv run python -m brave.cli run-fixture` (same DB state) | Nascente: 606c9251 \| Score: 93.0 \| Routing: mar \| Mar: 4fb6f420-e643-48c8-adc0-fe7db179bc25 \| Push: recorded (exit 0, same Mar id) | ✓ PASS |
| promote_to_mar idempotent on stable key (regression) | test_promote_to_mar_is_idempotent_on_stable_source_ref | mar_second.id == mar_first.id; exactly one active row | ✓ PASS |
| promote_to_mar supersedes on changed score (regression) | test_promote_to_mar_supersedes_on_changed_score | mar_second.id != mar_first.id; mar_first.superseded_by_id == mar_second.id | ✓ PASS |

---

### Requirements Coverage

| Requirement | Plans | Description | Status | Evidence |
|-------------|-------|-------------|--------|---------|
| CORE-01 | 01-01, 01-02 | Immutable, source-tagged, versioned JSONB Nascente store | ✓ SATISFIED | NascenteRecord model; store_raw append-only with SHA-256 content_hash |
| CORE-02 | 01-01, 01-02 | Rio: dedup (exact hash + pgvector), normalize names/coords/addresses, label | ✓ SATISFIED | dedup.py two-stage; normalize.py; label.py; process_nascente_record orchestrates |
| CORE-03 | 01-01, 01-02 | Three-way routing: Mar (≥85), DLQ (51-84.9), descarte (≤50) by config thresholds | ✓ SATISFIED | compute_score + route_by_score; thresholds in ScoreConfig |
| CORE-04 | 01-01 | Mar: canonical records, versioned by supersession, supports invalidation and update | ✓ SATISFIED | MarRecord partial unique index uq_mar_active_source_ref + DEFERRABLE FK; promote_to_mar supersession path verified by regression tests; D-15 idempotent upsert proven by CLI run-fixture twice |
| CORE-05 | 01-02, 01-03 | Pipeline pushes Mar to norteia-api idempotently keyed by source_ref | ✓ SATISFIED | NorteiaApiClient + push_mar wired; Pact contract frozen; promote_to_mar no-op upsert on stable source_ref confirmed; CLI exits 0 on second run with same Mar id |
| CORE-06 | 01-02 | Every record carries provenance/lineage through to Mar push | ✓ SATISFIED | promote_to_mar carries score_breakdown; push_mar flattens to Pact shape |
| CORE-07 | 01-02 | DLQ is durable, actionable queue with reason codes | ✓ SATISFIED | routing='dlq' on RioRecord with dlq_reason; /api/v1/dlq endpoint; reprocess/descarte patches |
| CORE-08 | 01-02 | Reprocess/re-score idempotent without double-publishing | ✓ SATISFIED | reprocess_record resets routing then re-routes; reprocess_record_task Celery task |
| CORE-09 | 01-02 | Error classification: transient (retry) vs permanent (DLQ/descarte) | ✓ SATISFIED | TransientError → self.retry; PermanentError → quarantine_poison; max_retries=3 |
| CORE-10 | 01-01, 01-02 | Celery + Redis 24/7 with beat scheduling (celery-redbeat), idempotent tasks, poison quarantine | ✓ SATISFIED | celery_app.py with redbeat; task_acks_late=True; quarantine_poison separate from DLQ |
| CORE-11 | 01-01 | Every external system behind a client interface with a fake | ✓ SATISFIED | 8 Protocol interfaces; 3 fakes (LLM, NorteiaApi, Places) |
| CORE-12 | 01-02 | FastAPI webhooks, REST for dashboard, lane ingest; idempotent webhook receivers | ✓ SATISFIED | health/metrics/dlq/audit/webhook routers; webhook 401-before-DB; 202 on success |
| SCORE-01 | 01-01, 01-02 | Pure score function: origem 30% · completude 20% · corroboração 20% · atualidade 15% · validação humana 15% | ✓ SATISFIED | compute_score formula confirmed; 18 parametrized boundary tests pass |
| SCORE-02 | 01-01, 01-02 | Weights/thresholds calibrable via config; scores versioned against weight set | ✓ SATISFIED | ScoreConfig with env_prefix; score_version stamped on every ScoreResult and RioRecord |
| SCORE-03 | 01-02 | One engine for both destino and atrativo; unit-tested on boundary cases | ✓ SATISFIED | compute_score entity-agnostic; boundary tests at 85.0, 84.9, 51.0, 50.9 |
| OBS-01 | 01-02 | Every LLM call recorded in llm_generations (per-lane, per-model, USD cost) | ✓ SATISFIED | LLMGeneration model; LLMTracker.track_and_call writes to llm_generations |
| OBS-02 | 01-02 | USD cost guard enforces spend ceiling, halts on breach | ✓ SATISFIED | pre_dispatch_check raises CostGuardError before dispatch; enforcing not advisory |
| OBS-03 | 01-02 | Per-layer Brave metrics + queue/worker health via FastAPI | ✓ SATISFIED | /api/v1/metrics returns nascente_count, rio_count (by routing), mar_count |
| OBS-04 | 01-02 | Pipeline writes audit logs for steward and pipeline actions | ✓ SATISFIED | write_audit in audit.py; AuditLog model; /api/v1/audit endpoint |
| CNTR-01 | 01-03 | Mar→norteia-api ingestion contract frozen and verified by Pact consumer test | ✓ SATISFIED | tests/contract/pacts/norteia-brave-norteia-api.json; 3 interactions; 100% offline |
| CNTR-02 | 01-02, 01-03 | Community error-report webhook reopens published record back into Rio/DLQ | ✓ SATISFIED | reopen_from_error_report; POST /webhook/error-report → 202 with dlq_reason='community_error_report' |
| TEST-01 | 01-01, 01-02, 01-03 | Full suite runs 100% offline via docker-compose; real externals opt-in; CI keyless | ✓ SATISFIED | 99 tests pass offline --disable-socket; no real API keys in any test |
| TEST-03 | 01-03 | HTTP boundaries faked with respx/VCR, LLM faked, webhooks fixture-driven; norteia-api covered by Pact | ✓ SATISFIED | respx used in test_mar_push.py; FakeLLMClient; Pact mock server for norteia-api |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `brave/core/rio/dedup.py` | 96-111 | compute_embedding returns `[0.0] * 1536` (zero vector stub) | INFO (intentional) | Documented Phase 1 stub; real embeddings in Phase 2; content_hash guards against false dedup |
| `brave/core/rio/label.py` | - | label_entity returns taxonomy stub only | INFO (intentional) | Documented Phase 1 stub; real NLP in Phase 2 |

(Previous BLOCKER on `brave/core/mar/service.py` and `brave/cli.py` resolved by commit b8015f9.)

---

### Human Verification Required

None. All truths are verifiable programmatically and the test suite confirms the full observable behavior.

---

### Gap Closure Summary

The sole blocker from the initial verification was resolved by commit b8015f9:

1. `promote_to_mar` now checks for an existing active MarRecord with the same `source_ref` before any write. If data is identical (score, canonical, provenance, score_version all match), it returns the existing row unchanged — no INSERT, no flush, no constraint touched (D-15 no-op upsert).

2. When data has changed, supersession proceeds via the new partial unique index `uq_mar_active_source_ref` (WHERE superseded_by_id IS NULL), which allows the old row and the new row to coexist (the old row's `superseded_by_id` is set within the same transaction). The self-FK is `DEFERRABLE INITIALLY DEFERRED`, so the circular write validates at COMMIT.

3. Migration 0003 applied the schema change: dropped `uq_mar_source_ref` (global UNIQUE), created `uq_mar_active_source_ref` (partial), and recreated `fk_mar_superseded_by` as DEFERRABLE.

4. Two new regression tests added and confirmed passing:
   - `test_promote_to_mar_is_idempotent_on_stable_source_ref` — uses a stable (non-UUID-suffixed) source_ref, calls `promote_to_mar` twice, asserts same row returned and exactly one active row.
   - `test_promote_to_mar_supersedes_on_changed_score` — mutates score between promotes, asserts new row created and old row's `superseded_by_id` set correctly.

5. CLI idempotency proof: `uv run python -m brave.cli run-fixture` run twice with the same DB state — both runs exit 0, both print the same Mar id (`4fb6f420-e643-48c8-adc0-fe7db179bc25`).

Full suite: **149 passed, 1 warning** (up from 147; the 2 new regression tests now run). Offline keyless suite: **99 passed**.

---

_Verified: 2026-06-11T12:00:00Z_
_Verifier: Claude (gsd-verifier)_
_Re-verification after gap closure: commit b8015f9_
