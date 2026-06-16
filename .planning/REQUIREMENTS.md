# Requirements: norteia-brave (Pipeline Brave)

**Defined:** 2026-06-11
**Core Value:** Only validated, reliability-scored canonical records ("Mar", ≥85%) reach the platform — the Nascente→Rio→Mar pipeline with §7.6 scoring and a DLQ gate is the single thing that must work.

## v1 Requirements

Requirements for the foundational milestone: entity-agnostic Brave core + Destinos lane + Atrativos lane + operations dashboard. Each maps to roadmap phases.

### Brave Core

- [x] **CORE-01**: Pipeline stores raw, source-tagged, versioned payloads (JSONB) from any lane/entity in Nascente, immutable (never mutated)
- [x] **CORE-02**: Rio explodes a Nascente payload, dedups (exact hash blocking → fuzzy/embedding via pgvector), normalizes names/coords/addresses, and labels with the Norteia taxonomy
- [x] **CORE-03**: A scored record routes three ways by config thresholds: Mar (≥85), DLQ (51–84.9), descarte (≤50)
- [x] **CORE-04**: Mar holds canonical records, versioned by supersession, supporting invalidation and update
- [x] **CORE-05**: Pipeline pushes a Mar record to norteia-api idempotently, keyed by canonical key / `source_ref` (re-push never duplicates)
- [x] **CORE-06**: Every record carries provenance/lineage (sources + per-criterion §7.6 breakdown + decisions) through to the Mar push
- [x] **CORE-07**: DLQ is a durable, actionable queue (not a log): records carry reason codes and are workable from the dashboard
- [x] **CORE-08**: Pipeline can reprocess / re-score a record on demand idempotently (triggered by config change, new corroboration, human validation, or error report) without double-publishing
- [x] **CORE-09**: Pipeline classifies errors as transient (backoff retry) vs permanent (route to DLQ/descarte)
- [x] **CORE-10**: Celery + Redis run the pipeline 24/7 with beat scheduling and fan-out by UF (single-beat, idempotent tasks, poison-message quarantine)
- [x] **CORE-11**: Every external system (Places, OTA, Apify, WhatsApp, Mtur, NotebookLM, NorteiaApi) sits behind a client interface with a fake
- [x] **CORE-12**: FastAPI exposes webhooks (WhatsApp/email), REST for the dashboard, and lane ingest, with idempotent webhook receivers

### Score Engine §7.6

- [x] **SCORE-01**: Score engine computes a reliability score as a pure function: origem 30% · completude 20% · corroboração 20% · atualidade 15% · validação humana 15%
- [x] **SCORE-02**: Weights and Mar/DLQ/descarte thresholds are calibrable via config; scores are versioned against the weight set used
- [x] **SCORE-03**: One engine serves both destino and atrativo, unit-tested on Mar/DLQ/descarte boundary cases

### Observability

- [x] **OBS-01**: Pipeline records every LLM call in an `llm_generations` table (per-lane, per-model, with USD cost)
- [x] **OBS-02**: A USD cost guard enforces a spend ceiling and halts/throttles when exceeded
- [x] **OBS-03**: Pipeline exposes per-layer Brave metrics (volume, rates, throughput) and queue/worker health via FastAPI
- [x] **OBS-04**: Pipeline writes audit logs for steward and pipeline actions

### Ingestion Contract & Feedback Loop

- [x] **CNTR-01**: The Mar→norteia-api ingestion contract is frozen and verified by a Pact contract test
- [x] **CNTR-02**: A community error-report webhook reopens a published record back into Rio/DLQ (self-healing loop)

### Lane: Destinos

- [x] **DEST-01**: MturSeedIngest ingests categorized Mtur municipalities (Oferta Principal/Complementar/Apoio) → Nascente (`source=mtur`, origem=100), linked to `municipality_id`
- [x] **DEST-02**: NotebookLMIngest ingests structured reports → Nascente (`source=notebooklm`, origem=80) for destinos absent from Mtur
- [x] **DEST-03**: DesmembramentoAgent (§7.4) uses DeepSeek to list real destinos inside each Oferta Principal município (distritos/praias/vilas) → Nascente (origem=40, flagged "LLM-generated, pending validation"), with a mandatory Pydantic+`instructor` 2nd-layer validator
- [x] **DEST-04**: Destinos flow through Rio + score and land in DLQ by default (lacking human validation)
- [x] **DEST-05**: Steward validates destinos in DLQ batch-by-state (BA/RJ/SP/SC/CE/PE first) → validação humana=100 → Mar → push to `destinations`

### Lane: Atrativos

- [ ] **ATR-01**: An atrativo advances through a persisted, resumable sub-state machine: discovered → contacts_found → signals_gathered → (score) → [borderline] aguardando_consulta_whatsapp → whatsapp_in_progress → re-score
- [ ] **ATR-02**: DiscoveryAgent sweeps Google Places (UF/município) + gov, maps via DeepSeek → schema → Nascente, resolves the parent destino (already in Mar), and persists `place_id`
- [ ] **ATR-03**: ContactFinderAgent finds contacts via Places Details (phone/website/WhatsApp link) + site/IG-FB/email
- [ ] **ATR-04**: SignalAgent reads Places `business_status` (CLOSED_* → descarte), `weekday_text` hours, and `reviews[].publishTime ≤30d ⇒ funcionando`; IG/X via Apify best-effort
- [ ] **ATR-05**: WhatsApp gate lets a human approve which borderline (<85%) atrativos to contact, with a volume ramp
- [ ] **ATR-06**: WhatsAppAgent runs automated outreach (WhatsApp Business API + n8n thin transport + LangGraph logic): Sonnet asks PT-BR (identifies Norteia + opt-out), DeepSeek extracts existe?/funcionando?/horários/valor; owner-validation boosts score → re-score → Mar/DLQ

### Dashboard (Territorial CMS)

- [ ] **DASH-01**: DLQ review queue shows Nascente payload + Rio data + §7.6 per-criterion score + signals + WhatsApp log, with approve/reject/edit/reprocess and batch-by-state mode; edit triggers re-score
- [ ] **DASH-02**: Brave monitor (§15.7) shows volume per layer, approval/rejection/DLQ rates, failure alerts, throughput, and audit
- [ ] **DASH-03**: WhatsApp gate UI works the `aguardando_consulta_whatsapp` queue (approve/reject) with ramp context
- [ ] **DASH-04**: Cost & LLM view shows spend per lane/model from `llm_generations`
- [ ] **DASH-05**: Dashboard shows WhatsApp conversations and funnels (destinos & atrativos by UF/source)
- [x] **DASH-06**: Dashboard is access-controlled via Bearer-header auth

### Compliance

- [ ] **COMP-01**: Atrativos/WhatsApp lane enforces LGPD: legal basis + Norteia identification + opt-out + consent log + data minimization
- [ ] **COMP-02**: WhatsApp lane uses approved BSP templates, respects the 24h window, enforces human gate + ramp + opt-out, and auto-pauses on degraded quality rating
- [ ] **COMP-03**: Google Places usage persists only `place_id` as cache; canonical data is the first-party validated record

### Testability

- [x] **TEST-01**: Full suite runs 100% offline via docker-compose (Postgres+Redis); real externals are opt-in by flag; CI runs keyless
- [x] **TEST-02**: Score engine and DesmembramentoAgent have unit tests covering Mar/DLQ/descarte cases
- [x] **TEST-03**: HTTP boundaries faked with respx/VCR, LLM faked, webhooks fixture-driven; norteia-api contract covered by Pact

## v2 Requirements

Deferred to a future milestone. Tracked but not in the current roadmap.

### Freshness & Tuning

- **FRESH-01**: Active freshness-decay / re-score cron (§7.8) for aging Mar records
- **TUNE-01**: Auto-tuning of §7.6 weights from accumulated steward decisions
- **OTA-01**: OTA price cross-check (Viator/GYG/Booking, ticketed only) with partner onboarding

### Steward Analytics

- **STEW-01**: Per-steward throughput and accuracy-trend analytics

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Human-approves-every-record | Defeats the score-gate premise; can't scale to all-BR cold start; steward burnout |
| Hosting Brave inside norteia-api | External APIs would hit the platform hot path; user decision keeps engine in this Python repo |
| DLQ/monitor in norteia-api Filament CMS | Conscious deviation from doc §15.7 — this dashboard is the territorial CMS |
| norteia-api ingestion endpoints / migrations / webhook receiver | Built in the separate Laravel repo (Trilha 5); here only the Pact contract matters |
| Automated IG/FB DM outreach | Meta ToS gray/red zone, ban + legal risk; read-only Apify signal only |
| ML / learning-to-rank matcher | Premature without labeled volume; opaque, un-unit-testable; deterministic + NLP first |
| Future lanes (site-scraping monitor, business CMS, UGC) | Core must support, not build, this milestone |
| Future entities (experiência, evento, temporada, rota) | Entity-agnostic core proves extensibility; add post-validation |
| Temporal / durable-workflow engine | Outreach tolerates day-scale latency; Celery+Redis suffices; adopt only if proven need |
| Real-time / streaming pipeline | Batch enrichment domain; streaming adds complexity with no value |
| Multi-tenant / i18n / theming on dashboard | Single internal PT-BR ops tool; YAGNI |

## Traceability

Each v1 requirement maps to exactly one phase. See `.planning/ROADMAP.md` for phase detail.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CORE-01 | Phase 1 | Complete |
| CORE-02 | Phase 1 | Complete |
| CORE-03 | Phase 1 | Complete |
| CORE-04 | Phase 1 | Complete |
| CORE-05 | Phase 1 | Complete |
| CORE-06 | Phase 1 | Complete |
| CORE-07 | Phase 1 | Complete |
| CORE-08 | Phase 1 | Complete |
| CORE-09 | Phase 1 | Complete |
| CORE-10 | Phase 1 | Complete |
| CORE-11 | Phase 1 | Complete |
| CORE-12 | Phase 1 | Complete |
| SCORE-01 | Phase 1 | Complete |
| SCORE-02 | Phase 1 | Complete |
| SCORE-03 | Phase 1 | Complete |
| OBS-01 | Phase 1 | Complete |
| OBS-02 | Phase 1 | Complete |
| OBS-03 | Phase 1 | Complete |
| OBS-04 | Phase 1 | Complete |
| CNTR-01 | Phase 1 | Complete |
| CNTR-02 | Phase 1 | Complete |
| TEST-01 | Phase 1 | Complete |
| TEST-03 | Phase 1 | Complete |
| DEST-01 | Phase 2 | Complete |
| DEST-02 | Phase 2 | Complete |
| DEST-03 | Phase 2 | Complete |
| DEST-04 | Phase 2 | Complete |
| DEST-05 | Phase 2 | Complete |
| TEST-02 | Phase 2 | Complete |
| ATR-01 | Phase 3 | Pending |
| ATR-02 | Phase 3 | Pending |
| ATR-03 | Phase 3 | Pending |
| ATR-04 | Phase 3 | Pending |
| ATR-05 | Phase 3 | Pending |
| ATR-06 | Phase 3 | Pending |
| COMP-01 | Phase 3 | Pending |
| COMP-02 | Phase 3 | Pending |
| COMP-03 | Phase 3 | Pending |
| DASH-01 | Phase 4 | Pending |
| DASH-02 | Phase 4 | Pending |
| DASH-03 | Phase 4 | Pending |
| DASH-04 | Phase 4 | Pending |
| DASH-05 | Phase 4 | Pending |
| DASH-06 | Phase 4 | Complete |

**Coverage:**
- v1 requirements: 44 total (CORE 12 · SCORE 3 · OBS 4 · CNTR 2 · DEST 5 · ATR 6 · DASH 6 · COMP 3 · TEST 3)
- Mapped to phases: 44 ✓
- Unmapped: 0 ✓

> Note: the prior header count of "38" was a miscount of the enumerated IDs; the canonical v1 set is the 44 IDs above, all mapped.

---
*Requirements defined: 2026-06-11*
*Last updated: 2026-06-11 after roadmap creation (traceability populated, coverage 44/44)*
