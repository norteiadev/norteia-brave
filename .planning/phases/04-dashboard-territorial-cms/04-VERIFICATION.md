---
phase: 04-dashboard-territorial-cms
verified: 2026-06-16T21:10:00Z
status: passed
score: 6/6 success criteria fully verified (DASH-03 gap closed by plan 04-10)
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 5/6
  gaps_closed:
    - "The WhatsApp gate UI works the aguardando_consulta_whatsapp queue (approve/reject) WITH ramp/quality context (DASH-03 / Success Criterion 3) — ramp-context read endpoint now implemented and data-backed"
  gaps_remaining: []
  regressions: []
gaps: []
deferred: []
---

# Phase 4: Dashboard (Territorial CMS) Verification Report

**Phase Goal:** Operators run the entire pipeline from a Next.js territorial CMS that consumes the FastAPI REST surface (never the DB directly): monitoring layer health, working the DLQ batch-by-state with per-criterion explainability, gating WhatsApp outreach, and viewing conversations, funnels, and LLM cost — all behind Bearer-header auth.
**Verified:** 2026-06-16T21:10:00Z
**Status:** passed
**Re-verification:** Yes — after gap closure (plan 04-10 closed the single DASH-03 ramp/quality-context gap)

> **MVP-mode note:** ROADMAP marks this phase `Mode: mvp`, but the phase goal is a descriptive
> goal, not a User Story (`As a … I want to … so that …`). Per the MVP guard the User-Flow
> Coverage table is not applicable; verification proceeds against the 5 ROADMAP Success Criteria
> (the contract) and the 6 DASH requirements. This is a recorded observation, not a blocker.

## Re-verification Summary

The initial verification (2026-06-16T20:47Z) found **one** gap: DASH-03 / Success Criterion #3 was
partial — the gate's `RampContext.tsx` panel fetched `GET /api/v1/atrativos/whatsapp/ramp-context`,
an endpoint the backend never exposed, so the panel always degraded to the "indisponível" fallback
(Level-4 HOLLOW artifact). Plan **04-10** closed it. Re-verified against the codebase (not the
SUMMARY):

- **Backend endpoint now exists** — `brave/api/routers/atrativos_gate.py:182` `get_ramp_context`,
  decorated `@router.get("/api/v1/atrativos/whatsapp/ramp-context", dependencies=[Depends(require_bearer)])`.
  Redis-only (no DB session injected): `redis.get(ramp_key(None))` → `used` (0 if key absent),
  `remaining = max(0, daily_cap - used)`, `is_quality_red(redis)` → `quality` RED|GREEN. Cap from
  `config.ramp.daily_cap` (RampConfig, default 50). Optional `?uf=` adds a per-UF block.
- **Shared key helper** — `brave/compliance/gate.py:72` `ramp_key(uf)` is now the single source of
  truth for the `wa:ramp:{YYYY-MM-DD}` / `wa:ramp:{UF}:{date}` format. The write path
  (`check_and_increment_ramp`, `gate.py:127`) and the read endpoint
  (`atrativos_gate.py:231,255`) both call it — verified by grep; no key-format drift is possible.
- **Read-only confirmed** — grep over the endpoint body (`atrativos_gate.py:182–263`) finds zero
  `incr/decr/.set/.delete/.expire`. Backend test `test_ramp_context_is_read_only_never_mutates_counter`
  asserts the counter is unchanged across 3 calls.
- **Frontend now data-backed** — `gate-api.ts:92` `fetchRampContext` targets
  `api/v1/atrativos/whatsapp/ramp-context` (matches the new backend route). The backend response is a
  superset returning the exact `RampQualityContext` aliases (`ramp_cap/ramp_used/ramp_remaining/quality_rating/paused`),
  so `RampContext.tsx` renders real GREEN/RED + cap data on the happy path. The "indisponível"
  fallback (`RampContext.tsx:44`) now appears **only** on a genuine fetch error (advisory graceful
  degradation).
- **Tests re-run (not trusted from SUMMARY):**
  - `pytest test_dashboard_endpoints.py -k ramp_context` → **6 passed** (401-before-read, happy-path
    shape, absent-key→used=0, RED, read-only-never-mutates, per-UF).
  - `pytest test_dashboard_endpoints.py -m "not integration"` → **30 passed** (offline dashboard, no regression).
  - `pytest -k "gate or compliance or ramp"` → **51 passed** (shared `ramp_key` refactor caused no regression).
  - `bunx vitest run` → **14 files, 82 passed** (was 79; +3 RampContext).
  - `bunx tsc --noEmit` → **clean (exit 0)**.

**No regressions.** The remaining 5 success criteria (verified in the initial pass) re-checked clean.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria — the contract)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | DLQ review queue shows Nascente payload + Rio + §7.6 per-criterion + signals + WhatsApp log; approve/reject/edit/reprocess + batch-by-state; edit → re-score | ✓ VERIFIED | `dashboard.py:69 get_dlq_detail` returns `score_breakdown`+`signals`+`whatsapp_log`+`nascente_payload`, 404 on unknown id. UI: ScoreBreakdownPanel/QueueList/ReviewPanel exist; dlq-actions.ts `invalidateQueries(['dlq'])` over existing PATCH validate/descarte/reprocess + batch. Backend + DLQ Vitest tests pass. |
| 2 | Brave monitor shows volume per layer, approval/rejection/DLQ rates, failure alerts, throughput, audit | ✓ VERIFIED | `dashboard.py:124 get_monitor` aggregates NascenteRecord/RioRecord/MarRecord counts, AuditLog action group_by (rates), window throughput, PoisonQuarantine failures. MonitorTiles.tsx polls via `refetchInterval`. MonitorTiles + ThroughputChart tests pass. |
| 3 | WhatsApp gate UI works `aguardando_consulta_whatsapp` (approve/reject) WITH ramp/quality context; conversations + funnels by UF/source | ✓ VERIFIED | Approve/reject FULLY functional over existing `/atrativos/gate/{id}/approve\|reject` (gate-actions tests pass). **Ramp/quality context now data-backed**: `get_ramp_context` (atrativos_gate.py:182, Bearer-guarded, Redis-only, shared `ramp_key`) feeds `RampContext.tsx` real GREEN/RED + cap data; 6 backend + 3 frontend RampContext tests pass. Conversations + funnels VERIFIED (truths 5–6). |
| 4 | Cost & LLM view shows spend per lane/model from `llm_generations` | ✓ VERIFIED | `dashboard.py:222 get_cost` `func.sum(LLMGeneration.usd_cost)` group_by lane\|model_slug, empty-period safe. CostByLaneChart/CostSummary `useQuery` through BFF; cost tests pass. |
| 5 | Dashboard access-controlled via Bearer auth; components tested offline (Vitest + MSW) | ✓ VERIFIED | `deps.py:70 require_bearer` constant-time `hmac.compare_digest`, fail-closed, either-or steward/bearer; BFF `route.ts:62,72` injects server-held `Authorization: Bearer` (secret never in browser). FE has zero direct DB access. 82/82 Vitest+MSW tests pass across 14 files, fully offline. |

### Additional DASH-05 truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 6 | Funnels show destinos & atrativos by UF/source across stages | ✓ VERIFIED | `dashboard.py:271 get_funnels` real group_by on NascenteRecord(source,uf,entity_type) + RioRecord(routing,uf) + MarRecord; FunnelChart.tsx `useQuery`. FunnelChart tests pass. |
| 7 | Conversations show masked-phone WhatsApp transcript from append-only log; no raw PII | ✓ VERIFIED | ConversationMessage model stores `phone_masked` only (no raw column); migration 0005 creates table with masked column; `_log_conversation_messages` appends at BOTH pipeline write-points (outreach `pipeline.py:1116`, resume `pipeline.py:1285`). `get_conversations`/`get_conversation_detail` return `phone_masked` only. TranscriptPanel tests pass. |

**Score:** 6/6 success criteria fully verified. SC #3 (DASH-03) ramp/quality context closed by plan 04-10 — now data-backed end-to-end.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/api/deps.py` | require_bearer + either-or guard | ✓ VERIFIED | `require_bearer` + `hmac.compare_digest` (70,101,107) |
| `brave/config/settings.py` | DashboardConfig bearer token + RampConfig.daily_cap | ✓ VERIFIED | RampConfig.daily_cap (settings.py:203, default 50); config.ramp wired (233) |
| `brave/api/routers/dashboard.py` | dlq detail + monitor + cost + funnels + conversations | ✓ VERIFIED | all 6 GETs Bearer-guarded, real queries |
| `brave/api/routers/atrativos_gate.py` | gate queue + approve/reject + **ramp-context read endpoint** | ✓ VERIFIED | `get_ramp_context` (182) Bearer-guarded, Redis-only, read-only, shared `ramp_key` |
| `brave/compliance/gate.py` | shared `ramp_key` helper | ✓ VERIFIED | `ramp_key` (72) single source of truth; write path re-pointed at it (127) |
| `brave/compliance/quality_rating.py` | `is_quality_red` | ✓ VERIFIED | reads `wa:quality_red`, fail-closed (returns True on Redis error) |
| `brave/api/main.py` | dashboard_router registered | ✓ VERIFIED | `app.include_router(dashboard_router)` (main.py:41) |
| `brave/core/models.py` | ConversationMessage (masked phone) | ✓ VERIFIED | `phone_masked` only, no raw e164 column (453) |
| `alembic/versions/0005_conversation_message.py` | migration | ✓ VERIFIED | creates table, masked column, FK + index |
| `brave/tasks/pipeline.py` | appends at both write-points | ✓ VERIFIED | `_log_conversation_messages` @ 1116 + 1285 |
| `dashboard/app/api/[...path]/route.ts` | BFF inject secret | ✓ VERIFIED | server-side Bearer injection, secret never to browser |
| `dashboard/lib/auth.ts` | browser Bearer 401 at edge | ✓ VERIFIED | exists, 401 path |
| `dashboard/lib/gate-api.ts` | fetchRampContext → ramp-context route | ✓ VERIFIED | targets `api/v1/atrativos/whatsapp/ramp-context` (92), matches backend |
| DLQ slice (ScoreBreakdownPanel/QueueList/ReviewPanel) | §7.6 master-detail | ✓ VERIFIED | exist + wired + tested |
| Monitor / Cost / Funnels / Conversations slices | charts + transcript | ✓ VERIFIED | exist + `useQuery` wired + tested |
| `dashboard/components/gate/GateQueue.tsx` | gate master list | ✓ VERIFIED | reuses DLQ scaffold, approve/reject wired |
| `dashboard/components/gate/RampContext.tsx` | ramp/quality context | ✓ VERIFIED | now data-backed; renders real GREEN/RED + cap; fallback only on fetch error |
| `dashboard/components/gate/__tests__/RampContext.test.tsx` | ramp panel tests | ✓ VERIFIED | 3 tests: happy-path real data, RED destructive, fallback-on-error |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| deps.py | DashboardConfig.bearer_token | hmac.compare_digest | ✓ WIRED | deps.py:70,107 |
| route.ts | FastAPI | fetch injecting Bearer | ✓ WIRED | route.ts:62,72 |
| main.py | dashboard_router | include_router | ✓ WIRED | main.py:41 |
| dlq-actions.ts → PATCH dlq + batch | invalidateQueries(['dlq']) | ✓ WIRED | |
| MonitorTiles → /monitor | refetchInterval | ✓ WIRED | |
| gate-actions → /atrativos/gate approve\|reject | invalidateQueries(['gate']) | ✓ WIRED | |
| CostByLaneChart → /cost | useQuery | ✓ WIRED | |
| pipeline.py → ConversationMessage | append both write-points | ✓ WIRED | |
| dashboard.py → ConversationMessage | masked SELECT | ✓ WIRED | |
| FunnelChart → /funnels | useQuery | ✓ WIRED | |
| TranscriptPanel → /conversations/{rio_id} | useQuery masked | ✓ WIRED | |
| RampContext → /atrativos/whatsapp/ramp-context | useQuery (fetchRampContext) | ✓ WIRED | **endpoint now exists** (atrativos_gate.py:182); path matches; 6 backend + 3 FE tests pass |
| atrativos_gate.py get_ramp_context → ramp_key | redis.get(ramp_key(None)) | ✓ WIRED | shared helper, same key as write path (gate.py:127) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| MonitorTiles | monitor | GET /monitor (real group_by) | Yes | ✓ FLOWING |
| CostByLaneChart | cost | GET /cost (func.sum llm_generations) | Yes | ✓ FLOWING |
| FunnelChart | funnels | GET /funnels (real group_by UF/source) | Yes | ✓ FLOWING |
| TranscriptPanel | conversation | GET /conversations/{rio_id} (masked SELECT) | Yes | ✓ FLOWING |
| ReviewPanel (DLQ) | dlq detail | GET /dlq/{rio_id} (breakdown+signals+log) | Yes | ✓ FLOWING |
| GateQueue | gate rows | GET /atrativos/gate (Phase 3 endpoint) | Yes | ✓ FLOWING |
| RampContext | ramp/quality ctx | GET /atrativos/whatsapp/ramp-context (Redis ramp counter + quality flag via shared `ramp_key`) | **Yes — endpoint live, reads real Redis counter + quality flag** | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Ramp-context endpoint (401/shape/RED/absent/read-only/per-UF) | `pytest test_dashboard_endpoints.py -k ramp_context` | 6 passed | ✓ PASS |
| Offline dashboard suite (no regression) | `pytest test_dashboard_endpoints.py -m "not integration"` | 30 passed | ✓ PASS |
| Gate/compliance/ramp (shared ramp_key refactor) | `pytest -k "gate or compliance or ramp"` | 51 passed | ✓ PASS |
| Dashboard offline suite (Vitest+MSW) | `bunx vitest run` | 82 passed, 14 files | ✓ PASS |
| Type safety | `bunx tsc --noEmit` | clean (exit 0) | ✓ PASS |

### Probe Execution

No conventional or phase-declared probes (`scripts/*/tests/probe-*.sh`) exist for this phase. Test suites (above) serve as the executable evidence. — SKIPPED (no probes).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| DASH-01 | 04-03, 04-04 | DLQ queue + §7.6 per-criterion + batch-by-state + edit→re-score | ✓ SATISFIED | get_dlq_detail + DLQ slice + dlq-actions |
| DASH-02 | 04-05 | Brave monitor rates/throughput/alerts/audit | ✓ SATISFIED | get_monitor + MonitorTiles |
| DASH-03 | 04-06, 04-10 | WhatsApp gate queue (approve/reject) WITH ramp context | ✓ SATISFIED | queue works; ramp context now data-backed (get_ramp_context + RampContext.tsx, plan 04-10) |
| DASH-04 | 04-07 | Cost & LLM per lane/model from llm_generations | ✓ SATISFIED | get_cost + CostByLaneChart |
| DASH-05 | 04-08, 04-09 | Conversations + funnels by UF/source | ✓ SATISFIED | get_funnels/get_conversations + masked transcript |
| DASH-06 | 04-01, 04-02 | Bearer-header auth | ✓ SATISFIED | require_bearer + BFF secret injection |

All 6 declared requirement IDs accounted for; none orphaned. REQUIREMENTS.md marks all 6 "Complete" — now consistent with the codebase (DASH-03 ramp context closed).

### Anti-Patterns Found

None. The previously-flagged permanent degraded fallback in `RampContext.tsx` is resolved: the
"indisponível" branch (`RampContext.tsx:44`) is now reached only on a genuine fetch error
(intentional advisory graceful degradation), not unconditionally. No `TBD`/`FIXME`/`XXX` debt
markers in any phase-modified file. No stub returns, no hardcoded-empty render data, no console-only
handlers.

### Architecture Invariants

- **Dashboard never touches DB directly:** ✓ VERIFIED — zero psycopg/sqlalchemy/postgres references in `dashboard/` (grep = 0); every datum flows through the BFF Route Handler.
- **Bearer fail-closed + secret server-side only:** ✓ VERIFIED — including the new ramp-context endpoint (`dependencies=[Depends(require_bearer)]`; 401-before-read test passes).
- **Only additive backend writes:** ✓ VERIFIED — plan 04-10 added a read-only GET + extracted a shared `ramp_key` helper (write path re-pointed at it, behavior unchanged — 51 gate/compliance/ramp tests still green). No INCR/DECR added; the endpoint never mutates the ramp counter (read-only test passes).
- **Frozen Phase 1 core untouched:** ✓ VERIFIED — no phase-04 changes to score/routing/Mar promotion. Plan 04-10 touched only `brave/compliance/gate.py` (additive helper + re-point), `brave/api/routers/atrativos_gate.py` (additive endpoint), and tests.
- **LGPD masked phone only / no raw phone leak:** ✓ VERIFIED — `phone_e164` appears in `dashboard/` only in comments, an adversarial test, and a defensive blocklist (`GateReviewPanel.tsx:106` strips it). The ramp-context response is aggregate counters + a GREEN/RED string only — no PII, no record-level data, no secrets.

### Human Verification Required

None blocking. (No `<verify><human-check>` blocks were deferred from plans; automated suites cover
the offline contract. Live-infra UAT items are tracked separately in the prior phase-03 UAT and are
out of scope here.)

### Gaps Summary

None. The single gap from the initial verification — DASH-03 / Success Criterion #3's ramp/quality
context being wired-but-hollow — has been closed by plan 04-10 and re-verified against the actual
codebase (endpoint exists, Bearer-guarded, Redis-only, read-only, shared key helper, frontend
data-backed) with all test suites re-run independently (6 backend ramp-context + 30 offline
dashboard + 51 gate/compliance/ramp pytest; 82 Vitest+MSW; tsc clean). All 6 success criteria and
all 6 DASH requirements are satisfied; architecture/LGPD invariants hold; no regressions.

---

_Verified: 2026-06-16T21:10:00Z_
_Verifier: Claude (gsd-verifier)_
