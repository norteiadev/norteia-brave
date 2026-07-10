---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: verifying
stopped_at: Completed 17.1-07-PLAN.md (Origem modal + TA cURL inject + Motor depth toggle + two-group nav + view-switcher — all 6 views reachable; Painel Brave shell finished). Phase 17.1 all 7 plans complete.
last_updated: "2026-06-30T20:55:48.102Z"
last_activity: 2026-06-30 - Completed quick task 260630-pfr: pipeline robustness (ta_config wire, savepoint isolation, dlq commit-order, reset broker purge)
progress:
  total_phases: 8
  completed_phases: 8
  total_plans: 38
  completed_plans: 38
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-11)

**Core value:** Only validated, reliability-scored canonical records ("Mar", ≥85%) reach the platform — the Nascente→Rio→Mar pipeline with §7.6 scoring and a DLQ gate is the single thing that must work.
**Current focus:** Phase 17.1 — Painel Brave — remaining pages + real backend (slice 2)

## Current Position

Phase: 17.1 (Painel Brave — remaining pages + real backend (slice 2)) — EXECUTING
Plan: 7 of 7 COMPLETE (17.1-01/03 wave-1 + 17.1-02 backend + 17.1-04 Duplicados + 17.1-06 board 6-col + 17.1-05 Varreduras + 17.1-07 shell integration — all 7 plans done)
Status: Phase complete — ready for verification
Last activity: 2026-07-10 - Completed quick task 260710-opo: reset producer inflight counter on motor OFF (badge no longer stuck on "Sincronizando")

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**

- Total plans completed: 61
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
| 14 | 2 | - | - |

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
| Phase 17.1 P01 | ~25min | 2 tasks | 4 files |
| Phase 17.1 P03 | ~25min | 2 tasks | 3 files |
| Phase 17.1 P02 | ~30min | 3 tasks | 10 files |
| Phase 17.1 P04 | ~20min | 2 tasks | 4 files |
| Phase 17.1 P06 | ~45min | 3 tasks | 13 files |
| Phase 17.1 P05 | ~20min | 2 tasks | 4 files |
| Phase 17.1 P07 | ~30min | 3 tasks | 6 files |
| Phase quick-260630-oa3 P01 | 15 | 3 tasks | 3 files |

## Accumulated Context

### Roadmap Evolution

- Phase 15 added: TripAdvisor full oa30 pagination + bulk Nascente collection + live sweep dashboard panel — closes the multi-page pagination follow-up deferred by Phase 13. Pagination is path-based (`-oa{N}-` HTML SSR, 334 pages, totalResults cap 10000), reachable via httpx with the full operator cookie jar. Slice-first validation, then full ~10k Brazil. New live progress panel (Redis key + FastAPI status + Next.js).

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
- [Phase 17.1 P03]: UI-PAINEL-2 stage transitions — ONE generic audited `transition` endpoint per entity, gated by a SERVER-SIDE edge allow-list (the twin of client mapDrop), keyed by (expected_column, to_column)→handler tag. cms.py `_ALLOWED_EDGES` (destino): rio→mar/descarte/dlq, dlq→rio/mar/descarte. atrativos.py `_ATRATIVO_ALLOWED_EDGES`: rio→dlq/mar/descarte, dlq→rio reopen (NEW), whatsapp→whatsapp. mar→* is ABSENT from both → 409 "transição não suportada" (no depublish; existing cms descarte_destino Mar guard intact). Endpoints REUSE existing helpers (validate_and_promote_rio / reprocess_record / promote_override) — no new pipeline machinery; into-whatsapp delegates fully to the audited approve_whatsapp_gate (sub_state aguardando_consulta_whatsapp guard), never duplicating outreach. TransitionBody (extra=forbid) + _ROUTING_TO_COLUMN optimistic-concurrency 409 shared via import (atrativos imports from cms — paired contract). 20 offline edge-table unit tests (RUN_REAL_EXTERNALS unset). Client mapDrop (plan 17.1-06) must mirror these allow-lists edge-for-edge.

- [Phase 17.1 P05]: UI-PAINEL-2 Varreduras frontend — dashboard/lib/runs-api.ts typed client (RunItem/RunsResponse/RunReprocessResult mirroring brave/api/routers/runs.py field-for-field; fetchRuns({uf,source,depth}) + reprocessRun(id); runsKeys ['runs'] prefix; 7-day window helpers recentRuns/totalSynced/totalFailed/formatCount mirroring the cost-api total/format idiom — `lane` deliberately OMITTED from RunItem because it is reprocess-only server state, not in the response model). dashboard/mocks/handlers/runs.ts at the double-prefixed /api/api/v1/runs/... (runsListSuccess/Empty/Error + runsReprocessSuccess; payloads typed against the lib interfaces = the A5 contract mirror; NOT registered in the empty index.ts barrel — per-suite server.use() pattern, mirrors cost.ts/dedup.ts). PainelVarreduras.tsx renders the runs table (Início/UF/Fonte/Profundidade/Total/Sincr./Falhas/Status) with colored status pills (concluido/parcial/falha/running); Fonte+Profundidade Seg filters go server-side, UF Seg derived from the loaded set filters client-side over each run's ufs array; ↺ Falhas → useMutation(reprocessRun)+toast+invalidateQueries(['runs']), disabled when failed==0; 7-day SummaryCards; empty state; pure --painel-* tokens. 4 offline Vitest green; full dashboard suite 269/269 (was 265, +4). Shell wiring into app/painel/page.tsx is plan 17.1-07.

- [Phase 17.1 P06]: UI-PAINEL-2 Painel board 6-column model + client transition allow-list. dashboard board moves 5→6 columns (nascente/rio/whatsapp/mar/dlq/falha); routingToColumn in_progress→rio (server twin _ROUTING_TO_COLUMN); atrativo sub_state aguardando_consulta_whatsapp buckets into whatsapp; falha cards from GET /api/v1/failures (real PoisonQuarantine, draggable for reprocess); metrics still read the envelope total. lib/painel-actions.ts mapDrop EXTENDED into the full-pipeline security boundary: emits ONE generic audited transition() call (engine-api transition → PATCH /api/v1/{destinos|atrativos}/{id}/transition) for EXACTLY the (expected→to) edges in the server _ALLOWED_EDGES/_ATRATIVO_ALLOWED_EDGES, and null (revert+toast) for every other pair (mar→* never depublishes, into-nascente, same-column, falha→*); exhaustive unit test proves no unmapped board pair is callable. engine-api adds transition()+injectTASession() (422/503 surfaced); fetchFailures/FailureItem re-exported from lib/workers-api.ts (no duplicate type). PainelColumnKey keeps descarte as a NON-rendered key so the drawer Descartar path + routingToColumn('descarte') stay valid (COLUMN_DEFS = 6 rendered). RecordCard falha affordance re-keyed descarte→falha [Rule 1]. 265/265 dashboard tests green. injectTASession consumed by the Origem modal in 17.1-07.

- [Phase 17.1 P02]: UI-PAINEL-2 Varreduras backend — durable runs_history trail (engine runs lived only in Redis). RunHistory model + Alembic 0007 (down_revision 0006, non-CONCURRENTLY ix_runs_history_started_at). engine_start INSERTs a row ONLY after depth/source 422 + start_run() success (Pitfall 3 — no phantom rows on rejected starts), persists run_id to Redis (brave:engine:run_id) + threads it into engine_sweep_run.delay; INSERT best-effort (never aborts a valid start). engine_sweep_run finalize (_finalize_run_history) UPDATEs ended_at/ufs_dispatched/status (concluido|parcial via STOPPING state read) in a swallow-all finally — a finalize write failure can NEVER abort the sweep (T-17.1-02-02). GET /api/v1/runs: source/depth SQL-filtered, uf filtered in Python over the JSON ufs array (no JSONB operator → portable+offline); synced/failed/total computed ON-READ over [started_at, ended_at] (Mar published + Rio dlq/descarte + PoisonQuarantine; A4 time-window approximation — producers never return counts). PATCH /runs/{id}/reprocess re-runs the SCOPE (ufs×source×lane) via the sweep.py prod-vs-offline broker fallback, audited run_reprocessed (per-record replay DEFERRED). 16 offline tests green (RUN_REAL_EXTERNALS unset) + migration DB up/down skip-safe without BRAVE_DB_URL. Deviation: added get_db override to test_engine_source client fixture (engine_start now needs db).

- [Phase 17.1 P07]: UI-PAINEL-2 shell integration — finishes the Painel Brave CMS. PainelOrigem.tsx modal: source radios (mtur/tripadvisor/google_places); selecting TripAdvisor reveals a cURL textarea whose in-modal parseTACurl() extracts cookie jar (-H 'cookie'/-b) + user-agent + preRegisteredQueryId ids into the strict SessionInjectBody (acquired_at stamped at submit) and posts via injectTASession. ApiError.status 422 (invalid_session, stale paste) vs 503 (canary_unverified, infra) surfaced as DISTINCT toasts AND inline origem-error-422/503 states (never a silent accept); live TTL badge from fetchTASessionStatus().expires_in (amber ≤5 min). PainelTopbar: "Origem {source}" button opens the modal (preselect from live engine source); motor START opens a depth menu (DEPTH_LABELS) → startEngine({depth}) — a depthless start is impossible (backend 422 twin); TA pill label/color + one-shot expiry toast driven by the real expires_in (sessionWarning ≤5 min), not a hardcoded clock. page.tsx view-switcher (PainelBody switch) renders PainelDuplicados + PainelVarreduras alongside Painel/Mapeamento/Conversas/Custo — drops "Em breve"; record-edit Drawer still reachable from a board card. Two-group nav (Processamento/Operação) was already centralized in nav.ts since slice 1; added stable per-group + per-item data-testids. Non-TA sources have no engine endpoint → Salvar just confirms + closes. Full dashboard suite 276/276 green (was 269). Deviation: updated PainelTopbar.test.tsx for the depth-menu start flow (test-only, matches the intended behavior change).

- [Phase 17.1 P04]: UI-PAINEL-2 Duplicados frontend — dashboard/lib/dedup-api.ts typed client (DedupPairItem/DedupPairsResponse mirroring brave/api/routers/dedup.py field-for-field; fetchDedupPairs(uf) + resolveDedupPair(id, {action, mar_id}); dedupKeys ['dedup'] prefix) + dashboard/mocks/handlers/dedup.ts at the double-prefixed /api/api/v1/dedup/... path (payloads typed against the lib interfaces = the A5 contract mirror; success/empty/error/resolve factories). Handler is NOT registered in mocks/handlers/index.ts — follows the established empty-barrel + per-suite server.use() pattern (cost.ts is also unregistered; keeps the harness booting with zero global mocks). PainelDuplicados.tsx renders candidate≈Mar pair cards with coincide/diverge chips + labeled similarity (similarity_source surfaced because embeddings are an A1 zero-stub), resolves via useMutation(resolveDedupPair)+toast+invalidateQueries(['dedup']); validation banner + ✓ empty state; pure --painel-* tokens. 3 offline Vitest tests green (renders pairs+chips+similarity, Descartar fires real resolve PATCH, empty state). Shell wiring into app/painel/page.tsx is plan 17.1-07.

- [Phase 17.1 P01]: UI-PAINEL-2 Duplicados backend — GET /api/v1/dedup/pairs is compute-on-read (territorial-key blocked candidate↔Mar pairs; matched/diverged + Jaccard token similarity computed in Python, similarity_source="embedding_stub", NO pgvector operator in the read path — real embeddings deferred A1). PATCH /api/v1/dedup/pairs/{candidate_rio_id}/resolve does merge|keep|discard, audited. merge (LOCKED A2, overrides stale RESEARCH Pitfall 4) unions the candidate source_ref into the EXISTING Mar's provenance["merged_source_refs"] + routes the candidate Rio→descarte: no new MarRecord, mar.source_ref untouched, no 409, no promote_to_mar. Candidate source_ref derived as canonical_key or str(id) (RioRecord has no source_ref column). 12 offline unit tests green (RUN_REAL_EXTERNALS unset).
- [Phase ?]: TA-destinos step removed from sweep_tripadvisor per-UF path (no QID captured); destino_rio_map widened to all destination RioRecords (Mtur/IBGE authoritative)

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

| # | Description | Date | Commit | Status | Directory |
|---|-------------|------|--------|--------|-----------|
| 260623-j78 | Fix Celery worker registering zero tasks (autodiscover related_name="pipeline") | 2026-06-23 | 22f9c25 | | [260623-j78-fix-celery-worker-registering-zero-tasks](./quick/260623-j78-fix-celery-worker-registering-zero-tasks/) |
| 260623-jw3 | Desmembramento None-result guard (offline NullLLMClient crash + Mtur-seed rollback) | 2026-06-23 | a49ebbd | | [260623-jw3-desmembramento-none-result-guard-offline](./quick/260623-jw3-desmembramento-none-result-guard-offline/) |
| 260628-jvk | Engine toggle persistence — operator-intent latch (brave:engine:enabled) so motor stays on across refresh + can be turned off | 2026-06-28 | 0362a5c | | [260628-jvk-fix-engine-toggle-persistence-add-operat](./quick/260628-jvk-fix-engine-toggle-persistence-add-operat/) |
| 260628-m1n | TripAdvisor bulk sync auto-resume on token re-inject (inject hook + 60s beat reconciler + persisted resume params; verified passed 7/7) | 2026-06-28 | (pending) | | [260628-m1n-tripadvisor-bulk-sync-auto-resume-on-tok](./quick/260628-m1n-tripadvisor-bulk-sync-auto-resume-on-tok/) |
| 260629-e69 | TA motor token-validity gate — token expiry turns motor OFF + start blocked for tripadvisor without valid-TTL session; auto-resume removed (verified 9/9) | 2026-06-29 | (pending) | | [260629-e69-tripadvisor-motor-token-validity-gate-au](./quick/260629-e69-tripadvisor-motor-token-validity-gate-au/) |
| 260629-p2v | TripAdvisor session auto-refresh — cookie write-back across 3 client transports (merge Set-Cookie + slide TTL + re-derive session_id) + keep-alive beat (10min HTML GET re-mints datadome) so operator pastes cURL only once; fallback (403→needs_bootstrap+engine OFF) unchanged (verified 5/5) | 2026-06-29 | 3bd1def | | [260629-p2v-tripadvisor-session-auto-refresh-cookie-](./quick/260629-p2v-tripadvisor-session-auto-refresh-cookie-/) |
| 260629-qny | Painel motor start dropped source (always swept default/Places) — new POST /engine/source sets active source w/o starting; PainelOrigem Salvar activates source (TA after inject; mtur/places→default); PainelTopbar start passes status source into startEngine. EngineControl + taBlocked intact (verified 7/7) | 2026-06-29 | 96c661e | | [260629-qny-fix-painel-motor-start-dropping-source-s](./quick/260629-qny-fix-painel-motor-start-dropping-source-s/) |
| 260629-rmz | Fix TripAdvisor lane geo-targeting + atrativo→destino linkage — 4 spike-confirmed bugs: corrected uf_geoids.json (27 state geoIds + RUN_REAL_EXTERNALS discovery script), destinos QID resolution chain (override→session→const, ValueError not silent-empty; _DESTINATIONS_QID stays None), null-safe _parse_attractions_page, + fetch_attraction_detail across client stack with detail-parents IBGE fallback (ta_config-gated + throttled). 162 TA unit tests pass offline. SPIKE-2 (live): parents have NO names → linkage superseded by 260630-ftx | 2026-06-29 | f9a10d7 | Needs Review | [260629-rmz-fix-tripadvisor-lane-geo-targeting-and-a](./quick/260629-rmz-fix-tripadvisor-lane-geo-targeting-and-a/) |
| 260630-ftx | TripAdvisor atrativo geo-linkage via single GraphQL query — SPIKE-2 found the listing card has no município, parents return bare geoIds (rmz's parents[0].localizedName was broken vs live data). New fetch_attraction_geo (qid d3d4987463b78a39, vars {locationId,eventType:PAGEVIEW,isGeoPage:true}) → gtmData.locationData.{cityName,stateName,cityGeoId} in 1 call; new uf_names.state_name_to_uf (27 UFs incl. "Federal District"→DF, live-confirmed); _ingest_one rewired (geo→UF→resolve_municipio→IBGE→Mtur destino), TestDetailParentsLinkage removed. Link by NAME (destino←atrativo←município←UF); destinos stay Mtur/IBGE-seeded. 214 TA unit tests pass offline. LIVE-VALIDATED: Paraná 60 atrativos → 60/60 geo+UF, 54/60 IBGE (→59/60 after jbt) | 2026-06-30 | b3b3758 | Verified | [260630-ftx-implement-tripadvisor-atrativo-geo-linka](./quick/260630-ftx-implement-tripadvisor-atrativo-geo-linka/) |
| 260630-jbt | resolve_municipio accent-fold — live dense test (Paraná) surfaced that rapidfuzz default_process is NOT accent-agnostic (the comment lied): TA returns ASCII "Maringa", IBGE has "Maringá" → fuzz 85.7 < 88 cutoff → miss. Added _fold_accents (unicodedata NFKD+drop Mn, stdlib no-dep); resolve_municipio pre-folds query+choices, returns original accented record. Live linkage 54/60→59/60 (98%; only Caiobá=district-not-município remains). 218 TA tests pass offline | 2026-06-30 | 5a5ba99 | Verified | [260630-jbt-accent-fold-resolve-municipio-so-ascii-t](./quick/260630-jbt-accent-fold-resolve-municipio-so-ascii-t/) |
| 260630-ks0 | Frontend: motor-running sync indicator + header logs sidebar (per active source). NEW backend log source (none existed — structlog wasn't even configured): brave/observability/log_buffer.py (Redis ring buffer per source, LPUSH+LTRIM, _BLOCKED_FIELDS secret-strip) + structlog_setup processor (FastAPI lifespan + Celery worker_process_init, gated off under fakeredis; source cached, idempotent) + GET /api/v1/logs (Bearer). Frontend: PainelTopbar indicator ("Sincronizando {source} · UF x/y", reuses existing engine/status poll, no 2nd fetch) + PainelLogs slide-over (clone PainelDrawer, 2s poll, cursor incremental) + logs-icon-btn + logs-api + MSW. 11 backend + 8 dashboard tests pass. LIVE-VALIDATED end-to-end: real UF=PR TA sweep → GET /api/v1/logs returns engine_started/llm_extract_ok/engine_uf_dispatched; /browse confirmed indicator + sidebar rendering real lines. Ops note: API-dispatched engine_sweep_run → default `celery` queue (worker needs -Q celery,brave.sweep) | 2026-06-30 | 30e7eac | Verified | [260630-ks0-frontend-motor-running-sync-indicator-he](./quick/260630-ks0-frontend-motor-running-sync-indicator-he/) |
| 260630-mb4 | Fix Celery queue routing BUG (surfaced by ks0): beat entries pinned options.queue="brave.sweep" while every .delay (API engine_sweep_run + fan-out) used default `celery`; the documented worker (no -Q) consumes only `celery` → beat tasks (daily sweeps, ta-keepalive) silently dropped (the real cause of the SPIKE-1 keepalive false-negative). No task_routes/queue= anywhere — the brave.sweep lane was never completed (cargo-cult). Fix (Solution A, single queue): removed all 55 beat options.queue pins, set task_default_queue="celery" explicit, added worker+beat docker-compose stubs (container-net hostnames, no -Q) + regression test (no beat entry pins non-default queue) + CLAUDE.md single-queue model + deferred dedicated-lanes trigger. 666 offline tests pass. Dedicated lanes (task_routes + 2 pools) deferred | 2026-06-30 | 57d8720 | Verified | [260630-mb4-fix-celery-queue-routing-beat-tasks-pinn](./quick/260630-mb4-fix-celery-queue-routing-beat-tasks-pinn/) |
| 260630-oa3 | Fix TA sweep crash (live-found via painel test): source=tripadvisor ran TripAdvisorDestinosIngest first → fetch_destinations raised ValueError 'No destinations queryId' (rmz _DESTINATIONS_QID=None) → task died 3×, atrativos never ran, 0 records/27 UFs. Fix: TA = atrativos-only — removed the TA-destinos step from sweep_tripadvisor; destino_rio_map now built from ALL destination RioRecords in the UF (Mtur/IBGE origem=100), keyed by IBGE. 892 offline tests pass. LIVE-VALIDATED: real PR TA sweep succeeds, atrativos producer runs, 0 destinos-QID crashes. Surfaced 2 separate follow-ups: (1) sweep_tripadvisor doesn't pass ta_config → ftx fetch_attraction_geo dormant in prod; (2) Mtur seed push_destination 'RioRecord not found' tx race | 2026-06-30 | 70feca2 | Verified | [260630-oa3-fix-ta-sweep-source-tripadvisor-crashes-](./quick/260630-oa3-fix-ta-sweep-source-tripadvisor-crashes-/) |
| 260630-pfr | Pipeline robustness — 4 fixes surfaced by the oa3 live test. (#1) sweep_tripadvisor now passes ta_config to TripAdvisorAtrativosIngest → activates the ftx fetch_attraction_geo geo-linkage in prod (was dormant → ibge_unmatched). (#2A) per-record SAVEPOINT (begin_nested) isolation in MturSeedIngest + DesmembramentoAgent → one bad município no longer rolls back the whole UF (was: single terminal commit, any error discarded all 168 + reported success). (#2B) dlq.py validate + validate-batch commit BEFORE push_destination.delay (mirror cms.py WR-01) → kills 'RioRecord not found' read-before-commit race. (#4) reset-brave-db purges the Celery broker queue (celery + _kombu*) so stale tasks don't re-fire on worker restart. 896 offline + integration (BRAVE_DB_URL) pass | 2026-06-30 | c6b938b | Verified | [260630-pfr-pipeline-robustness-fixes-wire-ta-config](./quick/260630-pfr-pipeline-robustness-fixes-wire-ta-config/) |
| 260701-has | Fix TA per-UF atrativos lane — 3 live-POC-backed fixes. (1) ALL 27 uf_geoids.json values were WRONG (rmz seeds, 0/27 matched reality; PR 303423 was actually Belém/PB city) → replaced with 27 validated STATE geoIds (each confirmed via GraphQL canonicalize qid a26bffd43d0e25b6 → State_of_<UF> page; PR=303435, SP=303598, PA=303402, RJ=303488). (2) POC DISPROVED rmz's "AttractionsFusion doesn't geo-scope" verdict — it DOES scope by geoId; the "fixed wrong set" was wrong geoIds + a transient. Added bounded transient-retry to fetch_attractions (client.py): AttractionsFusion intermittently returns HTTP 200 status.success==false "Failed to retrieve attractions data" totalResults=0 for a valid geoId; same payload retried succeeds (live: 1st=0, 2nd=3658). Retry keyed off status.success is False; status-absent/success==true = real-empty (1 call, no retries). 2 new BRAVE_TA_ config knobs. (3) Docstring note in ta_discover_state_geoids.py (TypeAhead is DataDome rate-limited; geoId in result `url` not locationId; GraphQL is durable path). GraphQL is page-1-only — HTML -oa{offset}- pagination untouched. TDD RED→GREEN; 221 TA + 672 full offline tests pass | 2026-07-01 | 4f1075a | Verified | [260701-has-fix-ta-per-uf-atrativos-correct-27-state](./quick/260701-has-fix-ta-per-uf-atrativos-correct-27-state/) |
| 260701-kiy | Surface município on Nascente cards + kanban lazy-load (display-only, no pipeline change). (1) API: `GET /api/v1/nascente` LGPD allow-list omitted município though every payload has it (`payload.canonical.municipio` nome + `payload.municipio_id` ibge); extracted `_project_nascente_item(rec)` helper adding both as approved PUBLIC-GEO fields (null-safe). (2) Frontend: nascente-api NascenteListItem gains `municipio`; toPainelCards nascente map now sets `municipality: n.municipio` (was hard-coded null) — RecordCard already rendered card.municipality, so pure plumbing. (3) Kanban lazy-load: PainelBoard per-column render windowing — extracted PainelColumn owning `visibleCount` (100 initial, +50 per IntersectionObserver sentinel intersect, reset on data/filter change). Client-side windowing (single-fetch→client-distribute makes server pagination impractical). Backend full unit suite + 7 new projection cases; dashboard 301 tests (43 files) incl. windowing (100-init/+50/capped) — all green | 2026-07-01 | e7831f0 | Verified | [260701-kiy-surface-municipio-on-nascente-cards-api-](./quick/260701-kiy-surface-municipio-on-nascente-cards-api-/) |
| 260710-opo | Fix motor OFF leaving the sync badge stuck on "Sincronizando" — set_mode(DESLIGADO) ran mark_idle + set_enabled(False) but never reset the producer inflight counter (_INFLIGHT_KEY), and get_status derives sync_phase="syncing" while get_inflight > 0, so a draining or leaked count pinned the badge on (permanently if a producer never hit its finally). Now DESLIGADO also zeroes _INFLIGHT_KEY (decr_inflight clamps at 0 → no underflow from late-draining producers). Reset-counter-only scope: no Celery revoke, no /engine/stop wiring, no log-lifecycle change (logs left as-is per user). New regression test + engine mode/latch/sweep suites pass | 2026-07-10 | 89c0cce | Verified | [260710-opo-fix-motor-off-leaving-engine-status-stuc](./quick/260710-opo-fix-motor-off-leaving-engine-status-stuc/) |

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-06-30T20:55:44.362Z
Stopped at: Completed 17.1-07-PLAN.md (Origem modal + TA cURL inject + Motor depth toggle + two-group nav + view-switcher — all 6 views reachable; Painel Brave shell finished). Phase 17.1 all 7 plans complete.
Resume file: None
