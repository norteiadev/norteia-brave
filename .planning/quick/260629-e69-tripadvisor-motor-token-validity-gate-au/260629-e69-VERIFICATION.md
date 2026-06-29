---
phase: quick-260629-e69
verified: 2026-06-29T10:55:00Z
status: passed
score: 9/9 must-haves verified
overrides_applied: 0
---

# Quick Task 260629-e69: TA Motor Token-Validity Gate Verification

**Task Goal:** Replace 260628-m1n auto-resume machinery with operator-gated, token-validity-coupled motor policy. R1: session expiry turns engine OFF. R2: source=tripadvisor start rejected (409) without valid TTL. Auto-resume artifacts gone. Phase-15 bulk offset-resume preserved. Suites green.
**Verified:** 2026-06-29T10:55:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | POST /engine/start source=tripadvisor + no valid session → 409 PT-BR detail | VERIFIED | `engine.py` lines 171-180: `if source == "tripadvisor": _ta_ttl = redis.ttl(BRAVE_TA_SESSION_KEY); if _ta_ttl != -1 and _ta_ttl <= 0: raise HTTPException(409, detail="Motor TripAdvisor requer uma sessão com TTL válido — injete um cURL primeiro.")` |
| 2 | POST /engine/start source=tripadvisor + present+TTL>0 session → 202 | VERIFIED | Same gate: `_ta_ttl > 0` passes through to `start_run`; test `test_ta_start_valid_session_returns_202` confirms 202 |
| 3 | POST /engine/start source=default NOT affected by TA session check | VERIFIED | Gate is guarded by `if source == "tripadvisor":` — default path goes straight to `start_run`. Test `test_default_source_no_session_returns_202` confirms 202 without session |
| 4 | sweep_tripadvisor SessionMissing/Expired except sets engine enabled=False + state=idle | VERIFIED | `pipeline.py` lines 1129-1135: `_r1_rc = rc if rc is not None else _r1_redis.from_url(...); collection_engine.set_enabled(_r1_rc, False); collection_engine.mark_idle(_r1_rc)` — both bulk (rc) and per-UF (fresh url) paths covered |
| 5 | ta_resume_watch task, ta-resume-watch beat entry, resume.py all GONE | VERIFIED | `resume.py` deleted; `beat_schedule.py` has no ta-resume-watch entry (file ends at line 67 with only sweep-UF/atrativos entries); grep on `brave/` for all removed symbols returns 0 results |
| 6 | PainelTopbar switch click source=tripadvisor + no valid session → toast, depth menu stays closed | VERIFIED | `PainelTopbar.tsx` lines 200-205: `if (taBlocked) { toast.error(...); return; }` — `setDepthMenuOpen` never called; test `source=tripadvisor + no valid session blocks depth menu` confirms |
| 7 | PainelTopbar switch click source=tripadvisor + valid session → depth menu opens normally | VERIFIED | `taBlocked = source === "tripadvisor" && (!sessionStatus?.present \|\| expires_in <= 0)` — false when present+TTL>0; test `source=tripadvisor + valid session → switch click opens depth menu` confirms |
| 8 | 409 from POST /engine/start surfaces backend detail message (not hardcoded) | VERIFIED | `explainError` line 75: `if (err.status === 409) return err.message \|\| "Motor já está em execução."` — backend PT-BR detail is forwarded; test `409 from startEngine with TA detail message shows the backend message` confirms |
| 9 | All offline suites green | VERIFIED | Backend: 557 passed, 5 skipped (0 failures). Dashboard: 284 passed, 42 test files (0 failures) |

**Score:** 9/9 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/lanes/tripadvisor/resume.py` | DELETED | VERIFIED — MISSING | File confirmed absent |
| `tests/unit/api/test_ta_auto_resume.py` | DELETED | VERIFIED — MISSING | File confirmed absent |
| `tests/unit/lanes/tripadvisor/test_resume.py` | DELETED | VERIFIED — MISSING | File confirmed absent |
| `tests/unit/lanes/tripadvisor/test_sweep_progress_resume.py` | DELETED | VERIFIED — MISSING | File confirmed absent |
| `brave/lanes/tripadvisor/sweep_progress.py` | RESUMING/claim_resume/get_resume_params/is_paused_needs_bootstrap REMOVED; get_resume_offset KEPT; start() reverted | VERIFIED | File has no RESUMING constant, no _RESUME_CLAIM_KEY, no _F_DEPTH/_F_GEO_ID/_F_TARGET_MAX_PAGES, no is_paused_needs_bootstrap/claim_resume/get_resume_params. start() signature is `(redis, pages_total, resume_from_offset=0)`. get_resume_offset() present at line 176 |
| `brave/api/routers/engine.py` | R2 gate before start_run for source=tripadvisor | VERIFIED | Lines 171-180: gate is BEFORE `start_run` call at line 182; source=default path bypasses it entirely |
| `brave/tasks/pipeline.py` | ta_resume_watch REMOVED; R1 set_enabled(False)+mark_idle in except block | VERIFIED | No ta_resume_watch task found; R1 at lines 1129-1135 in SessionMissing/Expired except block; sweep_progress.start() at line 1044 has no extra kwargs |
| `brave/tasks/beat_schedule.py` | ta-resume-watch entry REMOVED | VERIFIED | File ends at line 67; only sweep-{uf}-daily and sweep-atrativos-{uf}-daily entries present |
| `brave/api/routers/tripadvisor_session.py` | maybe_resume_bulk_sweep import+call REMOVED; "resuming" removed from Literal | VERIFIED | No maybe_resume_bulk_sweep in imports (lines 1-34); TASweepProgressResponse.state at line 124: `Literal["running", "done", "stopped_needs_bootstrap", "idle"]` |
| `dashboard/components/painel/PainelTopbar.tsx` | taBlocked gate, 409 surfacing, auto-off toast | VERIFIED | taBlocked lines 160-162; onToggleMotor guard lines 200-205; explainError 409 branch line 75; auto-off useEffect lines 182-191 |
| `tests/unit/api/test_ta_validity_gate.py` | 3 R2 tests: no-session 409, valid-session 202, default unaffected | VERIFIED | File present, all 3 test functions confirmed |
| `tests/unit/tasks/test_sweep_tripadvisor.py` | 2 R1 tests: SessionMissing+Expired each turn engine off | VERIFIED | R1 tests found (class TestR1EngineOffOnSessionExpiry, both functions confirmed) |
| `dashboard/components/painel/__tests__/PainelTopbar.test.tsx` | 3 new gate tests | VERIFIED | All 3 tests confirmed by grep (taBlocked no-session, valid-session, 409 detail) |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `engine.py:engine_start` | `brave:ta:session` (Redis key) | `redis.ttl(BRAVE_TA_SESSION_KEY)` before `start_run` | VERIFIED | Confirmed at lines 172-180; import of BRAVE_TA_SESSION_KEY is inline (noqa PLC0415) |
| `pipeline.py:sweep_tripadvisor except SessionMissing/Expired` | `collection_engine.set_enabled + mark_idle` | `_r1_rc = rc if rc is not None else redis.from_url(...)` | VERIFIED | Both bulk (rc present) and per-UF (rc=None, fresh url) paths at lines 1131-1135 |
| `PainelTopbar.tsx:onToggleMotor` | `taBlocked` (source===tripadvisor && !valid session) | `sessionStatus` query reuse `(present && expires_in > 0)` | VERIFIED | taBlocked at lines 160-162; guard in onToggleMotor at lines 200-205 with toast.error and return |

---

## Removal Completeness

Grep for all 260628-m1n symbols across `brave/` source (excluding `__pycache__`):

```
grep -r "ta_resume_watch|maybe_resume_bulk_sweep|claim_resume|is_paused_needs_bootstrap|get_resume_params|RESUMING" brave/ → 0 results
```

Result: CLEAN — no live references remain.

---

## Phase-15 Offset-Resume Preservation

`get_resume_offset` confirmed present in `brave/lanes/tripadvisor/sweep_progress.py` (lines 176-182).

Bulk offset-resume call in `pipeline.py` at lines 1044-1048:
```python
sweep_progress.start(
    rc,
    pages_total=334,
    resume_from_offset=_resume_offset,
)
```
No `depth`/`geo_id`/`target_max_pages` kwargs — reverted correctly.

---

## Behavioral Spot-Checks

| Behavior | Check | Result | Status |
|----------|-------|--------|--------|
| Backend suite: 557 tests pass | `.venv/bin/python -m pytest tests/unit -p no:warnings` | 557 passed, 5 skipped | PASS |
| Dashboard suite: 284 tests pass | `bun run test` | 284 passed, 42 files | PASS |
| R2 gate new tests (3) | `tests/unit/api/test_ta_validity_gate.py` | All in 557-pass total | PASS |
| R1 engine-off tests (2) | `tests/unit/tasks/test_sweep_tripadvisor.py::TestR1` | All in 557-pass total | PASS |
| Dashboard gate tests (3) | `PainelTopbar.test.tsx` (15 total, 3 new) | All in 284-pass total | PASS |

---

## Anti-Patterns Found

None. No TBD/FIXME/XXX markers, no placeholder returns, no hardcoded empty data in modified files.

---

## Human Verification Required

None. All goal criteria are verifiable programmatically and confirmed by running suites.

---

## Gaps Summary

No gaps. All 9 observable truths verified. Both offline suites green.

---

_Verified: 2026-06-29T10:55:00Z_
_Verifier: Claude (gsd-verifier)_
