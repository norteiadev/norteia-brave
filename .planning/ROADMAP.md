# Roadmap: norteia-brave (Pipeline Brave)

## Overview

Brave is a medallion ETL pipeline (Nascente → Rio → Mar/DLQ) gated by a deterministic §7.6 reliability score. The build is dependency-ordered: the entity-agnostic core (score engine, three layers, routing, the client boundary, observability, and the frozen Mar→norteia-api Pact contract) must exist before anything can be validated, because every record routes through the gate and every test mocks against the client seam. The Destinos lane comes next — it proves the full Nascente→Rio→DLQ→Mar→push path on real data with the simpler (no-PII) validation model and, critically, must populate Mar before Atrativos because an atrativo resolves a parent destino that must already be canonical. The Atrativos lane lands last: it is the hardest and riskiest (sub-state machine, LangGraph WhatsApp conversation, BSP/LGPD compliance, the most external dependencies), and ships after the gate, push, and DLQ are already proven. The Next.js territorial-CMS dashboard is built as the final phase, once each backing FastAPI surface (DLQ/monitor from core, gate/conversations from Atrativos, cost view from observability) exists to drive it.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Brave Core, Score Gate, Boundary & Contract** - Entity-agnostic Nascente/Rio/Mar/DLQ engine with the pure §7.6 score gate, client boundary, observability, 24/7 orchestration, and the frozen Mar→norteia-api Pact contract
- [ ] **Phase 2: Destinos Lane** - Mtur/NotebookLM/Desmembramento producers → Rio/score → DLQ → batch-by-state human validation → Mar, proving the full path end-to-end
- [ ] **Phase 3: Atrativos Lane (WhatsApp + Compliance)** - Discovery → ContactFinder → Signal → human WhatsApp gate → automated owner-validation outreach → re-score, with LGPD + BSP enforced before the first real message
- [ ] **Phase 4: Dashboard (Territorial CMS)** - Brave monitor, DLQ batch review, WhatsApp gate UI, conversations/funnels, and Cost/LLM views behind Bearer auth

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

- [ ] 01-01-PLAN.md — Project scaffold: uv, docker-compose, SQLAlchemy models, Alembic migrations (HNSW), pydantic-settings config, 8 client Protocol boundaries

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 01-02-PLAN.md — §7.6 score engine + simulation harness, Rio pipeline (dedup/normalize/label/route), Nascente/Mar services, Celery tasks + redbeat, observability (cost guard/llm_tracker/audit), FastAPI surface

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 01-03-PLAN.md — NorteiaApiClient real impl, Pact consumer contract test, end-to-end pipeline integration test, error-report webhook wiring

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

**Plans**: TBD

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

**Plans**: TBD

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

**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Brave Core, Score Gate, Boundary & Contract | 0/3 | Not started | - |
| 2. Destinos Lane | 0/TBD | Not started | - |
| 3. Atrativos Lane (WhatsApp + Compliance) | 0/TBD | Not started | - |
| 4. Dashboard (Territorial CMS) | 0/TBD | Not started | - |
