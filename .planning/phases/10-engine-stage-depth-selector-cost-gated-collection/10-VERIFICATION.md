---
phase: 10-engine-stage-depth-selector-cost-gated-collection
verified: 2026-06-23T12:45:00Z
status: passed
score: 7/7 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: none
---

# Phase 10: Engine Stage-Depth Selector (cost-gated collection) Verification Report

**Phase Goal:** Operator selects pipeline depth (`Apenas nascente` | `Nascente → Rio` | `Nascente → Rio → Mar`) on /processo before starting the engine; selection required to enable "Ligar motor"; depth is a real cost boundary; per-entity "nascente" StageBadge variant.
**Verified:** 2026-06-23T12:45:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Requirements ENG-01..ENG-07)

| # | Truth (Requirement) | Status | Evidence |
| --- | --- | --- | --- |
| ENG-01 | /processo exposes 3-option depth selector; "Ligar motor" disabled until a depth is chosen | ✓ VERIFIED | `EngineControl.tsx:21-25` `DEPTH_OPTIONS` (3 values); `:121-146` `role="radiogroup"` + 3 `role="radio"` buttons; `:149` `disabled={pending || !selectedDepth}` (disabled-until-chosen); test `EngineControl.test.tsx` (disabled-guard case) green |
| ENG-02 | Depth persisted in Redis `brave:engine:*`; `/start` accepts depth in body; `/status` exposes it back | ✓ VERIFIED | `engine.py:46` `_DEPTH_KEY="brave:engine:depth"`, `:102-114` `set_depth`, `:117-120` `get_depth`, `:130` `get_status` carries `depth`; router `engine.py:107-120` reads/validates/persists body depth; `:139` 202 echoes depth; `:130` status surfaces it. Client: `engine-api.ts:25,28-32,48,73` + read-back `EngineControl.tsx:171-178` |
| ENG-03 | `Apenas nascente` = Mtur seed → store_raw + §7.6 only; no Rio, no Desmembramento LLM, no atrativos Places (zero external cost) | ✓ VERIFIED | Orchestrator `pipeline.py:1632-1635` dispatches ONLY `sweep_uf.delay` (atrativos never dispatched, regardless of lane); `sweep_uf:814-815` `run_rio=False`/`run_desmembramento=False` under NASCENTE; `:831` LLM client only instantiated when `run_desmembramento`; lane `mtur.py:159,168` store_raw always, Rio gated by `run_rio`. Tests assert `mock_process.call_count==0` (RioRecord=0) + Desmemb skipped |
| ENG-04 | `Nascente → Rio` runs producers + Rio routing but NO Mar promotion and NO WhatsApp gate dispatch | ✓ VERIFIED | `discover_atrativo_task:719` wraps ENTIRE find_contacts fan-out in `if effective_depth != NASCENTE_RIO:` — both `.delay` (`:722`) and inline `.run` (`:724`) suppressed. Test `test_discover_nascente_rio_does_not_kick_contacts_chain` asserts `delay==0 AND run==0`. No promote/push (see ENG-05) |
| ENG-05 | `Nascente → Rio → Mar` = full pipeline incl. idempotent norteia-api Mar push (unchanged contract) | ✓ VERIFIED | nascente_rio_mar kicks the chain (`:719` condition false → fan-out fires, test `delay==2`). No `push_mar.delay`/`.run` anywhere in pipeline.py (grep: 0); Mar push stays on the unchanged human DLQ/WhatsApp finalize path. Test `test_sweep_never_auto_promotes_to_mar` parametrized over all 3 depths asserts `promote_to_mar==0` and `push_mar==[]` |
| ENG-06 | Per-entity "nascente" StageBadge variant for Nascente-only records | ✓ VERIFIED | `StageBadge.tsx:25` `nascente?: boolean` prop; `:96-103` renders "Nascente" chip (CSS-var token, stage-first). 12 StageBadge tests green |
| ENG-07 | 100% offline testable — pytest+fakeredis, Vitest+MSW, no RUN_REAL_EXTERNALS | ✓ VERIFIED | `test_engine_state.py`+`test_engine_depth_gating.py`: 33 passed (fakeredis, RUN_REAL_EXTERNALS unset). Dashboard `EngineControl`+`StageBadge`: 22 passed (MSW). MSW handler `engine.ts:24,33` carries depth |

**Score:** 7/7 truths verified

### Load-Bearing Nuance Verification

| Nuance | Status | Evidence |
| --- | --- | --- |
| (a) No automated promote_to_mar/push_mar added to the sweep under ANY depth | ✓ VERIFIED | `push_mar` defined but never dispatched (`grep push_mar.delay\|push_mar.run` → 0); `promote_to_mar` not called by any sweep producer; nascente_rio_mar differs from nascente_rio ONLY by kicking the find_contacts chain. Test asserts 0 across all 3 depths |
| (b) Under nascente_rio BOTH `.delay` and inline `.run` fallback of find_contacts_task suppressed | ✓ VERIFIED | Single `if effective_depth != NASCENTE_RIO:` at `pipeline.py:719` wraps the entire loop (`.delay` 722 + `.run` 724). Test asserts `delay==0 AND run==0` |
| Required-selection enforced BOTH client AND server | ✓ VERIFIED | Client: `EngineControl.tsx:149` button disabled until selectedDepth. Server: `engine.py:107-112` raises 422 if depth ∉ valid set, BEFORE `start_run`/409 (`:114`), and AFTER `require_steward_or_bearer` (`:85`). Integration tests assert 422-before-409 and 401-before-depth |
| Depth read ONCE in orchestrator, threaded as arg; lanes never read Redis depth | ✓ VERIFIED | `pipeline.py:1623` `effective_depth` resolved once outside loop; threaded via `sweep_uf.delay(uf, depth=...)` / `discover_atrativo_task.delay(uf, depth=...)`. `grep brave:engine:depth mtur.py` → 0; mtur receives `run_rio` kwarg only |

### Required Artifacts

| Artifact | Expected | Status | Details |
| --- | --- | --- | --- |
| `brave/core/engine.py` | depth constants + set/get_depth + get_status | ✓ VERIFIED | Constants `:37-40`, `_DEPTH_KEY :46`, set/get `:102-120`, status `:130` |
| `brave/api/routers/engine.py` | 422-before-409, auth guard, depth on /status | ✓ VERIFIED | `:85` auth dep, `:107-112` 422, `:114` start_run, `:120` set_depth, `:125` dispatch, `:139` echo |
| `brave/tasks/pipeline.py` | depth threaded; nascente Mtur-only; nascente_rio suppresses both paths; no push added | ✓ VERIFIED | `engine_sweep_run:1580-1656`, `sweep_uf:780-848`, `discover_atrativo_task:635-725` |
| `brave/lanes/destinos/mtur.py` | run_rio gate, no Redis depth read | ✓ VERIFIED | `produce:105` keyword-only `run_rio`; `:168` Rio gated; 0 Redis reads |
| `dashboard/lib/engine-api.ts` | EngineDepth + DEPTH_LABELS + depth in types | ✓ VERIFIED | `:25,28,48,73` |
| `dashboard/components/engine/EngineControl.tsx` | selector + disabled-until-chosen + read-back | ✓ VERIFIED | `:121-154,171-178` |
| `dashboard/components/cms/StageBadge.tsx` | nascente variant | ✓ VERIFIED | `:25,96-103` |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| --- | --- | --- | --- |
| Backend depth state + gating offline | `pytest test_engine_state.py test_engine_depth_gating.py` (RUN_REAL_EXTERNALS unset, fakeredis) | 33 passed in 0.89s | ✓ PASS |
| Dashboard selector + badge offline | `bun run test -- EngineControl StageBadge` | 22 passed (10+12) | ✓ PASS |
| No auto Mar push in sweep | `grep push_mar.delay\|push_mar.run pipeline.py` | 0 matches | ✓ PASS |
| Lane never reads Redis depth | `grep brave:engine:depth mtur.py` | 0 matches | ✓ PASS |

### Scope Fence (Deferred items stayed deferred)

| Deferred item | Leaked in? | Evidence |
| --- | --- | --- |
| gov atrativo source / Places-move (Phase B) | ✗ No | discovery_agent.py untouched by phase-10 commits |
| structured hours/price (Phase C) | ✗ No | no schema/model change |
| free-LLM Desmembramento | ✗ No | desmembramento.py untouched; LLM simply gated off under nascente |
| contacts table | ✗ No | no alembic migration in phase commits |
| multichannel | ✗ No | WhatsApp path untouched |
| schema migration / norteia-api contract change | ✗ No | files touched = engine/router/pipeline/mtur + 4 dashboard files + tests only |

Phase-10 implementation commits touched exactly 13 files, all in-scope (engine state, router, orchestrator, mtur lane, 4 dashboard files, 5 test files). No migration, no norteia-api, no new external source.

### Anti-Patterns Found

None. No TBD/FIXME/XXX debt markers in modified files. No stubs — every artifact is substantive, wired, and (for orchestrator gating) data-path-verified by tests asserting call counts.

### Human Verification Required

None — all behavior is programmatically verified by the offline test suites (gating logic via call-count assertions; UI guard via Vitest). No visual/real-time/external-service dependency that grep + tests cannot cover.

### Gaps Summary

No gaps. All seven requirements ENG-01..ENG-07 are delivered in source (not merely SUMMARY-claimed), both load-bearing nuances hold in code and are locked by explicit tests, required-selection is enforced on both client and server with the auth guard and 422-before-409 ordering preserved, depth is read once and threaded down (lanes never read Redis), and every deferred item stayed out of scope.

Note: `tests/integration/test_engine_endpoints.py` requires a live Postgres (`BRAVE_DB_URL`); it passes with the file's own DB setdefault but is outside the requested ENG-07 offline command set. This is expected per project MEMORY (integration tests need BRAVE_DB_URL) and does not affect ENG-07, which is satisfied by the unit + Vitest suites.

---

_Verified: 2026-06-23T12:45:00Z_
_Verifier: Claude (gsd-verifier)_
