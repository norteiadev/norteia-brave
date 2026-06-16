---
phase: 04-dashboard-territorial-cms
verified: 2026-06-16T20:47:07Z
status: gaps_found
score: 5/6 success criteria fully verified (DASH-03 partial)
overrides_applied: 0
gaps:
  - truth: "The WhatsApp gate UI works the aguardando_consulta_whatsapp queue (approve/reject) WITH ramp/quality context (DASH-03 / Success Criterion 3)"
    status: partial
    reason: >-
      The gate approve/reject queue itself is fully functional and data-backed over the
      existing atrativos_gate.py endpoints. However the ramp/quality CONTEXT sub-clause is
      wired-but-hollow: RampContext.tsx fetches GET /api/v1/atrativos/whatsapp/ramp-context
      (dashboard/lib/gate-api.ts:92) which does NOT exist on the backend. atrativos_gate.py
      exposes only /gate, /gate/{id}/approve, /gate/{id}/reject and webhooks — there is no
      readable ramp-remaining/quality-rating endpoint. The §7.6 quality flag exists
      server-side (brave/compliance/quality_rating.py, set via webhook) but is never exposed
      as a GET. In production the panel therefore ALWAYS renders the degraded fallback
      "Contexto de ramp/qualidade indisponível." (RampContext.tsx:45). The operator never
      sees the volume cap or quality state the requirement promises. Phase 4 is the final
      milestone phase, so this cannot be deferred to a later phase.
    artifacts:
      - path: "dashboard/components/gate/RampContext.tsx"
        issue: "Panel is wired but its data source never produces data — always shows 'indisponível' fallback"
      - path: "dashboard/lib/gate-api.ts"
        issue: "fetchRampContext targets api/v1/atrativos/whatsapp/ramp-context — endpoint not implemented on backend"
      - path: "brave/api/routers/atrativos_gate.py"
        issue: "No read endpoint exposes ramp_remaining/ramp_cap/quality_rating for the gate context panel"
    missing:
      - "Add a thin read-only GET endpoint (per D-01) that surfaces the Phase 3 send-path ramp state (ramp_remaining/ramp_used/ramp_cap from the Redis ramp counter) + the WhatsApp quality_rating flag, Bearer-guarded, returning the RampQualityContext shape gate-api.ts already expects"
      - "Point fetchRampContext at the new endpoint and add an MSW happy-path test so the panel renders real GREEN/AMBER/RED + cap values rather than only its degraded fallback"
deferred: []
---

# Phase 4: Dashboard (Territorial CMS) Verification Report

**Phase Goal:** Operators run the entire pipeline from a Next.js territorial CMS that consumes the FastAPI REST surface (never the DB directly): monitoring layer health, working the DLQ batch-by-state with per-criterion explainability, gating WhatsApp outreach, and viewing conversations, funnels, and LLM cost — all behind Bearer-header auth.
**Verified:** 2026-06-16T20:47:07Z
**Status:** gaps_found
**Re-verification:** No — initial verification

> **MVP-mode note:** ROADMAP marks this phase `Mode: mvp`, but the phase goal is a descriptive
> goal, not a User Story (`As a … I want to … so that …`). Per the MVP guard the User-Flow
> Coverage table is not applicable; verification proceeds against the 5 ROADMAP Success Criteria
> (the contract) and the 6 DASH requirements. This is a recorded observation, not a blocker.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria — the contract)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | DLQ review queue shows Nascente payload + Rio + §7.6 per-criterion + signals + WhatsApp log; approve/reject/edit/reprocess + batch-by-state; edit → re-score | ✓ VERIFIED | `dashboard.py:69 get_dlq_detail` returns `score_breakdown`+`signals`+`whatsapp_log`+`nascente_payload`, 404 on unknown id. UI: ScoreBreakdownPanel/QueueList/ReviewPanel exist; dlq-actions.ts `invalidateQueries(['dlq'])` over existing PATCH validate/descarte/reprocess + batch. 50 backend + DLQ Vitest tests pass. |
| 2 | Brave monitor shows volume per layer, approval/rejection/DLQ rates, failure alerts, throughput, audit | ✓ VERIFIED | `dashboard.py:124 get_monitor` aggregates NascenteRecord/RioRecord/MarRecord counts, AuditLog action group_by (rates), window throughput, PoisonQuarantine failures. MonitorTiles.tsx polls via `refetchInterval`. 8 MonitorTiles + 5 ThroughputChart tests pass. |
| 3 | WhatsApp gate UI works `aguardando_consulta_whatsapp` (approve/reject) WITH ramp/quality context; conversations + funnels by UF/source | ✗ PARTIAL | Approve/reject FULLY functional over existing `/atrativos/gate/{id}/approve\|reject` (gate-actions tests pass). Conversations + funnels VERIFIED (see truths 5–6 below). **Gap:** ramp/quality context panel fetches a non-existent endpoint and always degrades to "indisponível" — see Gaps Summary. |
| 4 | Cost & LLM view shows spend per lane/model from `llm_generations` | ✓ VERIFIED | `dashboard.py:222 get_cost` `func.sum(LLMGeneration.usd_cost)` group_by lane\|model_slug, empty-period safe. CostByLaneChart/CostSummary `useQuery` through BFF; 10 cost tests pass. |
| 5 | Dashboard access-controlled via Bearer auth; components tested offline (Vitest + MSW) | ✓ VERIFIED | `deps.py:70 require_bearer` constant-time `hmac.compare_digest`, fail-closed, either-or steward/bearer (`deps.py:101,107`); BFF `route.ts:62,72` injects server-held `Authorization: Bearer` (secret never in browser). FE has zero direct DB access. 79/79 Vitest+MSW tests pass across 13 files, fully offline. |

### Additional DASH-05 truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 6 | Funnels show destinos & atrativos by UF/source across stages | ✓ VERIFIED | `dashboard.py:271 get_funnels` real group_by on NascenteRecord(source,uf,entity_type) + RioRecord(routing,uf) + MarRecord; FunnelChart.tsx `useQuery`. 5 FunnelChart tests pass. |
| 7 | Conversations show masked-phone WhatsApp transcript from append-only log; no raw PII | ✓ VERIFIED | ConversationMessage model stores `phone_masked` only (no raw column); migration 0005 creates table with masked column; `_log_conversation_messages` appends at BOTH pipeline write-points (outreach `pipeline.py:1116`, resume `pipeline.py:1285`, inbound+follow-up). `get_conversations`/`get_conversation_detail` return `phone_masked` only. 11 TranscriptPanel tests pass. |

**Score:** 5/6 success criteria fully verified; SC #3 (DASH-03) partial — ramp/quality context not data-backed.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/api/deps.py` | require_bearer + either-or guard | ✓ VERIFIED | `require_bearer` + `hmac.compare_digest` (70,101,107) |
| `brave/config/settings.py` | DashboardConfig bearer token | ✓ VERIFIED | exists, substantive |
| `brave/api/routers/dashboard.py` | dlq detail + monitor + cost + funnels + conversations | ✓ VERIFIED | all 6 GETs Bearer-guarded, real queries |
| `brave/api/main.py` | dashboard_router registered | ✓ VERIFIED | `app.include_router(dashboard_router)` (main.py:41) |
| `brave/core/models.py` | ConversationMessage (masked phone) | ✓ VERIFIED | `phone_masked` only, no raw e164 column (453) |
| `alembic/versions/0005_conversation_message.py` | migration | ✓ VERIFIED | creates table, masked column, FK + index |
| `brave/tasks/pipeline.py` | appends at both write-points | ✓ VERIFIED | `_log_conversation_messages` @ 1116 + 1285 |
| `dashboard/app/api/[...path]/route.ts` | BFF inject secret | ✓ VERIFIED | server-side Bearer injection, secret never to browser |
| `dashboard/lib/auth.ts` | browser Bearer 401 at edge | ✓ VERIFIED | exists, 401 path |
| DLQ slice (ScoreBreakdownPanel/QueueList/ReviewPanel) | §7.6 master-detail | ✓ VERIFIED | exist + wired + tested |
| Monitor / Cost / Funnels / Conversations slices | charts + transcript | ✓ VERIFIED | exist + `useQuery` wired + tested |
| `dashboard/components/gate/GateQueue.tsx` | gate master list | ✓ VERIFIED | reuses DLQ scaffold, approve/reject wired |
| `dashboard/components/gate/RampContext.tsx` | ramp/quality context | ⚠️ HOLLOW | exists + wired but data source endpoint missing |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| dlq.py | require_bearer | either-or guard | ✓ WIRED | pattern present |
| deps.py | DashboardConfig.bearer_token | hmac.compare_digest | ✓ WIRED | manual confirm (deps.py:70,107) — verifier false-negative on `\\.` escape |
| route.ts | FastAPI | fetch injecting Bearer | ✓ WIRED | manual confirm (route.ts:62,72) — verifier false-negative on `fetch\\(` regex |
| main.py | dashboard_router | include_router | ✓ WIRED | manual confirm (main.py:41) — verifier false-negative on `\\(` escape |
| dlq-actions.ts → PATCH dlq + batch | invalidateQueries(['dlq']) | ✓ WIRED | |
| MonitorTiles → /monitor | refetchInterval | ✓ WIRED | |
| gate-actions → /atrativos/gate approve\|reject | invalidateQueries(['gate']) | ✓ WIRED | |
| CostByLaneChart → /cost | useQuery | ✓ WIRED | |
| pipeline.py → ConversationMessage | append both write-points | ✓ WIRED | |
| dashboard.py → ConversationMessage | masked SELECT | ✓ WIRED | |
| FunnelChart → /funnels | useQuery | ✓ WIRED | |
| TranscriptPanel → /conversations/{rio_id} | useQuery masked | ✓ WIRED | |
| RampContext → /atrativos/whatsapp/ramp-context | useQuery | ✗ NOT_WIRED | target endpoint does not exist on backend |

> Note: three `verified=false` results from `gsd-sdk query verify.key-links` were false negatives caused by double-escaped regex patterns in PLAN frontmatter (`fetch\\(`, `\\(`, `\\.`). All three were manually confirmed WIRED in source.

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| MonitorTiles | monitor | GET /monitor (real group_by) | Yes | ✓ FLOWING |
| CostByLaneChart | cost | GET /cost (func.sum llm_generations) | Yes | ✓ FLOWING |
| FunnelChart | funnels | GET /funnels (real group_by UF/source) | Yes | ✓ FLOWING |
| TranscriptPanel | conversation | GET /conversations/{rio_id} (masked SELECT) | Yes | ✓ FLOWING |
| ReviewPanel (DLQ) | dlq detail | GET /dlq/{rio_id} (breakdown+signals+log) | Yes | ✓ FLOWING |
| GateQueue | gate rows | GET /atrativos/gate (Phase 3 endpoint) | Yes | ✓ FLOWING |
| RampContext | ramp/quality ctx | GET /atrativos/whatsapp/ramp-context | **No — endpoint missing** | ✗ DISCONNECTED |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Backend Bearer + read endpoints | `pytest tests/integration/test_dashboard_endpoints.py` | 50 passed | ✓ PASS |
| Dashboard offline suite (Vitest+MSW) | `bun run test` | 79 passed, 13 files | ✓ PASS |

### Probe Execution

No conventional or phase-declared probes (`scripts/*/tests/probe-*.sh`) exist for this phase. Test suites (above) serve as the executable evidence. — SKIPPED (no probes).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| DASH-01 | 04-03, 04-04 | DLQ queue + §7.6 per-criterion + batch-by-state + edit→re-score | ✓ SATISFIED | get_dlq_detail + DLQ slice + dlq-actions |
| DASH-02 | 04-05 | Brave monitor rates/throughput/alerts/audit | ✓ SATISFIED | get_monitor + MonitorTiles |
| DASH-03 | 04-06 | WhatsApp gate queue (approve/reject) WITH ramp context | ✗ BLOCKED (partial) | queue works; ramp context not data-backed (Gap) |
| DASH-04 | 04-07 | Cost & LLM per lane/model from llm_generations | ✓ SATISFIED | get_cost + CostByLaneChart |
| DASH-05 | 04-08, 04-09 | Conversations + funnels by UF/source | ✓ SATISFIED | get_funnels/get_conversations + masked transcript |
| DASH-06 | 04-01, 04-02 | Bearer-header auth | ✓ SATISFIED | require_bearer + BFF secret injection |

All 6 declared requirement IDs accounted for; none orphaned. REQUIREMENTS.md marks all 6 "Complete" — DASH-03 is overstated relative to the codebase (ramp context not data-backed).

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| dashboard/components/gate/RampContext.tsx | 45 | Permanent degraded fallback ("indisponível") because data source endpoint absent | ⚠️ Warning | Ramp/quality context never visible in production |

No `TBD`/`FIXME`/`XXX` debt markers in any phase-modified file. No stub returns, no hardcoded-empty render data, no console-only handlers.

### Architecture Invariants

- **Dashboard never touches DB directly:** ✓ VERIFIED — zero psycopg/sqlalchemy/postgres references in `dashboard/`; every datum flows through the BFF Route Handler.
- **Bearer fail-closed + secret server-side only:** ✓ VERIFIED.
- **Frozen Phase 1 core untouched:** ✓ VERIFIED — no phase-04 commits to score/routing/Mar; only additive append-only ConversationMessage (migration 0005) at two pinned pipeline write-points.
- **LGPD masked phone only:** ✓ VERIFIED — no raw `phone_e164` in dashboard.py reads, ConversationMessage model, or migration; `mask_phone` applied at write time.

### Human Verification Required

None blocking. (No `<verify><human-check>` blocks were deferred from plans; automated suites cover the offline contract. Live-infra UAT items are tracked separately in the prior phase-03 UAT and are out of scope here.)

### Gaps Summary

One gap blocks full goal achievement: **DASH-03 / Success Criterion #3 is partial.** The WhatsApp
gate's approve/reject queue is fully implemented, wired, masked, and offline-tested over the
existing Phase 3 endpoints — that half is solid. But the requirement explicitly demands the gate
work the queue **"with ramp/quality context."** `RampContext.tsx` is built and correctly wired to
TanStack Query, yet its fetcher (`gate-api.ts:92`) targets
`GET /api/v1/atrativos/whatsapp/ramp-context`, an endpoint that does not exist anywhere in the
backend. `atrativos_gate.py` exposes only the queue, approve, reject, and webhooks; the §7.6
quality flag lives in `brave/compliance/quality_rating.py` (writable via webhook) but is never
exposed as a readable GET. Consequently the panel **always** renders its degraded fallback,
"Contexto de ramp/qualidade indisponível." — the operator never sees the volume cap or quality
state the requirement promises.

This is a Level-4 (data-flow) hollow artifact: it exists, is substantive, and is wired, but its
data source produces nothing in production. It does NOT crash the gate (graceful degradation is
intentional), and REQUIREMENTS.md marking DASH-03 "Complete" overstates the codebase. Because
Phase 4 is the final milestone phase, this cannot be deferred to a later phase. Closing it is a
small, well-scoped task: add a thin read-only D-01 endpoint surfacing the Redis ramp counter +
quality flag and re-point the existing fetcher.

**If this deviation is intentional** (e.g., ramp/quality context judged advisory-only and deferred
to a later milestone), add to this file's frontmatter:

```yaml
overrides:
  - must_have: "The gate shows ramp + quality-rating context so the operator sees the volume cap and quality state"
    reason: "Ramp/quality context is advisory-only; the ramp is enforced server-side in the Phase 3 send path and the gate queue is fully functional without it. Read endpoint deferred to a later milestone."
    accepted_by: "<name>"
    accepted_at: "<ISO timestamp>"
```

---

_Verified: 2026-06-16T20:47:07Z_
_Verifier: Claude (gsd-verifier)_
