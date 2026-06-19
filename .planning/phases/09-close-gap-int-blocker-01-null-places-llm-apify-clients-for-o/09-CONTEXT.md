# Phase 9: Close gap INT-BLOCKER-01 — Null Places/LLM/Apify clients - Context

**Gathered:** 2026-06-19
**Status:** Ready for planning
**Source:** v1.0 Milestone Audit (INT-BLOCKER-01) treated as locked spec

<domain>
## Phase Boundary

This phase closes the single production-packaging BLOCKER found by the v1.0
milestone audit. It is a bounded, behavior-preserving wiring fix — **not** a
feature phase.

**The defect (INT-BLOCKER-01):** Production Celery tasks import `tests.fakes.*`
in their **default** (`run_real_externals=False`) branch. The production wheel
ships only `packages=["brave"]` (`pyproject.toml:62-63`), so under the documented
default config every sweep/discovery/FSM task raises
`ModuleNotFoundError: No module named 'tests'`. It is masked today only because
the repo `.env` forces `RUN_REAL_EXTERNALS=true`. This is a real packaging break,
not a test artifact.

**The fix (audit-prescribed):** Add three in-package, production-safe stub
clients under `brave/clients/` — `NullPlacesClient`, `NullLLMClient`,
`NullApifyClient` — mirroring the existing `NullWhatsAppClient`/`NullMturClient`/
`NullNotebookLMClient`/`NullNorteiaApiClient`. Replace the 8 `tests.fakes.*`
imports in `brave/tasks/pipeline.py` with these Null clients in the offline branch.

**In scope:** the three new Null clients; rewiring the 8 import sites in
`pipeline.py`; a regression guard proving `brave/` never imports `tests`.

**Out of scope:** the two WARNINGs in the audit (silent push-drop in `dlq.py`,
orphaned CMS edit routes) — those are separate, non-blocking issues. Do not
change real-client behavior, the score engine, or any FSM logic.

</domain>

<decisions>
## Implementation Decisions

### New Null clients (locked)
- Create `brave/clients/null_places.py` → `NullPlacesClient` satisfying
  `PlacesClientProtocol`: `async text_search(query, uf) -> list` returns `[]`;
  `async place_details(place_id) -> dict` returns `{}`.
- Create `brave/clients/null_llm.py` → `NullLLMClient` satisfying
  `LLMClientProtocol`: `async extract(prompt, schema, mode="tools") -> Any`
  returns `None`; `async generate(messages, model="claude-sonnet-4-5") -> str`
  returns a fixed canned PT-BR string (mirror `FakeLLMClient`'s default
  `generate_result`).
- Create `brave/clients/null_apify.py` → `NullApifyClient` satisfying
  `ApifyClientProtocol`: `async scrape_ig(handle) -> dict` returns `{}`.
- Each module mirrors `null_mtur.py`/`null_notebooklm.py` exactly: module
  docstring noting production-safety + "lives in brave/ not tests/", no network
  I/O, and a `_check_protocol_compliance()` structural-typing assertion at the
  bottom that binds the class to its protocol from `brave.clients.base`.
- Behavior must be **empty/no-op**, matching the no-fixture defaults the
  production tasks currently construct (`FakePlacesClient()`, `FakeLLMClient()`,
  `FakeApifyClient()` all return empty). This makes the swap behavior-preserving:
  the default offline branch already produces nothing — it just must stop crashing.

### Rewiring pipeline.py (locked)
- Replace every `from tests.fakes.fake_places import FakePlacesClient` →
  `from brave.clients.null_places import NullPlacesClient` and instantiate
  `NullPlacesClient()` in the `else` (offline) branch.
- Same for `fake_llm` → `NullLLMClient`, `fake_apify` → `NullApifyClient`.
- Sites (per audit + grep): `brave/tasks/pipeline.py:662, 673, 809, 896, 1001,
  1002, 1249, 1441`. Confirm by grepping `tests.fakes` after the edit — zero
  matches must remain in `brave/`.
- Do not touch the `if app_config.run_real_externals:` real-client branches.

### Regression guard (locked)
- Add an offline test asserting the production package never reaches into the
  test tree: e.g. grep/AST check that no file under `brave/` contains an
  `import tests` / `from tests` statement (the durable fix for this class of bug).
- Optionally also assert that with `run_real_externals=False` the task client
  selection yields `Null*Client` instances.

### Claude's Discretion
- Exact wording of docstrings, the canned PT-BR string in `NullLLMClient.generate`
  (reuse `FakeLLMClient`'s default), test file placement/naming, and whether the
  guard is grep-based or AST-based.
- Whether `brave/clients/__init__.py` should export the new Null clients (match
  whatever the existing Null clients do).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### The defect spec (source of truth for this phase)
- `.planning/v1.0-MILESTONE-AUDIT.md` — INT-BLOCKER-01 entry (`where`, `fix`,
  `affected_reqs`) and the "Cross-Phase Integration" section.

### Pattern to mirror (existing in-package Null clients)
- `brave/clients/null_whatsapp.py` — the canonical Null pattern named by the audit.
- `brave/clients/null_mtur.py` — data-fetch Null returning `[]` + protocol check.
- `brave/clients/null_notebooklm.py` — data-fetch Null returning `{}` + protocol check.

### Protocols the new clients must satisfy
- `brave/clients/base.py` — `PlacesClientProtocol` (lines 105-133),
  `LLMClientProtocol` (24-72), `ApifyClientProtocol` (154-170).

### Behavior to preserve (current offline-branch fakes)
- `tests/fakes/fake_places.py`, `tests/fakes/fake_llm.py`,
  `tests/fakes/fake_apify.py` — match their no-fixture empty-return defaults.

### Sites to rewire + packaging facts
- `brave/tasks/pipeline.py` — 8 `tests.fakes.*` import sites listed above.
- `pyproject.toml:62-63` — `[tool.hatch.build.targets.wheel] packages = ["brave"]`
  (why the test tree is absent from the wheel).
- `brave/config/settings.py:235-236` — `run_real_externals: bool = False` default.

</canonical_refs>

<specifics>
## Specific Ideas

- The audit explicitly names `NullWhatsAppClient` as the model to mirror.
- Tests today pass only because repo `.env` forces `RUN_REAL_EXTERNALS=true`;
  verification MUST exercise the `run_real_externals=False` path explicitly
  (the suite's documented default) so the regression cannot reappear masked.
- Affected requirements (regression surface): ORCH-01..04, ATR-01..04,
  DEST-01..05, CORE-10, CORE-11, TEST-03.

</specifics>

<deferred>
## Deferred Ideas

- WARNING (silent push-drop): `brave/api/routers/dlq.py:165,224` swallow
  `push_destination_task.delay` failure with no log — separate issue, not this phase.
- WARNING (orphaned edit routes): `PATCH /api/v1/{atrativos,destinos}/{rio_id}/edit`
  have no dashboard consumer — separate issue, not this phase.

</deferred>

---

*Phase: 09-close-gap-int-blocker-01-null-places-llm-apify-clients-for-o*
*Context gathered: 2026-06-19 via audit-as-spec path (/gsd-plan-phase 9)*
