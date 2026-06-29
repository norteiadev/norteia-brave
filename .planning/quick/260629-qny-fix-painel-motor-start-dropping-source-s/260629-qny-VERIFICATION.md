---
phase: quick-260629-qny
verified: 2026-06-29T22:50:00Z
status: passed
score: 7/7 must-haves verified
overrides_applied: 0
---

# Phase quick-260629-qny: Fix Painel Motor Start Dropping Source — Verification Report

**Phase Goal:** Painel motor start now sends the selected origem source to POST /api/v1/engine/start. Backend POST /api/v1/engine/source sets active source without starting a run; PainelOrigem onSave activates source (tripadvisor after inject; mtur/google_places → "default"); PainelTopbar start passes source from status into startEngine. EngineControl untouched, taBlocked gate not regressed.

**Verified:** 2026-06-29T22:50:00Z
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | POST /api/v1/engine/source with a valid source persists it to Redis and returns 200 {source: ...} | VERIFIED | `engine.py` lines 239-265: `@router.post("/api/v1/engine/source", status_code=200, dependencies=[Depends(require_steward_or_bearer)])` calls `collection_engine.set_source(redis, source)` and returns `{"source": source}`. Backend test `test_engine_set_source_valid` PASS. |
| 2 | POST /api/v1/engine/source with an invalid source returns 422 before touching engine Redis state | VERIFIED | `engine.py` line 258-262: validates against `collection_engine._VALID_SOURCES`, raises `HTTPException(status_code=422)` before any Redis write. Backend test `test_engine_set_source_invalid_422` PASS (Redis key remains None). |
| 3 | POST /api/v1/engine/source without auth credentials returns 401/403 | VERIFIED | Endpoint declared with `dependencies=[Depends(require_steward_or_bearer)]`. Backend test `test_engine_set_source_no_auth` PASS. |
| 4 | Saving TripAdvisor in PainelOrigem (after successful inject) fires POST /engine/source with {source: 'tripadvisor'} | VERIFIED | `PainelOrigem.tsx` line 169: `activateSource.mutate("tripadvisor")` in `inject.onSuccess`, where `activateSource` mutationFn calls `setEngineSource(src)`. Dashboard test "saving TA fires POST /engine/source {source: tripadvisor}" PASS. |
| 5 | PainelTopbar start mutation sends {depth, source} where source = data?.source ?? 'default' | VERIFIED | `PainelTopbar.tsx` line 139: `mutationFn: (depth: EngineDepth) => startEngine({ depth, source })`. Line 153: `const source: EngineSource = data?.source ?? "default"`. Dashboard test "source=tripadvisor in status: picking a depth fires POST /start with {depth, source: 'tripadvisor'}" PASS. |
| 6 | mtur and google_places onSave paths call setEngineSource('default') before close so a stale 'tripadvisor' source in Redis is not left behind | VERIFIED | `PainelOrigem.tsx` line 211-215: non-TA `onSave` branch calls `activateSource.mutate("default")` before `onClose()`. Dashboard test "saving mtur fires POST /engine/source {source: default}" PASS. |
| 7 | taBlocked gate (source=tripadvisor + no valid session blocks depth menu) is not regressed | VERIFIED | `PainelTopbar.tsx` lines 162-164: `taBlocked = source === "tripadvisor" && (!sessionStatus?.present \|\| (sessionStatus?.expires_in ?? 0) <= 0)`. Line 202: `if (taBlocked) { toast.error(...); return; }`. Test "source=tripadvisor + no valid session blocks depth menu on switch click" PASS. |

**Score:** 7/7 truths verified

### Naming Deviation (Non-Blocking)

The PLAN specified `contains: "setSource.mutate"` for `PainelOrigem.tsx`. The executor renamed the mutation variable from `setSource` to `activateSource` to resolve a compile-time conflict with the existing `useState` setter `const [source, setSource] = useState(...)`. The behavior is identical — `activateSource.mutate("tripadvisor")` and `activateSource.mutate("default")` call `setEngineSource(src)` via the same `useMutation`. Tests assert on HTTP calls intercepted by MSW, not the variable name.

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `tests/unit/api/test_engine_set_source_endpoint.py` | Backend TDD tests for POST /api/v1/engine/source | VERIFIED | Exists; contains `test_engine_set_source_valid`, `test_engine_set_source_default`, `test_engine_set_source_invalid_422`, `test_engine_set_source_no_auth`. All 4 PASS. |
| `brave/api/routers/engine.py` | POST /api/v1/engine/source endpoint — validate+persist without starting a run | VERIFIED | `engine_set_source` handler at lines 239-265. Validates `_VALID_SOURCES`, calls `set_source`, returns `{"source": source}`, no RunHistory row, no dispatch. |
| `dashboard/lib/engine-api.ts` | `setEngineSource` client function | VERIFIED | `setEngineSource(source: EngineSource)` at lines 234-242: calls `apiFetch("api/v1/engine/source", { method: "POST", body: JSON.stringify({ source }) })`. |
| `dashboard/mocks/handlers/engine.ts` | MSW mock for POST /engine/source | VERIFIED | `engineSetSourceSuccess()` at lines 103-107: intercepts `http.post(\`${BASE}/source\`, ...)` and echoes `{ source: body.source }`. |
| `dashboard/components/painel/PainelOrigem.tsx` | Calls setEngineSource in inject.onSuccess (TA) and non-TA onSave branch | VERIFIED | `activateSource.mutate("tripadvisor")` at line 169; `activateSource.mutate("default")` at line 213. Both wired to `setEngineSource`. |
| `dashboard/components/painel/PainelTopbar.tsx` | start mutation sends source from data?.source | VERIFIED | Line 139: `(depth: EngineDepth) => startEngine({ depth, source })`. `source` in closure from line 153. |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `PainelTopbar.tsx` start.mutationFn | `engine-api.ts` startEngine | `{ depth, source }` — source = data?.source ?? 'default' | VERIFIED | Line 139: `startEngine({ depth, source })` |
| `PainelOrigem.tsx` inject.onSuccess | `setEngineSource('tripadvisor')` | `activateSource.mutate("tripadvisor")` immediately after inject success | VERIFIED | Line 169 in inject.onSuccess block |
| `PainelOrigem.tsx` non-TA onSave branch | `setEngineSource('default')` | `activateSource.mutate("default")` in mtur/google_places else branch, before onClose | VERIFIED | Lines 211-215 |
| `engine.py` engine_set_source | `collection_engine.set_source(redis, source)` | validates against `_VALID_SOURCES`, 422 on invalid | VERIFIED | Lines 258-263 |
| GET /api/v1/engine/status | brave:engine:source Redis key | `get_source(redis)` → echoed as data?.source in PainelTopbar | VERIFIED | `brave/core/engine.py` line 169: `"source": get_source(redis)` in `get_status()` |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `PainelTopbar.tsx` | `source` | `data?.source` from `useQuery(engineKeys.status)` → GET /api/v1/engine/status → `get_source(redis)` | Yes — reads `brave:engine:source` Redis key written by `set_source()` | FLOWING |
| `engine.py` engine_set_source | `source` | POST body → validated → `collection_engine.set_source(redis, source)` → `redis.set(_SOURCE_KEY, source)` | Yes — real Redis write, confirmed by test asserting `get_source(rc) == "tripadvisor"` | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 4 backend tests (valid, default, 422, no-auth) | `.venv/bin/python -m pytest tests/unit/api/test_engine_set_source_endpoint.py -v` | 4 passed | PASS |
| Regression: existing engine tests | `.venv/bin/python -m pytest tests/unit/api/test_engine_source.py tests/unit/api/test_engine_latch.py -v` | 10 passed | PASS |
| Full dashboard suite | `cd dashboard && bun run test` | 287 passed (42 files) | PASS |
| PainelTopbar taBlocked + source propagation | `bun run test PainelTopbar.test.tsx --reporter=verbose` | 16/16 passed (including taBlocked gate + new source test) | PASS |

---

### Anti-Patterns Found

None. No TBD/FIXME/XXX markers found in modified files. No placeholder returns. No empty implementations. No hardcoded empty data passed to renderers.

---

### Human Verification Required

None. All must-haves are verifiable programmatically. Tests cover:
- HTTP call payloads (source in POST /start body, POST /source body)
- Redis persistence (get_source after set_source)
- Auth enforcement (401/403 without credentials)
- taBlocked gate behavior

---

### Gaps Summary

No gaps. All 7 must-have truths are verified. Phase goal is achieved.

---

_Verified: 2026-06-29T22:50:00Z_
_Verifier: Claude (gsd-verifier)_
