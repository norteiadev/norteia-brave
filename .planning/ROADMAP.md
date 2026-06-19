# Roadmap: norteia-brave (Pipeline Brave)

## Overview

Brave is a medallion ETL pipeline (Nascente → Rio → Mar/DLQ) gated by a deterministic §7.6 reliability score. The build is dependency-ordered: the entity-agnostic core (score engine, three layers, routing, the client boundary, observability, and the frozen Mar→norteia-api Pact contract) must exist before anything can be validated, because every record routes through the gate and every test mocks against the client seam. The Destinos lane comes next — it proves the full Nascente→Rio→DLQ→Mar→push path on real data with the simpler (no-PII) validation model and, critically, must populate Mar before Atrativos because an atrativo resolves a parent destino that must already be canonical. The Atrativos lane lands last: it is the hardest and riskiest (sub-state machine, LangGraph WhatsApp conversation, BSP/LGPD compliance, the most external dependencies), and ships after the gate, push, and DLQ are already proven. The Next.js territorial-CMS dashboard is built as the final phase, once each backing FastAPI surface (DLQ/monitor from core, gate/conversations from Atrativos, cost view from observability) exists to drive it.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Brave Core, Score Gate, Boundary & Contract** - Entity-agnostic Nascente/Rio/Mar/DLQ engine with the pure §7.6 score gate, client boundary, observability, 24/7 orchestration, and the frozen Mar→norteia-api Pact contract (completed 2026-06-11)
- [x] **Phase 2: Destinos Lane** - Mtur/NotebookLM/Desmembramento producers → Rio/score → DLQ → batch-by-state human validation → Mar, proving the full path end-to-end (completed 2026-06-12)
- [x] **Phase 3: Atrativos Lane (WhatsApp + Compliance)** - Discovery → ContactFinder → Signal → human WhatsApp gate → automated owner-validation outreach → re-score, with LGPD + BSP enforced before the first real message (completed 2026-06-15)
- [x] **Phase 4: Dashboard (Territorial CMS)** - Brave monitor, DLQ batch review, WhatsApp gate UI, conversations/funnels, and Cost/LLM views behind Bearer auth (completed 2026-06-16)
- [x] **Phase 5: Auto-Discovery Orchestration** - celery-redbeat 27-UF fan-out, sweep_uf Destinos task, Atrativos FSM auto-advance, ops CLI/endpoint trigger (completed 2026-06-17)
- [x] **Phase 6: Real-Externals Enablement (RealLLMClient + live 24/7 collection)** - RealLLMClient implementing LLMClientProtocol (extract/generate), cost-guard + llm_generations wiring, docstring footgun fix (D-06), offline tests + opt-in smoke (completed 2026-06-18)

## Phase Details

### Phase 1: Brave Core, Score Gate, Boundary & Contract

**Goal**: A record can flow Nascente → Rio → Mar/DLQ/descarte through a pure, calibrable §7.6 score gate, with every external system behind a faked client interface, 24/7 Celery orchestration, full observability, and a frozen idempotent Mar→norteia-api contract — all validated by a 100%-offline keyless suite.
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: CORE-01, CORE-02, CORE-03, CORE-04, CORE-05, CORE-06, CORE-07, CORE-08, CORE-09, CORE-10, CORE-11, CORE-12, SCORE-01, SCORE-02, SCORE-03, OBS-01, OBS-02, OBS-03, OBS-04, CNTR-01, CNTR-02, TEST-01, TEST-03
**Success Criteria** (what must be TRUE):

  1. A raw payload ingested into Nascente is stored immutably (source-tagged, versioned, content-hashed) and a Celery task processes it through Rio (dedup by territorial-key blocking then pgvector fuzzy, normalize, label) to a §7.6 score that routes it to Mar (≥85), DLQ (51–84.9), or descarte (≤50) by config thresholds.
  2. The §7.6 score engine runs as a pure, zero-I/O function with config-driven weights, is unit-tested on Mar/DLQ/descarte boundary cases, stamps each score with its `score_version`, and a record can be reprocessed/re-scored idempotently without double-publishing.
  3. A Mar record pushes to norteia-api idempotently keyed by `source_ref` (re-push is a no-op upsert), carries full per-criterion provenance/lineage, and the push shape is frozen and verified by a passing Pact consumer contract test.
  4. Every external system (Places/OTA/Apify/WhatsApp/Mtur/NotebookLM/LLM/NorteiaApi) sits behind a client interface with a fake; the entire suite runs offline via docker-compose (Postgres+Redis) with no real network and no keys in CI.
  5. The pipeline records every LLM call with USD cost in `llm_generations`, an enforcing USD cost guard halts/throttles on budget breach, per-layer Brave metrics + queue/worker health + audit logs are exposed via FastAPI, and a community error-report webhook reopens a published record back into Rio/DLQ.

**Plans**: 3 plans

Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Project scaffold: uv, docker-compose, SQLAlchemy models, Alembic migrations (HNSW), pydantic-settings config, 8 client Protocol boundaries

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — §7.6 score engine + simulation harness, Rio pipeline (dedup/normalize/label/route), Nascente/Mar services, Celery tasks + redbeat, observability (cost guard/llm_tracker/audit), FastAPI surface

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 01-03-PLAN.md — NorteiaApiClient real impl, Pact consumer contract test, end-to-end pipeline integration test, error-report webhook wiring

### Phase 2: Destinos Lane

**Goal**: Destinos flow through the proven core from three producers (Mtur origem=100, NotebookLM origem=80, DesmembramentoAgent origem=40) into the DLQ, where a steward validates them batch-by-state (BA/RJ/SP/SC/CE/PE first) to set validação humana=100 and promote them to Mar and push to `destinations`.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: DEST-01, DEST-02, DEST-03, DEST-04, DEST-05, TEST-02
**Success Criteria** (what must be TRUE):

  1. MturSeedIngest ingests categorized municipalities (Oferta Principal/Complementar/Apoio) into Nascente (`source=mtur`, origem=100, linked to `municipality_id`) and NotebookLMIngest ingests structured reports (`source=notebooklm`, origem=80) for destinos absent from Mtur.
  2. The DesmembramentoAgent (DeepSeek) lists real destinos (distritos/praias/vilas) inside each Oferta Principal município into Nascente (origem=40, flagged "LLM-generated, pending validation") behind a mandatory Pydantic+`instructor` second-layer validator that quarantines malformed output.
  3. Destinos flow through Rio + §7.6 score and land in DLQ by default (lacking human validation); the origem=40 firewall means no LLM-only destino reaches Mar unaided.
  4. A steward validates destinos in the DLQ batch-by-state (BA/RJ/SP/SC/CE/PE first), setting validação humana=100, which re-scores them into Mar and pushes them to `destinations`.
  5. The score engine and DesmembramentoAgent have unit tests covering Mar/DLQ/descarte boundary cases, all running offline.

**Plans**: 9 plans

Plans:
**Wave 0** *(pre-conditions — run before any lane code)*

- [x] 02-01-PLAN.md — Pact contract update: add ibge_code to canonical dict (RISK-01; breaking change; coordinate with norteia-api Laravel team)
- [x] 02-02-PLAN.md — Score calibration: run simulation harness, lower threshold_dlq to 40 (D-05; resolves descarte black-hole for DesmembramentoAgent)

**Wave 1** *(parallel — no file conflicts)*

- [x] 02-03-PLAN.md — Schemas + client impls + fakes: DesmembramentoResult schema, MturClient+NullMturClient, NotebookLMClient+NullNotebookLMClient, FakeMturClient, FakeNotebookLMClient, Mtur seed CSV
- [x] 02-04-PLAN.md — push_destination_task Celery task (mirrors push_mar; always calls push_destination)

**Wave 2** *(depends on Wave 0 + Wave 1)*

- [x] 02-05-PLAN.md — MturSeedIngest lane + producer score boundary unit tests (D-06 firewall, TEST-02)
- [x] 02-06-PLAN.md — DLQ validate + validate-batch endpoints (D-07, D-08; flag_modified guard; steward→Mar→push)

**Wave 3** *(depends on Wave 2)*

- [x] 02-07-PLAN.md — NotebookLMIngest lane with IBGE corroboration boost (D-02; load-bearing for Mar promotion)
- [x] 02-08-PLAN.md — DesmembramentoAgent with validate-or-quarantine + unit tests (DEST-03, TEST-02)

**Wave 4** *(depends on Wave 3 — phase acceptance gate)*

- [x] 02-09-PLAN.md — End-to-end integration tests: full Destinos lane offline suite (all five requirements verified)

### Phase 3: Atrativos Lane (WhatsApp + Compliance)

**Goal**: An atrativo advances through a persisted, resumable sub-state machine — discovered (parent destino resolved from Mar) → contacts_found → signals_gathered → score → [borderline] human WhatsApp gate → automated owner-validation outreach → re-score → Mar/DLQ — with LGPD and WhatsApp BSP enforced as hard send-path gates before any real message is sent.
**Mode:** mvp
**Depends on**: Phase 2 (destinos must be in Mar for parent resolution)
**Requirements**: ATR-01, ATR-02, ATR-03, ATR-04, ATR-05, ATR-06, COMP-01, COMP-02, COMP-03
**Success Criteria** (what must be TRUE):

  1. The DiscoveryAgent sweeps Google Places (UF/município) + gov, maps via DeepSeek → schema → Nascente, resolves the parent destino already in Mar, and persists only `place_id` as cache (canonical data stays first-party validated); the ContactFinderAgent then finds phone/website/WhatsApp/email contacts.
  2. The SignalAgent reads Places `business_status` (CLOSED_* → descarte), `weekday_text` hours, and `reviews[].publishTime ≤30d ⇒ funcionando`, with IG/X via Apify best-effort and non-blocking; the atrativo advances through a `sub_state` column persisted across worker restarts.
  3. A human works the WhatsApp gate to approve which borderline (<85%) atrativos to contact, with an enforced volume ramp; no automated outreach happens without that approval.
  4. The WhatsAppAgent runs automated outreach (BSP API + n8n thin transport + LangGraph logic): Sonnet asks PT-BR (identifies Norteia + opt-out), DeepSeek extracts existe?/funcionando?/horários/valor, and owner-validation boosts the score → re-score → Mar/DLQ.
  5. The lane enforces LGPD (legal basis + Norteia identification + opt-out + consent log + minimization) and BSP (approved templates, 24h window, human gate + ramp, opt-out, auto-pause on degraded quality rating) as hard, code-enforced, offline-tested gates that block sending before the first real message.

**Plans**: 5 plans

Plans:
**Wave 1** *(package gate + scaffold — run first)*

- [x] 03-01-PLAN.md — Package legitimacy gate (langgraph-checkpoint-postgres), ConsentLog model + Alembic migration 0004, WhatsAppConfig + RampConfig settings, atrativos schemas (AtrativoResult/ContactResult/SignalResult/ConversationExtractionResult), FakeApifyClient + FakeWhatsAppClient + NullWhatsAppClient

**Wave 2** *(depends on Wave 1)*

- [x] 03-02-PLAN.md — Discovery producers: DiscoveryAgent (parent-resolution + place_id cache), ContactFinderAgent, SignalAgent (CLOSED_* hard descarte + Apify best-effort), state_machine.advance_sub_state, RealPlacesClient + RealApifyClient, discover_atrativo_task + find_contacts_task + gather_signals_task Celery tasks

**Wave 3** *(depends on Wave 1, parallel with Wave 2)*

- [x] 03-03-PLAN.md — Compliance gate: send_path_gate (8 D-11 conditions, 9 unit tests), consent_log (write/is_opted_out/record_opt_out/lookup), quality_rating (Redis flag), atrativos_gate FastAPI router (list/approve/reject/inbound/quality-rating endpoints)

**Wave 4** *(depends on Wave 2 + Wave 3)*

- [x] 03-04-PLAN.md — WhatsApp conversation: LangGraph WhatsAppAgent (Sonnet ask + DeepSeek extract + AsyncPostgresSaver), TwilioWhatsAppClient, outreach_task + resume_conversation_task + push_attraction_task

**Wave 5** *(depends on Wave 4 — phase acceptance gate)*

- [x] 03-05-PLAN.md — End-to-end integration tests: full Atrativos lane offline suite (all 5 success criteria + all 9 requirements verified)

### Phase 4: Dashboard (Territorial CMS)

**Goal**: Operators run the entire pipeline from a Next.js territorial CMS that consumes the FastAPI REST surface (never the DB directly): monitoring layer health, working the DLQ batch-by-state with per-criterion explainability, gating WhatsApp outreach, and viewing conversations, funnels, and LLM cost — all behind Bearer-header auth.
**Mode:** mvp
**Depends on**: Phase 1 (monitor/DLQ/cost surfaces), Phase 3 (WhatsApp gate/conversations surfaces)
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04, DASH-05, DASH-06
**Success Criteria** (what must be TRUE):

  1. The DLQ review queue shows Nascente payload + Rio data + §7.6 per-criterion score + signals + WhatsApp log, supports approve/reject/edit/reprocess with a batch-by-state mode, and editing triggers a re-score.
  2. The Brave monitor (§15.7) shows volume per layer, approval/rejection/DLQ rates, failure alerts, throughput, and audit.
  3. The WhatsApp gate UI works the `aguardando_consulta_whatsapp` queue (approve/reject) with ramp/quality context, and a conversations + funnels view shows destinos & atrativos by UF/source.
  4. The Cost & LLM view shows spend per lane/model from `llm_generations`.
  5. The dashboard is access-controlled via Bearer-header auth and its components are tested offline with Vitest + MSW.

**Plans**: 9 plans

Plans:
**Wave 1** *(backend auth foundation — run first)*

- [x] 04-01-PLAN.md — Bearer FastAPI dependency + DashboardConfig + either-or steward/Bearer mutation guard (R4) + offline pytest (DASH-06)

**Wave 2** *(parallel — frontend scaffold + DLQ detail endpoint; no file conflict)*

- [x] 04-02-PLAN.md — Dashboard scaffold (Next 16/Bun/Tailwind v4/shadcn/TanStack Query/Vitest+MSW) + BFF auth Route Handler + login gate (DASH-06)
- [x] 04-03-PLAN.md — GET /api/v1/dlq/{rio_id} detail endpoint (§7.6 breakdown + payload + signals + whatsapp log) + register dashboard router (DASH-01)

**Wave 3** *(depends on scaffold + DLQ detail)*

- [x] 04-04-PLAN.md — DLQ review UI: QueueList/ReviewPanel/ScoreBreakdownPanel master-detail, batch-by-state, approve/reject/edit→re-score (DASH-01)

**Wave 4** *(parallel — monitor endpoint+UI, gate UI; no file conflict)*

- [x] 04-05-PLAN.md — GET /api/v1/monitor endpoint (rates/throughput/alerts) + monitor view (tiles + Recharts + polling) (DASH-02)
- [x] 04-06-PLAN.md — WhatsApp gate UI over existing endpoints (reuse ReviewPanel) + ramp/quality context + masked PII (DASH-03)

**Wave 5** *(depends on monitor — shares dashboard.py)*

- [x] 04-07-PLAN.md — GET /api/v1/cost group-by endpoint + Cost & LLM view (Recharts by lane/model) (DASH-04)

**Wave 6** *(depends on cost — shares dashboard.py)*

- [x] 04-08-PLAN.md — conversation_message log + migration 0005 (R2 Option B) + appends at both pipeline write-points + funnels/conversations endpoints (DASH-05, backend half)

**Wave 7** *(depends on 04-08 endpoints — frontend half)*

- [x] 04-09-PLAN.md — Funnels + conversations views (Recharts stage bars, masked-phone transcript master-detail) over the 04-08 endpoints (DASH-05, frontend half)

**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Brave Core, Score Gate, Boundary & Contract | 3/3 | Complete    | 2026-06-11 |
| 2. Destinos Lane | 9/9 | Complete   | 2026-06-12 |
| 3. Atrativos Lane (WhatsApp + Compliance) | 5/5 | Complete   | 2026-06-15 |
| 4. Dashboard (Territorial CMS) | 9/9 | Complete   | 2026-06-16 |
| 5. Auto-Discovery Orchestration | 3/3 | Complete   | 2026-06-17 |
| 6. Real-Externals Enablement | 3/3 | Complete    | 2026-06-18 |

### Phase 5: Auto-Discovery Orchestration

**Goal**: The celery-redbeat 27-UF fan-out actually drives records end-to-end, closing the gap between the "24/7 automatic" promise and what was wired. Implement the registered `brave.sweep_uf` Destinos sweep task (the beat entry is currently a dangling phantom — no such task exists) so the daily Destinos sweep runs a real recurring producer (Desmembramento per Oferta-Principal município + idempotent Mtur seed; NotebookLM stays a manual ingest), and auto-advance the Atrativos sub-state FSM so `discover_atrativo_task` chains `discovered → contacts_found → signals_gathered → score → [borderline] aguardando_consulta_whatsapp` via idempotent, replay-safe Celery task dispatch (today `find_contacts_task`/`gather_signals_task` exist but are never enqueued, so the FSM stalls at `discovered`). The human WhatsApp gate + outreach stay exactly as built (no automatic send). Add an ops trigger (CLI/endpoint) to kick a UF sweep on demand, keep the 100%-offline test mandate (fakes + opt-in real flag).
**Mode:** mvp
**Requirements**: ORCH-01, ORCH-02, ORCH-03, ORCH-04
**Depends on:** Phase 4 (dashboard surfaces produced records), Phase 2 (Destinos producers), Phase 3 (Atrativos agents + FSM tasks + gate)
**Plans:** 3/3 plans complete

Plans:
**Wave 1** *(Destinos sweep — run first; owns pipeline.py)*

- [x] 05-01-PLAN.md — Implement `brave.sweep_uf` task (Mtur seed + Desmembramento producer composition); offline idempotency/quarantine test (ORCH-01, ORCH-04)

**Wave 2** *(depends on 05-01 — shares pipeline.py)*

- [x] 05-02-PLAN.md — Atrativos FSM auto-advance: init `sub_state='discovered'` at discovery + enqueue chain discover→find_contacts→gather_signals, stop at the human gate; offline e2e chain test (ORCH-02, ORCH-04)

**Wave 3** *(depends on 05-01 + 05-02 — both tasks must exist)*

- [x] 05-03-PLAN.md — Ops trigger: `brave.cli sweep <UF> [--lane ...]` + optional Bearer-guarded POST /api/v1/sweep; offline CLI + endpoint tests (ORCH-03, ORCH-04)

### Phase 6: Real-Externals Enablement (RealLLMClient + live 24/7 collection)

**Goal:** Create the single missing client  /  so the existing 24/7 Destinos+Atrativos sweep actually runs on real LLM providers when ; fix the  docstring footgun (7 occurrences); add offline unit tests + opt-in smoke.
**Requirements**: OBS-01, OBS-02, CORE-11, TEST-01
**Depends on:** Phase 5
**Plans:** 3/3 plans complete

Plans:
**Wave 1** *(parallel — no file conflicts)*

- [x] 06-01-PLAN.md — Footgun fix (D-06): replace BRAVE_RUN_REAL_EXTERNALS → RUN_REAL_EXTERNALS in 7 doc/error strings across places.py, apify.py, whatsapp.py, test_atrativos_lane_e2e.py
- [x] 06-02-PLAN.md — RealLLMClient: brave/clients/llm.py implementing LLMClientProtocol (extract via instructor+OpenRouter+DeepSeek; generate via native AsyncAnthropic; deny-block D-04; slug fallback D-03; cost-guard + llm_generations tracking D-05)

**Wave 2** *(depends on 06-02 — llm.py must exist)*

- [x] 06-03-PLAN.md — Tests + pipeline cleanup: 4 offline unit tests (guard/deny/fallback/cost-guard wiring), opt-in smoke (skipif no key), remove # type: ignore[import] from 4 pipeline.py RealLLMClient sites

### Phase 7: Real Places Hardening + Targeted Atrativos Discovery + Mtur Refresh

**Goal:** Fix the real Google Places path (field-mask 400, wrong get_place prefix, missing municipio_ibge), add targeted per-município atrativos discovery, refresh the Mtur dataset tooling, and extract the DLQ validate-and-promote service — so an operator can run a load test registering 10 destinos × ≥10 atrativos from live data.
**Requirements**: PLACE-01, PLACE-02, PLACE-03, PLACE-04, PLACE-05, PLACE-06, PLACE-07, PLACE-08
**Depends on:** Phase 6
**Plans:** 7/7 plans complete

Plans:
**Wave 1** *(parallel — no file conflicts)*

- [x] 07-01-PLAN.md — RealPlacesClient: _TEXT_SEARCH_FIELD_MASK + _GET_PLACE_FIELD_MASK constants + addressComponents→municipio extraction + ibge_lookup wiring + 5 offline tests (D-01, D-02, D-08)
- [x] 07-02-PLAN.md — Extract validate_and_promote_rio into brave/core/dlq/service.py; dlq.py router delegates to it (D-06)

**Wave 2** *(07-01 required for 07-03; 07-04 independent)*

- [x] 07-03-PLAN.md — DiscoveryAgent: empty-ibge guard on _resolve_parent_destino + produce_for_destino(parent_mar, target_count=10) targeted method + 3 offline tests (D-02, D-03, D-08)
- [x] 07-04-PLAN.md — Mtur refresh tooling: scripts/mtur_xlsx_to_csv.py converter + data/mtur/README (D-04)

**Wave 3** *(depends on 07-01, 07-02, 07-03)*

- [x] 07-05-PLAN.md — Load-test harness: scripts/loadtest_destinos_atrativos.py (ingest → promote 10 → targeted discovery × 10 → acceptance summary) (D-05, D-07)

**Wave 4 (gap-closure)** *(depends on 07-05 — close live-run blockers G1 + G2; parallel plans)*

- [x] 07-06-PLAN.md — G1: Harness corroboration boost (reassign + flag_modified + reprocess_record before validate_and_promote_rio) so Mtur destinos score 85.5 → Mar; 3 offline tests (PLACE-05)
- [x] 07-07-PLAN.md — G2: Copy parent_mar_id from nascente payload to rio.normalized in process_nascente_record; fix harness Step-4 to group by normalized.get("parent_mar_id") in Python; offline test (PLACE-03)

### Phase 8: Ops CMS: Destinos/Atrativos CRUD + Process Observability (cores, badges, jornada ate Mar)

**Goal:** [To be planned]
**Requirements**: TBD
**Depends on:** Phase 7
**Plans:** 0 plans

Plans:
- [ ] TBD (run /gsd-plan-phase 8 to break down)
