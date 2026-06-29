---
phase: quick-260629-qny
plan: 01
type: tdd
wave: 1
depends_on: []
files_modified:
  - tests/unit/api/test_engine_set_source_endpoint.py
  - brave/api/routers/engine.py
  - dashboard/lib/engine-api.ts
  - dashboard/mocks/handlers/engine.ts
  - dashboard/components/painel/PainelOrigem.tsx
  - dashboard/components/painel/PainelTopbar.tsx
  - dashboard/components/painel/__tests__/PainelOrigem.test.tsx
  - dashboard/components/painel/__tests__/PainelTopbar.test.tsx
autonomous: true
requirements: [QNY-01]

must_haves:
  truths:
    - "POST /api/v1/engine/source with a valid source persists it to Redis and returns 200 {source: ...}"
    - "POST /api/v1/engine/source with an invalid source returns 422 before touching engine Redis state"
    - "POST /api/v1/engine/source without auth credentials returns 401/403"
    - "Saving TripAdvisor in PainelOrigem (after successful inject) fires POST /engine/source with {source: 'tripadvisor'}"
    - "PainelTopbar start mutation sends {depth, source} where source = data?.source ?? 'default'"
    - "mtur and google_places onSave paths call setEngineSource('default') before close so a stale 'tripadvisor' source in Redis is not left behind"
    - "taBlocked gate (source=tripadvisor + no valid session blocks depth menu) is not regressed"
  artifacts:
    - path: "tests/unit/api/test_engine_set_source_endpoint.py"
      provides: "Backend TDD tests (RED first) for POST /api/v1/engine/source"
      contains: "test_engine_set_source_valid, test_engine_set_source_invalid_422, test_engine_set_source_no_auth"
    - path: "brave/api/routers/engine.py"
      provides: "POST /api/v1/engine/source endpoint — validate+persist without starting a run"
      contains: "engine_set_source"
    - path: "dashboard/lib/engine-api.ts"
      provides: "setEngineSource client function"
      contains: "setEngineSource"
    - path: "dashboard/mocks/handlers/engine.ts"
      provides: "MSW mock for POST /engine/source"
      contains: "engineSetSourceSuccess"
    - path: "dashboard/components/painel/PainelOrigem.tsx"
      provides: "Calls setEngineSource('tripadvisor') in inject.onSuccess; calls setEngineSource('default') in non-TA onSave branch before onClose"
      contains: "setSource.mutate"
    - path: "dashboard/components/painel/PainelTopbar.tsx"
      provides: "start mutation sends source from data?.source"
      contains: "startEngine({ depth, source })"
  key_links:
    - from: "PainelTopbar.tsx start.mutationFn"
      to: "engine-api.ts startEngine"
      via: "{ depth, source } — source = data?.source ?? 'default'"
      pattern: "startEngine\\(\\{\\s*depth,\\s*source"
    - from: "PainelOrigem.tsx inject.onSuccess"
      to: "setEngineSource('tripadvisor')"
      via: "setSource.mutate('tripadvisor') immediately after inject success"
      pattern: "setSource\\.mutate"
    - from: "PainelOrigem.tsx non-TA onSave branch"
      to: "setEngineSource('default')"
      via: "setSource.mutate('default') in mtur/google_places else branch, before onClose"
      pattern: "setSource\\.mutate\\(\"default\"\\)"
    - from: "engine.py engine_set_source"
      to: "collection_engine.set_source(redis, source)"
      via: "validates against _VALID_SOURCES, 422 on invalid"
      pattern: "collection_engine\\.set_source"
    - from: "GET /api/v1/engine/status"
      to: "brave:engine:source Redis key"
      via: "get_source(redis) → echoed as data?.source in PainelTopbar"
      pattern: "get_source"
---

<objective>
Fix: Painel motor start drops source — selected TripAdvisor origem never reaches POST /api/v1/engine/start.

Root causes:
1. No endpoint to persist the active collection source outside of /start (so PainelOrigem cannot activate it).
2. PainelOrigem.onSave for TA only calls injectTASession — it never tells the backend which source is active.
3. PainelTopbar start mutation sends only {depth} — source is never included.

Fix: (1) add POST /api/v1/engine/source to set the active source without starting a run; (2) PainelOrigem calls it after a successful TA inject, and also calls it with "default" when the operator saves mtur/google_places (so a stale tripadvisor source cannot linger in Redis); (3) PainelTopbar start reads data?.source from /status and passes it into startEngine.

Purpose: Ensure the selected origem source (tripadvisor) actually reaches the sweep orchestrator so sweep_tripadvisor runs instead of sweep_uf.
Output: Passing backend + dashboard TDD tests; the running motor uses the correct lane.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/quick/260629-qny-fix-painel-motor-start-dropping-source-s/260629-qny-CONTEXT.md
@.planning/STATE.md

<!-- Interface contracts for the executor -->
<interfaces>
<!-- brave/core/engine.py -->
_VALID_SOURCES = frozenset({"default", "tripadvisor"})
_SOURCE_KEY = "brave:engine:source"

def set_source(redis, source: str) -> None:
    # raises ValueError on invalid; never writes invalid values
    ...

def get_source(redis) -> str | None:
    # returns the persisted source, or None when absent/corrupt
    ...

<!-- brave/api/routers/engine.py auth deps already in scope -->
from brave.api.deps import get_redis, require_steward_or_bearer
from brave.core import engine as collection_engine
# engine_start pattern for source validation (lines 163-168):
#   source = body.get("source", "default")
#   if source not in collection_engine._VALID_SOURCES:
#       raise HTTPException(status_code=422, detail="source must be 'default' or 'tripadvisor'")

<!-- dashboard/lib/engine-api.ts -->
export type EngineSource = "default" | "tripadvisor";
export function startEngine(body?: { ufs?: string[]; lane?: string; depth?: EngineDepth; source?: EngineSource }): Promise<EngineActionResult>
export function injectTASession(body: InjectTASessionBody): Promise<InjectTASessionResult>
export const engineKeys = { status: ["engine", "status"] as const }
export const taSessionKeys = { status: ["ta", "session", "status"] as const }

<!-- PainelTopbar.tsx existing source read (line 151) -->
const source: EngineSource = data?.source ?? "default";
// start mutation (line 136-141) currently:
//   mutationFn: (depth: EngineDepth) => startEngine({ depth })
// FIX: mutationFn: (depth: EngineDepth) => startEngine({ depth, source })

<!-- PainelOrigem.tsx existing inject mutation (lines 151-173) -->
// inject.onSuccess currently: toast, invalidate taSessionKeys, onClose()
// FIX TA branch: also fire setSource.mutate("tripadvisor") before onClose()
// FIX non-TA else branch: also fire setSource.mutate("default") before onClose()

<!-- dashboard/mocks/handlers/engine.ts BFF prefix -->
const BASE = "http://localhost:3000/api/api/v1/engine";
// new handler: http.post(`${BASE}/source`, ...)

<!-- Existing backend test fixture pattern (tests/unit/api/test_engine_source.py) -->
// @pytest.fixture(autouse=True) _env: sets BRAVE_DASHBOARD_BEARER_TOKEN + BRAVE_STEWARD_SECRET + BRAVE_USE_FAKEREDIS
// @pytest.fixture client: get_redis().flushall(), TestClient(app, raise_server_exceptions=False)
// set-source endpoint does NOT touch get_db (no RunHistory row) — no db override needed
</interfaces>
</context>

<tasks>

<task type="tdd">
  <name>Task 1 (RED→GREEN): Backend — POST /api/v1/engine/source endpoint</name>
  <files>tests/unit/api/test_engine_set_source_endpoint.py, brave/api/routers/engine.py</files>
  <behavior>
    - test_engine_set_source_valid: POST /api/v1/engine/source {source: "tripadvisor"} with steward auth → 200, body contains {source: "tripadvisor"}, get_source(redis) == "tripadvisor"
    - test_engine_set_source_default: POST /api/v1/engine/source {source: "default"} → 200, persists "default"
    - test_engine_set_source_invalid_422: POST /api/v1/engine/source {source: "mtur"} → 422, Redis source key untouched (still None)
    - test_engine_set_source_no_auth: POST /api/v1/engine/source {source: "tripadvisor"} with no headers → 401 or 403 (non-2xx)
  </behavior>
  <action>
    RED: Create `tests/unit/api/test_engine_set_source_endpoint.py`. Model the fixture setup after `tests/unit/api/test_engine_source.py` (BRAVE_USE_FAKEREDIS=1, monkeypatch env, TestClient). The set-source endpoint has no RunHistory write so no get_db override is needed — the fixture is simpler than test_engine_source's client fixture.

    Run tests — all four must FAIL (endpoint does not exist yet).

    GREEN: Add `engine_set_source` handler to `brave/api/routers/engine.py` immediately after `engine_stop`:

    ```
    POST /api/v1/engine/source
    auth: require_steward_or_bearer (same dep as start/stop)
    body: dict = Body(default={}) — read body.get("source")
    validate: if source not in collection_engine._VALID_SOURCES → HTTPException 422 "source must be 'default' or 'tripadvisor'"
    persist: collection_engine.set_source(redis, source)
    log: logger.info("engine_source_set", source=source)
    return: {"source": source}
    ```

    Status code: 200 (not 202 — this is a configuration write, not a long-running dispatch).
    No RunHistory row. No start_run call. No engine state mutation.

    Run tests — all four must PASS.
  </action>
  <verify>
    <automated>.venv/bin/python -m pytest tests/unit/api/test_engine_set_source_endpoint.py -v</automated>
  </verify>
  <done>Four tests pass: valid→200+persisted, default→200+persisted, invalid→422+no mutation, no-auth→non-2xx. Existing engine test suites still pass: `.venv/bin/python -m pytest tests/unit/api/test_engine_source.py tests/unit/api/test_engine_latch.py -v`</done>
</task>

<task type="tdd">
  <name>Task 2 (RED→GREEN): Dashboard — setEngineSource client + PainelOrigem + PainelTopbar wiring</name>
  <files>
    dashboard/lib/engine-api.ts,
    dashboard/mocks/handlers/engine.ts,
    dashboard/components/painel/__tests__/PainelOrigem.test.tsx,
    dashboard/components/painel/__tests__/PainelTopbar.test.tsx,
    dashboard/components/painel/PainelOrigem.tsx,
    dashboard/components/painel/PainelTopbar.tsx
  </files>
  <behavior>
    PainelTopbar new test:
    - "source=tripadvisor in status: picking a depth fires POST /start with {depth, source: 'tripadvisor'}": server.use(engineStatus({ source: "tripadvisor", state: "idle", enabled: false }), taSessionStatus({ present: true, expires_in: 1200 }), engineSetSourceSuccess()); intercept POST /start → capture body. Click switch → depth menu → pick "nascente_rio". Assert startBody.source === "tripadvisor" AND startBody.depth === "nascente_rio".

    PainelOrigem new tests:
    - "saving TA (after inject success) fires POST /engine/source with {source: 'tripadvisor'}": server.use(taSessionStatus(), http.post(TA_INJECT_URL, () => HttpResponse.json({ status: "ready" })), http.post(ENGINE_SOURCE_URL, async ({ request }) => { captured = await request.json(); return HttpResponse.json({ source: "tripadvisor" }); })). Select TA, paste SAMPLE_CURL, click Salvar. waitFor(() => expect(captured).toMatchObject({ source: "tripadvisor" })).
    - "saving mtur fires POST /engine/source with {source: 'default'}": server.use(taSessionStatus(), http.post(ENGINE_SOURCE_URL, async ({ request }) => { captured = await request.json(); return HttpResponse.json({ source: "default" }); })). Select mtur (already default), click Salvar. waitFor(() => expect(captured).toMatchObject({ source: "default" })).
  </behavior>
  <action>
    RED: Write the two new PainelOrigem tests and the one new PainelTopbar test in their respective test files. Add `ENGINE_SOURCE_URL` constant to PainelOrigem.test.tsx ("http://localhost:3000/api/api/v1/engine/source"). Add `engineSetSourceSuccess` import reference to PainelTopbar.test.tsx (the helper will be added to mocks/handlers/engine.ts during GREEN). Run tests — all three must FAIL.

    Before running the full test suite, make these two surgical fixes to existing tests to prevent regressions:

    Fix 1 — PainelTopbar.test.tsx line 88: change
      `expect(startBody).toEqual({ depth: "nascente_rio" });`
    to
      `expect(startBody).toMatchObject({ depth: "nascente_rio" });`
    Rationale: after the fix, startBody contains { depth, source } where source="default" (status.source is null in that test). The strict toEqual fails because the object now has an extra key. toMatchObject asserts the required fields without being broken by additional ones.

    Fix 2 — PainelOrigem.test.tsx existing "submits the parsed cURL body..." test (line 53): add `engineSetSourceSuccess()` to the server.use() call alongside the existing taSessionStatus() and TA_INJECT_URL handler. After the fix, inject.onSuccess fires setSource.mutate("tripadvisor") which hits POST /engine/source — without a handler, MSW emits an unhandled-request warning and the test may log noise or flake.

    GREEN (in this order):

    1. `dashboard/lib/engine-api.ts` — add after `injectTASession`:
       ```
       export function setEngineSource(source: EngineSource): Promise<{ source: EngineSource }> {
         return apiFetch<{ source: EngineSource }>("api/v1/engine/source", {
           method: "POST",
           headers: { "Content-Type": "application/json" },
           body: JSON.stringify({ source }),
         });
       }
       ```

    2. `dashboard/mocks/handlers/engine.ts` — add helper (export it):
       ```
       export function engineSetSourceSuccess() {
         return http.post(`${BASE}/source`, async ({ request }) => {
           const body = (await request.json()) as { source: EngineSource };
           return HttpResponse.json({ source: body.source });
         });
       }
       ```

    3. `dashboard/components/painel/PainelOrigem.tsx` — add setSource mutation and wire it. Import `engineKeys` and `setEngineSource` from engine-api. Add a second mutation:
       ```
       const setSource = useMutation({
         mutationFn: (src: EngineSource) => setEngineSource(src),
         onSuccess: () => {
           void qc.invalidateQueries({ queryKey: engineKeys.status });
         },
       });
       ```
       In `inject.onSuccess`, after `invalidateQueries({ queryKey: taSessionKeys.status })`, add:
       `setSource.mutate("tripadvisor");`
       Keep `onClose()` at the end of inject.onSuccess as-is.

       In the non-TA `else` branch of `onSave` (the mtur/google_places path), add
       `setSource.mutate("default");`
       BEFORE the existing `onClose()` call. This ensures that when an operator switches back from TripAdvisor to mtur/google_places and saves, the Redis source key is set to "default" and does not leave a stale "tripadvisor" value that would route the next start to the wrong lane.

    4. `dashboard/components/painel/PainelTopbar.tsx` — change one line in the start mutation:
       `mutationFn: (depth: EngineDepth) => startEngine({ depth, source }),`
       `source` is already in scope from line 151: `const source: EngineSource = data?.source ?? "default";`. No other changes.

    Run tests:
    `cd dashboard && bun run test --reporter=verbose 2>&1 | tail -30`
    All new tests must pass. All existing PainelTopbar and PainelOrigem tests must pass (taBlocked gate tests particularly important — verify they still pass).
  </action>
  <verify>
    <automated>cd /Users/leandro/Projects/norteia/norteia-brave/dashboard && bun run test 2>&1 | tail -20</automated>
  </verify>
  <done>Full dashboard suite passes (previously 276/276, now 279/279 minimum with 3 new tests). The three new tests pass: (a) start body includes source=tripadvisor when status.source=tripadvisor; (b) PainelOrigem TA save fires POST /engine/source with source=tripadvisor; (c) PainelOrigem mtur save fires POST /engine/source with source=default. Existing tests updated: PainelTopbar line 88 uses toMatchObject; PainelOrigem inject-success test registers engineSetSourceSuccess handler. taBlocked gate tests (source=tripadvisor + no valid session blocks depth menu; 409 detail toast) still pass.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| dashboard → BFF | Bearer header required; BFF forwards to FastAPI |
| BFF → FastAPI /engine/source | Steward or Bearer; any unauthenticated or invalid source is rejected |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-qny-01 | Tampering | POST /api/v1/engine/source | mitigate | require_steward_or_bearer (same dep as /start and /stop); 422 on invalid source so mtur/google_places/unknown cannot be persisted; set_source() itself also raises ValueError as a second-layer guard |
| T-qny-02 | Elevation of Privilege | /engine/source → set arbitrary lane | accept | _VALID_SOURCES is a frozenset of two known values; no new spend is triggered (endpoint does not call start_run, set_enabled, or dispatch tasks) |
| T-qny-03 | Spoofing | Dashboard source read | accept | source echoed from GET /status Redis read; if Redis key is absent, get_source returns None → startEngine body falls back to "default" via data?.source ?? "default" |
</threat_model>

<verification>
Backend (offline, no keys):
```
.venv/bin/python -m pytest tests/unit/api/test_engine_set_source_endpoint.py -v
.venv/bin/python -m pytest tests/unit/api/test_engine_source.py tests/unit/api/test_engine_latch.py -v
```

Dashboard (offline, Vitest+MSW):
```
cd dashboard && bun run test
```

Smoke check (end-to-end narrative):
1. Operator opens Origem modal → selects TripAdvisor → pastes cURL → clicks Salvar.
2. PainelOrigem fires injectTASession → 200 → fires setEngineSource("tripadvisor") → 200 → closes.
3. PainelTopbar polls /status → data.source = "tripadvisor".
4. Operator clicks switch → picks depth → startEngine({ depth: "nascente_rio", source: "tripadvisor" }) → POST /start with source=tripadvisor.
5. Backend receives source=tripadvisor → set_source persists it → engine_sweep_run.delay(source="tripadvisor") → sweep_tripadvisor runs.

Switch-back scenario:
1. Operator opens Origem modal → selects mtur → clicks Salvar.
2. PainelOrigem non-TA branch fires setEngineSource("default") → 200 → closes.
3. Redis source key is now "default" — next start uses Places lane, not TA.
</verification>

<success_criteria>
- POST /api/v1/engine/source exists and is reachable (4 backend tests green)
- Full dashboard suite passes with 3 new tests covering source propagation
- No regression in taBlocked gate (source=tripadvisor + expired session blocks depth menu)
- mtur/google_places onSave calls setEngineSource("default") so stale Redis source is cleared
- Existing PainelTopbar line-88 test updated to toMatchObject (non-breaking); existing PainelOrigem inject-success test registers engineSetSourceSuccess handler
- EngineControl.tsx (/processo) is untouched
</success_criteria>

<output>
Create `.planning/quick/260629-qny-fix-painel-motor-start-dropping-source-s/260629-qny-01-SUMMARY.md` when done.
</output>
