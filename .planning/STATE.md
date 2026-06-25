---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Completed 10-04-PLAN.md (phase 10 all 4 plans done)
last_updated: "2026-06-25T20:00:00.000Z"
last_activity: 2026-06-25 -- Phase 14 plan 14-02 complete (Level-3 approved)
progress:
  total_phases: 5
  completed_phases: 5
  total_plans: 18
  completed_plans: 18
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-11)

**Core value:** Only validated, reliability-scored canonical records ("Mar", ≥85%) reach the platform — the Nascente→Rio→Mar pipeline with §7.6 scoring and a DLQ gate is the single thing that must work.
**Current focus:** Phase 14 — coordless-attraction-geo-resolution-nominatim

## Current Position

Phase: 14 (coordless-attraction-geo-resolution-nominatim) — COMPLETE
Plan: 2 of 2 (all plans complete)
Status: Phase 14 complete — all plans executed and verified
Last activity: 2026-06-25 -- Phase 14 plan 14-02 complete (Level-3 real MG sweep approved)

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**

- Total plans completed: 59
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1 | 3 | - | - |
| 02 | 9 | - | - |
| 03 | 5 | - | - |
| 04 | 10 | - | - |
| 05 | 3 | - | - |
| 6 | 3 | - | - |
| 7 | 7 | - | - |
| 08 | 7 | - | - |
| 12 | 4 | - | - |
| 13 | 3 | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
| Phase 01-brave-core-score-gate-boundary-contract P01 | 10 | 1 tasks | 29 files |
| Phase 01-brave-core-score-gate-boundary-contract P02 | 90 | 2 tasks | 43 files |
| Phase 01 P03 | 18 | 2 tasks | 9 files |
| Phase 02-destinos-lane P04 | 8m | 1 tasks | 2 files |
| Phase 02-destinos-lane P05 | 20m | 2 tasks | 2 files |
| Phase 02-destinos-lane P06 | 13m | 1 tasks | 2 files |
| Phase 02-destinos-lane P07 | 10m | 1 tasks | 2 files |
| Phase 02-destinos-lane P08 | 13m | 2 tasks | 4 files |
| Phase 04 P01 | 6min | 2 tasks | 5 files |
| Phase 04 P03 | 12min | 2 tasks | 3 files |
| Phase 04 P02 | 8min | 2 tasks | 23 files |
| Phase 04 P04 | 25min | 2 tasks | 24 files |
| Phase 04 P05 | 20m | 2 tasks | 15 files |
| Phase 04 P06 | 15min | 2 tasks | 9 files |
| Phase 04 P07 | ~15min | 2 tasks | 12 files |
| Phase 04 P08 | 45 | 2 tasks | 5 files |
| Phase 04 P09 | ~12min | 1 tasks | 11 files |
| Phase 05 P01 | 9min | 2 tasks | 2 files |
| Phase 05 P02 | 18min | 3 tasks | 5 files |
| Phase 05 P03 | ~25 min | 2 tasks | 5 files |
| Phase 06 P01 | 2min | 1 tasks | 4 files |
| Phase 06 P02 | 119 | 1 tasks | 1 files |
| Phase 07 P01 | 4min | 2 tasks | 2 files |
| Phase 07 P03 | 8min | 2 tasks | 2 files |
| Phase 07 P05 | 130 | 1 tasks | 1 files |
| Phase 07 P06 | 15min | 1 tasks | 2 files |
| Phase 07 P07 | 4min | 2 tasks | 3 files |
| Phase 10 P01 | ~12min | 2 tasks | 4 files |
| Phase 10 P02 | 25min | 2 tasks | 3 files |
| Phase 10 P03 | 9min | 2 tasks | 4 files |
| Phase 10 P4 | 6min | 1 tasks | 2 files |

## Accumulated Context

### Roadmap Evolution

- Phase 8 added: Ops CMS — Destinos/Atrativos CRUD + Process Observability (MÉDIO: cores Norteia + StageBadge + CRUD destinos/atrativos + /processo workers/falhas/pendências/jornada-até-Mar). No new shell/fonts/i18n (GRANDE deferred).

- Phase 7 added: Real Places Hardening + Targeted Atrativos Discovery + Mtur Refresh — live load-test attempt (10 destinos × 10 atrativos) surfaced real Places gaps: `google-maps-places` missing (installed), `RealPlacesClient` omits `X-Goog-FieldMask` (live 400), `text_search` returns no município → `_resolve_parent_destino` mislinks atrativos to an arbitrary Mar parent, `DiscoveryAgent.produce(uf)` is a UF-wide sweep (no per-destino volume), Mtur seed is a 16-row sample. Fix the real Places path + targeted per-município discovery + refresh Mtur + load-test harness.
- Phase 6 added: Real-Externals Enablement (RealLLMClient + live 24/7 collection) — closes the real-data blocker found in Phase 4/5 dogfooding: `brave/clients/llm.py`/`RealLLMClient` is missing (4 phantom import sites in pipeline.py), so `run_real_externals=True` ImportErrors on every LLM lane; plus the `BRAVE_RUN_REAL_EXTERNALS` docstring footgun (real toggle is `RUN_REAL_EXTERNALS`, no prefix).
- Phase 9 added: Close gap: INT-BLOCKER-01 — Null Places/LLM/Apify clients for offline task branch

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: Pact ingestion contract (1b) folded into Phase 1 (Core) — frozen early because cheap to build / expensive to change, and both lanes + the external Laravel repo depend on its stability.
- [Roadmap]: Dashboard is its own final phase (4) rather than parallel tracks — coarse granularity; each panel depends on its backing FastAPI surface (DLQ/monitor from P1, gate/conversations from P3) existing.
- [Roadmap]: Destinos (P2) precedes Atrativos (P3) — an atrativo's DiscoveryAgent resolves a parent destino that must already be in Mar.
- [Roadmap]: Compliance (LGPD + BSP) mapped into Phase 3 as hard send-path gates that land before the first real WhatsApp message, not as a late checkbox.
- [Phase ?]: D-01: table-per-layer medallion models implemented
- [Phase ?]: D-08: HNSW index on rio_records.embedding; no CONCURRENTLY in migration (Alembic transaction constraint)
- [Phase ?]: D-18: brave/core, brave/lanes, brave/clients package boundaries created and importable
- [Phase ?]: [Phase 4 P01]: D-02 Bearer-at-edge auth: require_bearer mirrors require_steward (constant-time, fail-closed, never-logged); either-or steward/Bearer guard (R4) on DLQ+gate mutations
- [Phase ?]: [Phase 4 P03]: D-01 DLQ detail GET /api/v1/dlq/{rio_id} on new read-only dashboard.py router, Bearer-guarded; surfaces score_breakdown+normalized+nascente_payload+signals+whatsapp_log, 404 on unknown id
- [Phase ?]: [Phase 4 P02]: D-02/D-03 dashboard scaffold + BFF auth — Next 16 App Router (Bun/Tailwind v4/shadcn new-york/TanStack Query); catch-all Route Handler validates browser Bearer (401 before forward) then injects server-held service secret to FastAPI, never leaking it; offline Vitest+MSW harness (DASH-06)
- [Phase ?]: ReviewPanel/QueueList kept action-agnostic (injected actions) for plan-05 gate reuse; shared ['dlq'] query key → single invalidate refetches list+detail
- [Phase ?]: DLQ §7.6 ScoreBreakdownPanel uses custom threshold-capped bars (not shadcn Progress) for per-bar green/amber/red caps
- [Phase ?]: [Phase 4 P05]: DASH-02 GET /api/v1/monitor read-only Bearer-guarded aggregate (volume + AuditLog-derived rates = audit coverage + throughput + PoisonQuarantine/RED-quality alerts); live-polled via shared useMonitor (refetchInterval 10s, D-04), WebSocket deferred
- [Phase ?]: [Phase 4 P07]: DASH-04 GET /api/v1/cost?group_by=lane|model&since= read-only Bearer-guarded GROUP BY over llm_generations (func.sum usd_cost + token sums + count); Cost & LLM view = mono USD/tokens summary + per-lane/per-model Recharts bars via shared useCost + window selector; empty period renders 'Sem dados no periodo'
- [Phase ?]: [Phase 4 P10 gap-closure]: DASH-03 ramp/quality context closed — GET /api/v1/atrativos/whatsapp/ramp-context (atrativos_gate.py), read-only Bearer-guarded, reads shared ramp_key() Redis counter + wa:quality_red flag; returns daily_cap/used/remaining/quality + RampQualityContext aliases so RampContext.tsx renders real data (not the 'indisponível' fallback). Never mutates the ramp counter (read-only; ramp enforced in P3 send path)
- [Phase ?]: brave.sweep_uf is producer-only (D-02) — composes Mtur seed + Desmembramento, no scoring branch; promotion stays behind §7.6 + human DLQ gate
- [Phase ?]: Integration tests for internally-committing Celery tasks use a SAVEPOINT-isolated session (join_transaction_mode=create_savepoint) so the outer rollback discards everything — prevents shared-DB leakage
- [Phase ?]: [Phase 5 P02]: ORCH-02 Atrativos FSM auto-advance — DiscoveryAgent.produce seeds Rio + sub_state='discovered' (finding #1); discover_atrativo_task self-enqueues find_contacts->gather_signals keyed on sub_state (D-03); chain terminal at aguardando_consulta_whatsapp with NO auto-outreach (D-07); inline agent guards kept for replay-safety (finding #2/D-04)
- [Phase ?]: Ops trigger (CLI sweep + Bearer-guarded POST /api/v1/sweep) dispatches only producer/chain tasks with Celery-or-inline fallback; no §7.6 or WhatsApp-send bypass (ORCH-03/04, T-05-07/09)
- [Phase ?]: D-02: _resolve_parent_destino guard returns None immediately on empty municipio_ibge — prevents source_ref.contains('') DB mislink
- [Phase ?]: D-03: produce_for_destino injects parent_mar.id directly, bypassing _resolve_parent_destino — parent is known from caller context
- [Phase ?]: G1 gap closure: harness-only corroboration boost (+50) standing in for NotebookLM/2nd-source corroboration; global §7.6 weights/thresholds untouched
- [Phase ?]: G2 gap closure
- [Phase 10 P01]: ENG-01/02 — engine depth contract (NASCENTE|NASCENTE_RIO|NASCENTE_RIO_MAR, Redis `brave:engine:depth`) exported from brave/core/engine.py; set_depth raises on invalid (never silently spends); get_status carries depth; POST /start enforces required depth with 422 BEFORE start_run/409 and threads depth into engine_sweep_run.delay (orchestrator accepts it in 10-02); require_steward_or_bearer guard unchanged
- [Phase ?]: [Phase 10 P02]: ENG-03/04/05/07 — engine depth threaded orchestrator->producers; nascente forces Mtur-only (run_rio=False, no Desmembramento, no atrativos); nascente_rio gates the entire find_contacts fan-out (delay + inline .run) so the WhatsApp chain is never kicked; no automated Mar push added under any depth (stays on human DLQ gate + WhatsApp finalize); lanes never read Redis depth
- [Phase 10 P03]: ENG-01/02 client half — /processo depth selector (3 PT-BR opts via DEPTH_LABELS); Ligar motor disabled until a depth is chosen (no default spend); chosen depth sent in POST /start body; active depth read back from /status on engine-active-depth testid; native radiogroup (no new npm pkg); Vitest+MSW offline 140/140
- [Phase 10 P04]: ENG-06/07 — StageBadge nascente variant: prop-driven `nascente?: boolean` chip (PT-BR "Nascente", `--color-primary` CSS-var token, no hex), rendered stage-first; stage stays implicit by table membership (D-01), no backend/schema/endpoint change; Vitest +2 offline, full dashboard suite 142/142

### Pending Todos

[From .planning/todos/pending/ — ideas captured during sessions]

None yet.

### Blockers/Concerns

[Issues that affect future work]

- [Phase 1 P03]: pact-python 3.4.0 uses top-level Pact class (from pact import Pact), not pact.v3 submodule
- [Phase 1 P03]: NorteiaApiClient accepts str or yarl.URL via str(base_url) normalization
- [Phase 1 P03]: Function-scoped webhook test fixtures with fresh fakeredis per test isolates rate limiter state
- [Phase 1 research flag] Score-distribution calibration (50/85 boundaries + §7.6 weights) is MEDIUM-confidence — ship the histogram-simulation harness and treat boundaries as tunable; calibrate on the first state before national fan-out.
- [Phase 3 research flag] WhatsApp BSP pricing/policy/limits shift often — re-verify Twilio-vs-Meta-Cloud, template categorization, and rate caps at build time; the Celery-durable-executor + LangGraph multi-day FSM warrants a focused design pass.
- [Phase 1 code-review follow-ups — deferred, see 01-REVIEW.md] CR-03: harden `process_nascente` Celery retry/quarantine control flow so an exception escaping the quarantine write can't ack-and-lose a record. CR-04 (atomicity): make the USD cost guard reserve-before-call (atomic INCRBYFLOAT-then-check or Lua) so concurrent workers can't overshoot the daily ceiling; also crash-safe TTL + UTC-consistent daily key. Plus warnings: `get_redis` must not silently cache fakeredis in prod; `/health` should report non-200/degraded when DB/Redis are down. (CR-01, CR-02, CR-04-recording already fixed in 6b69226.)

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260623-j78 | Fix Celery worker registering zero tasks (autodiscover related_name="pipeline") | 2026-06-23 | 22f9c25 | [260623-j78-fix-celery-worker-registering-zero-tasks](./quick/260623-j78-fix-celery-worker-registering-zero-tasks/) |
| 260623-jw3 | Desmembramento None-result guard (offline NullLLMClient crash + Mtur-seed rollback) | 2026-06-23 | a49ebbd | [260623-jw3-desmembramento-none-result-guard-offline](./quick/260623-jw3-desmembramento-none-result-guard-offline/) |

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-06-25T20:00:00.000Z
Stopped at: Completed 14-02-PLAN.md (phase 14 all 2 plans done; Level-3 human-verified)
Resume file: None
