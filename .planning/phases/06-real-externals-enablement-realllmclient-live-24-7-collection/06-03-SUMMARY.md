---
phase: "06"
plan: "03"
subsystem: "clients/tests"
tags: ["testing", "llm", "cost-guard", "pipeline", "offline-suite", "D-07"]
dependency_graph:
  requires:
    - "brave/clients/llm.py RealLLMClient (06-02)"
    - "brave/observability/cost_guard.py (pre_dispatch_check, record_spend)"
    - "brave/core/models.py (LLMGeneration)"
    - "brave/config/settings.py (LLMConfig, AppConfig)"
    - "brave/tasks/pipeline.py (4 real-path LLM call sites)"
  provides:
    - "tests/unit/clients/test_real_llm_client.py (5 offline unit tests, D-07)"
    - "tests/integration/test_real_llm_smoke.py (opt-in real smoke, skipif gated)"
    - "brave/tasks/pipeline.py (clean import + all 4 sites wired with redis+session+lane)"
  affects:
    - "Phase 6 acceptance bar: cost guard enforced + llm_generations rows on real path"
tech_stack:
  added: []
  patterns:
    - "AsyncMock patching of instructor.AsyncInstructor.create_with_completion for offline testing"
    - "SQLite in-memory engine for LLMGeneration row assertions (no BRAVE_DB_URL required)"
    - "fakeredis.FakeRedis for cost-guard unit testing"
    - "Structural grep assertion as an offline pipeline wiring test"
    - "pytest.mark.skipif on dual env var gate (_HAS_OPENROUTER_KEY AND _HAS_REAL_EXTERNALS)"
key_files:
  created:
    - "tests/unit/clients/__init__.py"
    - "tests/unit/clients/test_real_llm_client.py"
    - "tests/integration/test_real_llm_smoke.py"
  modified:
    - "brave/tasks/pipeline.py"
decisions:
  - "D-07: sqlite in-memory session for T4 LLMGeneration row assertion (fully offline, no BRAVE_DB_URL)"
  - "D-07: structural grep assertion (T5) intentionally runs after Task 2 pipeline edits"
  - "D-07: smoke test uses no redis_client/session (pure transport smoke; wiring verified by offline tests)"
  - "D-07: resequence redis_client construction before RealLLMClient in Sites 3+4 (no logic change, ordering only)"
metrics:
  duration: "~15 minutes"
  completed: "2026-06-18"
  tasks_completed: 2
  files_created: 3
  files_modified: 1
---

# Phase 06 Plan 03: RealLLMClient Tests + Pipeline Wiring Summary

**One-liner:** 5 offline unit tests covering guard/deny/fallback/cost-guard/wiring for RealLLMClient, plus a key-gated smoke test and full pipeline.py cost-guard wiring at all 4 real-path call sites.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Offline unit tests for RealLLMClient (D-07, 5 tests) | 0d3871b | tests/unit/clients/__init__.py, tests/unit/clients/test_real_llm_client.py (created) |
| 2 | Opt-in smoke test + pipeline.py wiring + type: ignore removal | 4a6177e | tests/integration/test_real_llm_smoke.py (created), brave/tasks/pipeline.py (modified) |

## What Was Built

### Task 1 — Offline unit tests (`tests/unit/clients/test_real_llm_client.py`)

Created 5 offline unit tests proving D-07 requirements. All run without env keys, no network:

**T1 — test_guard_raises_when_run_real_externals_false:** Monkeypatches `RUN_REAL_EXTERNALS` to absent/false; asserts `RuntimeError` containing `"run_real_externals=False"` is raised at construction. Confirms the fail-closed guard works.

**T2 — test_deny_block_present_in_openrouter_request:** Sets `RUN_REAL_EXTERNALS=true` + `BRAVE_LLM_OPENROUTER_API_KEY=test-key`, patches `_instructor_client.create_with_completion` with `AsyncMock`, calls `extract()`, and asserts `call_args.kwargs["extra_body"]["provider"]["data_collection"] == "deny"`. Confirms D-04 enforcement on every call.

**T3 — test_primary_slug_notfound_falls_back_to_next_slug:** Patches `_call_slug` to raise `openai.NotFoundError` on the primary slug and succeed on the fallback; asserts exactly 2 calls were made and the second used `"fallback/slug"`. Confirms D-03 slug fallback loop.

**T4 — test_cost_guard_invoked_and_llm_generation_written:** Uses `fakeredis.FakeRedis` (from shared fixture) + SQLite in-memory `Session` (local fixture — no `BRAVE_DB_URL`). Constructs `RealLLMClient(..., redis_client=fake_redis, session=sqlite_session, lane="test")`, patches `create_with_completion` to return fake usage with `cost=0.002`, calls `extract()`, and asserts: 1 `LLMGeneration` row with `usd_cost > 0`, `lane == "test"`, and no prompt content in the row (T-02-04).

**T5 — test_pipeline_outreach_task_passes_redis_and_session_to_real_llm_client:** Reads `brave/tasks/pipeline.py` as text and asserts the expected wired constructor signature appears at least once. Purely structural, no env vars needed. Designed to fail if Task 2 is skipped.

### Task 2 — Smoke test + pipeline.py wiring

**Smoke test (`tests/integration/test_real_llm_smoke.py`):** Module-level `_SMOKE_ENABLED` gate requires both `BRAVE_LLM_OPENROUTER_API_KEY` and `RUN_REAL_EXTERNALS` to be set. Single test `test_smoke_extract_real_openrouter` calls `extract()` with a minimal `_Ping` schema and asserts a valid Pydantic response. Auto-skipped in CI (keyless). No redis_client/session in smoke path.

**pipeline.py wiring (4 sites):**

- All 4 `# type: ignore[import]` stubs on `RealLLMClient` import lines removed
- Sites 1+2 (`discover_atrativo_task`, `sweep_uf_task`): Added `redis_client` construction (`redis_lib.from_url(os.environ.get("BRAVE_DB_REDIS_URL", ...))`) immediately before the `if app_config.run_real_externals:` LLM selection block; `RealLLMClient` now receives `redis_client=redis_client, session=session, lane="atrativos"/"destinos"`
- Sites 3+4 (`outreach_task`, `resume_conversation_task`): Resequenced the existing `redis_client` construction block to appear BEFORE the `RealLLMClient` construction (previously it was AFTER); no logic change — same code, new position
- `FakeLLMClient()` branches at all 4 sites unchanged — offline path zero-cost, unaffected

## Deviations from Plan

None — plan executed exactly as written. The "T5 fails until Task 2 completes" warning in the plan was acknowledged and handled correctly: Task 2 was executed immediately after Task 1, before the first commit that included T5.

Note: Both tasks were committed as separate atomic commits per the plan's task structure, but T5 was naturally confirmed after Task 2 edits since pipeline.py was edited in the same execution session.

## Threat Model Compliance

| Threat ID | Status | Notes |
|-----------|--------|-------|
| T-06-03-01 (API keys in smoke test) | Mitigated | Smoke test reads from `LLMConfig()` env-only; no hardcoded keys; `_HAS_OPENROUTER_KEY` checks presence only |
| T-06-03-02 (CI accidentally running real smoke) | Mitigated | `pytest.mark.skipif(not _SMOKE_ENABLED, ...)` — both key and RUN_REAL_EXTERNALS must be set; confirmed skipped in CI |
| T-06-03-03 (type: ignore removal) | Accepted | `brave/clients/llm.py` exists — removal is correct; import verified by importable check |
| T-06-03-04 (cost guard bypass — missing redis_client) | Mitigated | All 4 real-path sites now pass `redis_client`; `grep -c` returns 4; `pre_dispatch_check` fires before dispatch |
| T-06-03-SC (no new packages) | Accepted | No new packages installed; no package manager invocations |

## Verification Results

- `.venv/bin/python -m pytest tests/unit/clients/test_real_llm_client.py -v` → 5 passed
- `.venv/bin/python -m pytest tests/integration/test_real_llm_smoke.py -v` → 1 skipped (no key)
- `grep -F -c "RealLLMClient(config=app_config.llm, redis_client=redis_client, session=session" brave/tasks/pipeline.py` → 4
- `grep "type: ignore[import]" brave/tasks/pipeline.py | grep RealLLMClient` → empty
- `.venv/bin/python -m pytest tests/ -x --ignore=tests/integration/test_real_llm_smoke.py` → 393 passed, 0 failed

## Known Stubs

None — all tests exercise real code paths (mocked at the network boundary, not with stub data).

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced.

## Self-Check: PASSED

- `tests/unit/clients/test_real_llm_client.py` exists: FOUND
- `tests/integration/test_real_llm_smoke.py` exists: FOUND
- Commit `0d3871b` exists: FOUND
- Commit `4a6177e` exists: FOUND
- All 5 unit tests pass: PASS
- Smoke test skips cleanly: PASS
- grep -c wired sites = 4: PASS
- No type: ignore[import] stubs: PASS
- Full offline suite 393 passed: PASS
