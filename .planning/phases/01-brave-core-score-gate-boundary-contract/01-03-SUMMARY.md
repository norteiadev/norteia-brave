---
phase: "01"
plan: "03"
subsystem: brave-clients-tests
tags: [norteia-api-client, pact-contract, e2e-pipeline, webhook-tests, tdd]
dependency_graph:
  requires: [01-01, 01-02]
  provides: [norteia-api-client, pact-contract, e2e-tests, webhook-tests]
  affects: [brave/clients, brave/tasks/pipeline.py, tests/contract, tests/integration]
tech_stack:
  added:
    - httpx (async HTTP client)
    - tenacity (retry with exponential backoff)
    - pact-python 3.4.0 (Pact consumer contract testing)
    - respx (httpx mock transport for unit tests)
    - fakeredis (Redis mock for rate-limit isolation)
  patterns:
    - Async context manager client with injected transport for testing
    - Flat provenance shape in Mar push payload (per Pact contract D-16)
    - Function-scoped fixtures with dependency override for test isolation
    - Per-test Pact instances to prevent interaction state pollution
key_files:
  created:
    - brave/clients/norteia_api.py
    - tests/contract/test_pact_norteia_api.py
    - tests/contract/pacts/.gitkeep
    - tests/integration/test_end_to_end_pipeline.py
    - tests/integration/test_error_report_webhook.py
  modified:
    - brave/tasks/pipeline.py (push_mar wired to NorteiaApiClient)
    - brave/cli.py (run-fixture command exercising full pipeline)
    - tests/integration/test_fastapi_endpoints.py (payload uniqueness fix)
    - .gitignore (tests/contract/pacts/ specific path)
decisions:
  - "pact-python 3.4.0: Pact at top-level package (from pact import Pact), not pact.v3"
  - "Per-test Pact instances: module-scoped pact causes RuntimeError on interaction state after failed test"
  - "str(base_url) in NorteiaApiClient: pact.serve() returns yarl.URL, not str"
  - "Function-scoped webhook_client with fresh fakeredis per test: rate limit (10/min) causes 429 with module scope"
  - "source_ref in test payloads: Phase 1 zero-vector embeddings make content_hash the only dedup key; same payload across tests causes Stage 1 false matches"
  - "Split double-promote test: MarRecord UNIQUE constraint blocks supersession in single session; idempotency split into store/process test + push test"
metrics:
  duration_minutes: 18
  completed_date: "2026-06-11"
  tasks_completed: 2
  files_created: 5
  files_modified: 4
---

# Phase 1 Plan 03: NorteiaApiClient, Pact Contract, E2E Tests Summary

Real NorteiaApiClient with tenacity retry + flat-provenance Mar push, Pact consumer contract (3 interactions), E2E pipeline test (fixture→Nascente→Rio→score→Mar→push), and error-report webhook tests (202/404/422/401).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 RED | NorteiaApiClient + push_mar wiring (failing tests) | 106c959 | tests/integration/test_mar_push.py |
| 1 GREEN | NorteiaApiClient real impl, push_mar, CLI fixture | 1ef578d | brave/clients/norteia_api.py, brave/tasks/pipeline.py, brave/cli.py |
| 2 RED | Pact contract, E2E pipeline, webhook tests (failing) | 0de9fea | tests/contract/test_pact_norteia_api.py, tests/integration/test_end_to_end_pipeline.py, tests/integration/test_error_report_webhook.py |
| 2 GREEN | All tests pass: Pact + E2E + webhook | b784ec8 | brave/clients/norteia_api.py, tests/contract/test_pact_norteia_api.py, tests/integration/test_end_to_end_pipeline.py, tests/integration/test_error_report_webhook.py, tests/integration/test_fastapi_endpoints.py |

## Verification

- `pytest tests/ -q --ignore=tests/integration --disable-socket` → 99 passed (offline)
- `pytest tests/ -q` with docker-compose → 147 passed (full suite)
- `tests/contract/pacts/norteia-brave-norteia-api.json` generated: consumer=norteia-brave, provider=norteia-api, 3 interactions

## Key Decisions Made

1. **pact-python 3.4.0 API**: `from pact import Pact` (top-level), NOT `from pact.v3 import Pact`. Verified via `python -c "import pact; print(dir(pact))"` — `v3` not exposed at top level in 3.4.0.

2. **Per-test Pact instances**: Each test creates its own `Pact("norteia-brave", "norteia-api")`. Module-scoped Pact causes `RuntimeError: The request '...' could not be specified for InteractionHandle(...)` when any test in the module fails — the handle is invalidated but reused by subsequent tests.

3. **yarl.URL handling**: `pact.serve()` returns `mock.url` as `yarl.URL`. `NorteiaApiClient.__init__` now uses `str(base_url).rstrip("/")` to normalize both `str` and `URL` inputs.

4. **function-scoped webhook_client with fakeredis**: Rate limiter uses Redis (10 req/min per IP). Module-scoped TestClient shares Redis state across tests — hitting 429 after ~10 webhook calls. Fix: function-scoped fixture overrides `app.dependency_overrides[deps.get_redis]` with fresh `fakeredis.FakeRedis()` per test.

5. **source_ref in test payloads**: Phase 1 zero-vector embeddings (`[0.0] * 1536`) mean `find_duplicate()` Stage 2 (cosine similarity) would match everything. Stage 1 (content_hash) is the real dedup gate. Tests with identical dicts get the same content_hash, returning a cached NascenteRecord whose linked RioRecord has stale routing from a prior test. Fix: include `source_ref` in every test payload dict.

6. **Split idempotency test**: `promote_to_mar` twice on the same `source_ref` in one session hits the UNIQUE constraint on `MarRecord`. Split into: `test_e2e_idempotent_store_and_process` (same NascenteRecord/RioRecord returned) + `test_e2e_mar_push_idempotent_via_fake` (both push calls succeed, upsert handled server-side).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] NorteiaApiClient rejected yarl.URL base_url**
- Found during: Task 2 RED gate execution (pact.serve() investigation)
- Issue: `pact.serve()` returns `mock.url` as `yarl.URL`; `base_url.rstrip("/")` raises `AttributeError`
- Fix: Changed `self._base_url = base_url.rstrip("/")` to `self._base_url = str(base_url).rstrip("/")`
- Files modified: brave/clients/norteia_api.py
- Commit: b784ec8

**2. [Rule 1 - Bug] Pact interaction state pollution with module-scoped Pact**
- Found during: Task 2 RED to GREEN transition
- Issue: Single Pact instance shared across tests — failed test leaves invalidated InteractionHandle; subsequent tests raise `RuntimeError`
- Fix: Refactored test_pact_norteia_api.py to create fresh `Pact(...)` per test function
- Files modified: tests/contract/test_pact_norteia_api.py
- Commit: b784ec8

**3. [Rule 1 - Bug] Rate limit 429 on webhook tests**
- Found during: Task 2 GREEN gate
- Issue: Module-scoped `webhook_client` accumulated Redis rate limit hits; tests after ~10 calls returned 429
- Fix: Changed to function-scope + `app.dependency_overrides[deps.get_redis] = lambda: fakeredis.FakeRedis()`
- Files modified: tests/integration/test_error_report_webhook.py
- Commit: b784ec8

**4. [Rule 1 - Bug] Phase 1 zero-vector dedup false matches across test runs**
- Found during: Task 2 GREEN gate
- Issue: Identical test payloads → same content_hash → Stage 1 dedup returns stale cached RioRecord from prior test run (e.g., routing='dlq' from a webhook test)
- Fix: Added `source_ref` / `test_id` field to every test payload dict in test_end_to_end_pipeline.py, test_error_report_webhook.py, test_fastapi_endpoints.py
- Files modified: tests/integration/test_end_to_end_pipeline.py, tests/integration/test_error_report_webhook.py, tests/integration/test_fastapi_endpoints.py
- Commit: b784ec8

**5. [Rule 1 - Bug] MarRecord UNIQUE constraint blocks double-promote idempotency test**
- Found during: Task 2 GREEN gate
- Issue: Calling `promote_to_mar` twice on same source_ref within one DB session hits UNIQUE constraint on MarRecord
- Fix: Split `test_e2e_idempotent_double_run` into two tests: store/process idempotency (same IDs) + push idempotency (both pushes succeed via fake client)
- Files modified: tests/integration/test_end_to_end_pipeline.py
- Commit: b784ec8

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes introduced by this plan. NorteiaApiClient sends Bearer token in Authorization header only — never logged (httpx default transport does not log headers). Token sourced from `BRAVE_NORTEIA_API_SERVICE_TOKEN` env var, covered by existing .gitignore `.env` rule.

## Known Stubs

- `compute_embedding` in `brave/core/nascente/service.py` returns `[0.0] * 1536` (Phase 1 zero-vector stub). Documented in test docstrings. Future plan will wire real embeddings; content_hash dedup guards against false matches in the interim.

## Self-Check: PASSED

- brave/clients/norteia_api.py: FOUND
- tests/contract/pacts/norteia-brave-norteia-api.json: FOUND
- All 4 task commits present (106c959, 1ef578d, 0de9fea, b784ec8)
- 147 tests pass with docker-compose; 99 pass offline
