---
phase: 09-close-gap-int-blocker-01-null-places-llm-apify-clients-for-o
verified: 2026-06-19T16:00:00Z
status: passed
score: 4/4
overrides_applied: 0
---

# Phase 09: Close INT-BLOCKER-01 (Null clients) Verification Report

**Phase Goal:** Close INT-BLOCKER-01 — production Celery tasks imported `tests.fakes.*` in their default (`run_real_externals=False`) branch, but the production wheel ships only `packages=["brave"]`, so under the documented default config every sweep/discovery/FSM task raised `ModuleNotFoundError: No module named 'tests'`. The phase adds in-package Null clients, rewires the 8 offline-branch import sites in `brave/tasks/pipeline.py`, and adds a durable regression guard.

**Verified:** 2026-06-19T16:00:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Every Celery sweep/discovery/FSM task runs under `run_real_externals=False` without `ModuleNotFoundError` | VERIFIED | `env -u RUN_REAL_EXTERNALS .venv/bin/python -c "import brave.tasks.pipeline"` exits 0. Full offline suite: 439 passed, 1 skipped, exit 0. |
| 2 | The offline branch instantiates in-package Null clients, not `tests.fakes.*` fakes | VERIFIED | All 8 rewired sites confirmed in `else:` branches of `if app_config.run_real_externals:` blocks. NullPlacesClient count=3, NullLLMClient count=4, NullApifyClient count=1. Real-client branches untouched: RealPlacesClient=6, RealLLMClient=8, RealApifyClient=2. |
| 3 | The `brave` package never contains an `import tests` / `from tests` statement | VERIFIED | `grep -rEn '^\s*(from\|import)\s+tests\b' brave/ --include='*.py'` returns zero matches. |
| 4 | Offline-branch behavior is unchanged: Null clients return the same empty/no-op results the Fake clients returned with no fixtures | VERIFIED | Behavioral assertions passed: `text_search` returns `[]`, `place_details` returns `{}`, `extract` returns `None`, `generate` returns `"Olá! Da Norteia. Poderia confirmar mais detalhes?"` (verbatim from `FakeLLMClient` default), `scrape_ig` returns `{}`. Protocol compliance confirmed via `_check_protocol_compliance()` on all three modules. |

**Score:** 4/4 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/clients/null_places.py` | NullPlacesClient satisfying PlacesClientProtocol | VERIFIED | Contains `class NullPlacesClient`, async `text_search` returns `[]`, async `place_details` returns `{}`, `_check_protocol_compliance()` imports `PlacesClientProtocol` from `brave.clients.base`. No network SDK imports. |
| `brave/clients/null_llm.py` | NullLLMClient satisfying LLMClientProtocol | VERIFIED | Contains `class NullLLMClient`, async `extract` returns `None`, async `generate` returns the canned PT-BR string verbatim, `_check_protocol_compliance()` imports `LLMClientProtocol` from `brave.clients.base`. No network SDK imports. |
| `brave/clients/null_apify.py` | NullApifyClient satisfying ApifyClientProtocol | VERIFIED | Contains `class NullApifyClient`, async `scrape_ig` returns `{}`, `_check_protocol_compliance()` imports `ApifyClientProtocol` from `brave.clients.base`. No network SDK imports. |
| `tests/unit/test_no_test_imports_in_brave.py` | Regression guard: no `import tests`/`from tests` under `brave/` | VERIFIED | Contains `def test_brave_package_never_imports_tests_tree`. Uses compiled regex `re.compile(r"^\s*(from\|import)\s+tests\b")` — NOT bare substring. Test passes (exit 0). |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `brave/tasks/pipeline.py` | `brave.clients.null_places.NullPlacesClient` | `from brave.clients.null_places import NullPlacesClient` in `else:` branch | WIRED | Count=3. Lines 662, 896, 1002 confirmed in `else:` blocks. |
| `brave/tasks/pipeline.py` | `brave.clients.null_llm.NullLLMClient` | `from brave.clients.null_llm import NullLLMClient` in `else:` branch | WIRED | Count=4. Lines 673, 809, 1249, 1441 confirmed in `else:` blocks. |
| `brave/tasks/pipeline.py` | `brave.clients.null_apify.NullApifyClient` | `from brave.clients.null_apify import NullApifyClient` in `else:` branch | WIRED | Count=1. Line 1001 confirmed in `else:` block. |
| `tests/unit/test_no_test_imports_in_brave.py` | `brave/` tree scan | `rglob("*.py")` + regex match | WIRED | Test function walks all `*.py` under `brave/`, regex anchored to import statements. |

---

### Data-Flow Trace (Level 4)

Not applicable. This phase delivers Null client stubs and a wiring fix, not components rendering dynamic data from a data source. The behavioral correctness is verified by direct invocation assertions (Step 7b) rather than data-flow tracing.

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `pipeline.py` imports without `ModuleNotFoundError` under offline default | `env -u RUN_REAL_EXTERNALS .venv/bin/python -c "import brave.tasks.pipeline; print('imports OK')"` | `imports OK` | PASS |
| All three Null clients satisfy their protocols and return correct values | `env -u RUN_REAL_EXTERNALS .venv/bin/python -c "...all behavioral assertions..."` | `All behavioral assertions PASSED` | PASS |
| Regression guard passes on fixed tree | `env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest tests/unit/test_no_test_imports_in_brave.py -q` | 1 passed, exit 0 | PASS |
| Full offline suite green with `RUN_REAL_EXTERNALS` unset | `env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest --tb=no` | `439 passed, 1 skipped, 1 warning in 21.01s`, exit 0 | PASS |

---

### Probe Execution

No phase-declared probes. The phase used inline verification commands (documented in the PLAN verification section). Those commands were run directly and results recorded in Behavioral Spot-Checks above.

---

### Requirements Coverage

The PLAN declares requirements ORCH-01..04, ATR-01..04, DEST-01..05, CORE-10, CORE-11, TEST-03. This phase is a wiring fix — it does not introduce new functionality for those requirements; it closes the packaging break that caused ModuleNotFoundError when the offline default branch executed any of those requirements' pipeline tasks. The regression guard (TEST-03) is directly satisfied by the new test file.

| Requirement | Source Plan | Description (inferred) | Status | Evidence |
|-------------|-------------|----------------------|--------|----------|
| ORCH-01..04 | 09-01-PLAN | Orchestration tasks runnable offline | SATISFIED | `import brave.tasks.pipeline` succeeds; full suite green |
| ATR-01..04 | 09-01-PLAN | Atrativos tasks runnable offline | SATISFIED | Same — no ModuleNotFoundError in atrativos FSM branch |
| DEST-01..05 | 09-01-PLAN | Destinos tasks runnable offline | SATISFIED | Same — no ModuleNotFoundError in destinos sweep branch |
| CORE-10, CORE-11 | 09-01-PLAN | Core packaging integrity | SATISFIED | Zero `from tests`/`import tests` statements in `brave/` |
| TEST-03 | 09-01-PLAN | Regression guard for test-tree imports | SATISFIED | `test_brave_package_never_imports_tests_tree` exists, uses compiled regex, passes |

---

### Anti-Patterns Found

No anti-patterns detected in the modified files.

| File | Pattern Checked | Result |
|------|----------------|--------|
| `brave/clients/null_places.py` | TBD/FIXME/XXX markers | None |
| `brave/clients/null_llm.py` | TBD/FIXME/XXX markers | None |
| `brave/clients/null_apify.py` | TBD/FIXME/XXX markers | None |
| `brave/clients/null_places.py` | Real SDK imports (httpx, openai, anthropic, etc.) | Zero matches |
| `brave/clients/null_llm.py` | Real SDK imports | Zero matches |
| `brave/clients/null_apify.py` | Real SDK imports | Zero matches |
| `brave/tasks/pipeline.py` | `tests.fakes` import statements | Zero matches |
| `tests/unit/test_no_test_imports_in_brave.py` | Bare `"tests.fakes" in` substring check | Not present — uses `re.compile` correctly |

Pipeline.py comment at line ~1068 (`# run_real_externals=False. Test fakes are NEVER imported in production tasks`) references `tests/fakes` in prose, not as an import statement — this is correct and expected per the plan.

---

### Human Verification Required

None. The phase goal is a bounded wiring fix with fully automatable success criteria. All criteria verified programmatically:

- Import absence (grep)
- Module existence and class names (file read + grep)
- Protocol compliance (_check_protocol_compliance() runs without error)
- Return value correctness (asyncio.run assertions)
- Rewire counts (grep -c)
- Regression guard (pytest)
- Full offline suite (pytest)

---

### Gaps Summary

No gaps. All 4 must-have truths verified, all 4 required artifacts present and substantive, all 3 key links wired and confirmed in correct `else:` branches. Full offline suite green (439 passed, 1 skipped) with `RUN_REAL_EXTERNALS` unset.

**INT-BLOCKER-01 is closed.**

---

## Commits Verified

| Hash | Type | Description |
|------|------|-------------|
| `e649b02` | feat | Add NullPlacesClient, NullLLMClient, NullApifyClient in-package stubs |
| `bba889d` | fix | Rewire 8 offline-branch import sites in pipeline.py to Null clients |
| `27c4620` | test | Add regression guard preventing tests-tree imports in brave/ |

---

_Verified: 2026-06-19T16:00:00Z_
_Verifier: Claude (gsd-verifier)_
