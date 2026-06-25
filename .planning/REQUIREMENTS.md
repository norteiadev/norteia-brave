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

- [x] **DASH-01**: DLQ review queue shows Nascente payload + Rio data + §7.6 per-criterion score + signals + WhatsApp log, with approve/reject/edit/reprocess and batch-by-state mode; edit triggers re-score
- [x] **DASH-02**: Brave monitor (§15.7) shows volume per layer, approval/rejection/DLQ rates, failure alerts, throughput, and audit
- [x] **DASH-03**: WhatsApp gate UI works the `aguardando_consulta_whatsapp` queue (approve/reject) with ramp context
- [x] **DASH-04**: Cost & LLM view shows spend per lane/model from `llm_generations`
- [x] **DASH-05**: Dashboard shows WhatsApp conversations and funnels (destinos & atrativos by UF/source)
- [x] **DASH-06**: Dashboard is access-controlled via Bearer-header auth

### Compliance

- [ ] **COMP-01**: Atrativos/WhatsApp lane enforces LGPD: legal basis + Norteia identification + opt-out + consent log + data minimization
- [ ] **COMP-02**: WhatsApp lane uses approved BSP templates, respects the 24h window, enforces human gate + ramp + opt-out, and auto-pauses on degraded quality rating
- [ ] **COMP-03**: Google Places usage persists only `place_id` as cache; canonical data is the first-party validated record

### Testability

- [x] **TEST-01**: Full suite runs 100% offline via docker-compose (Postgres+Redis); real externals are opt-in by flag; CI runs keyless
- [x] **TEST-02**: Score engine and DesmembramentoAgent have unit tests covering Mar/DLQ/descarte cases
- [x] **TEST-03**: HTTP boundaries faked with respx/VCR, LLM faked, webhooks fixture-driven; norteia-api contract covered by Pact

### Orchestration (Phase 5 — gap closure)

- [x] **ORCH-01**: Implement the registered `brave.sweep_uf` Destinos sweep task the beat fan-out fires per UF (currently a dangling phantom — no such task exists). The daily sweep runs a real recurring producer: DesmembramentoAgent per Oferta-Principal município + idempotent Mtur seed re-ingest (NotebookLM stays manual ingest — deferred). Replay-safe (idempotent by source_ref/content_hash).
- [x] **ORCH-02**: Auto-advance the Atrativos sub-state FSM — `discover_atrativo_task` enqueues `find_contacts_task` per discovered record, which enqueues `gather_signals_task` → §7.6 score → borderline lands in `aguardando_consulta_whatsapp`. Idempotent, replay-safe, keyed on `sub_state` (no double-advance on retry). Today these tasks exist but are never enqueued, so the FSM stalls at `discovered`.
- [x] **ORCH-03**: On-demand ops trigger (CLI command and/or internal endpoint) to kick a UF sweep for destinos and/or atrativos without waiting for the beat schedule.
- [x] **ORCH-04**: All new orchestration is 100%-offline-testable (fakes + opt-in real flag), and the human WhatsApp gate + outreach are unchanged — discovery/contacts/signals automate up to the gate; no automatic send.

### Engine Control: Stage-Depth Selector (Phase 10 — cost gating)

- [x] **ENG-01**: The /processo engine control exposes a pipeline-depth selector with exactly three options — `Apenas nascente`, `Nascente → Rio`, `Nascente → Rio → Mar`. A depth selection is **required** to enable the "Ligar motor" button (button disabled until the operator chooses).
- [x] **ENG-02**: The chosen depth is persisted in Redis using the existing `brave:engine:*` convention; `POST /api/v1/engine/start` accepts the depth in its body and `GET /api/v1/engine/status` exposes the current depth back to the dashboard. *(10-01: `brave:engine:depth` + set_depth/get_depth, required-depth /start, depth on /status.)*
- [x] **ENG-03**: Depth `Apenas nascente` runs only the free Mtur seed producer → `store_raw` + §7.6 score; it does NOT call `process_nascente_record` (Rio), NOT run Desmembramento (LLM), NOT run atrativos discovery (Places) — zero external cost. Atrativos do not run in this mode (no free source today).
- [x] **ENG-04**: Depth `Nascente → Rio` runs producers + validation up to Rio routing (mar-eligible / dlq / descarte) but does NOT promote any record to Mar and does NOT dispatch the WhatsApp gate/outreach.
- [x] **ENG-05**: Depth `Nascente → Rio → Mar` runs the full pipeline including the idempotent norteia-api Mar push (unchanged contract).
- [x] **ENG-06**: A per-entity "nascente" StageBadge variant renders in the dashboard (`StageBadge.tsx`) for records that exist only in the Nascente layer (no Rio row yet).
- [x] **ENG-07**: All Phase 10 behavior is 100%-offline-testable — backend via pytest with fakeredis (no broker, no externals), dashboard via Vitest + MSW. No test depends on `RUN_REAL_EXTERNALS`.

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
| DASH-01 | Phase 4 | Complete |
| DASH-02 | Phase 4 | Complete |
| DASH-03 | Phase 4 | Complete |
| DASH-04 | Phase 4 | Complete |
| DASH-05 | Phase 4 | Complete |
| DASH-06 | Phase 4 | Complete |
| ORCH-01 | Phase 5 | Complete |
| ORCH-02 | Phase 5 | Complete |
| ORCH-03 | Phase 5 | Complete |
| ORCH-04 | Phase 5 | Complete |
| TA-01 | Phase 11 | Pending |
| TA-02 | Phase 11 | Pending |
| TA-03 | Phase 11 | Pending |
| TA-04 | Phase 11 | Pending |
| TA-05 | Phase 11 | Pending |
| TA-06 | Phase 11 | Pending |
| TA-07 | Phase 11 | Pending |
| TA-08 | Phase 11 | Pending |
| TA-09 | Phase 12 | Complete |
| TA-10 | Phase 12 | Complete |
| TA-11 | Phase 12 | Complete |
| TA-12 | Phase 12 | Complete |
| TA-13 | Phase 12 | Complete |
| TA-14 | Phase 14 | Complete |
| TA-15 | Phase 14 | Complete |

**Phase 11 — TripAdvisor source lane (TA-01 … TA-08):**
- **TA-01** — `brave/lanes/tripadvisor/` GraphQL hybrid client: Playwright bootstraps a DataDome session, captures the rotating `queryId` live (never hardcoded), injects cookies into `httpx` for persisted-query POSTs; residential-proxy seam; Playwright lazy-imported (never in CI); Null/Fake clients + `TripAdvisorConfig` (`BRAVE_TA_*`). UF→`geoId` resolution cached (Redis + 27-UF seed JSON).
- **TA-02** — Producers `TripAdvisorDestinosIngest` + `TripAdvisorAtrativosIngest` (`produce(uf, *, run_rio=True)`, mirror Mtur) scrape per UF and write Nascente via `store_raw` → `process_nascente_record`; `source='tripadvisor'`.
- **TA-03** — IBGE linkage via local `data/ibge/ibge_municipios.csv` (rapidfuzz name + haversine fallback); no-match → quarantine `ibge_unmatched`. Attraction parent = destino **RioRecord produced in the same sweep** (carry `parent_rio_id`/`parent_source_ref`; `parent_mar_id` only if already in Mar); quarantine `parent_destino_absent` only when no destino RioRecord exists.
- **TA-04** — Reviews → §7.6: `corroboracao` (volume/rating) + `atualidade` (recency); `origem=65`; LGPD-safe (only `review_count`/`rating`/`most_recent_review_at`, never author/text). Calibrated so typical record routes DLQ (never auto-Mar).
- **TA-05** — `mar_ready` flag (migration 0006 `rio_records.mar_ready`) set in `route_by_score` for TA attractions with `atualidade≥70` & `corroboracao≥bar`; audited human promote-override (`promote_override`) bypasses the ≥85 gate only for `mar_ready` records, with `promotion_reason` provenance + audit log.
- **TA-06** — Engine source-awareness: `sweep_tripadvisor` task; `engine_sweep_run` source branch; engine `set_source/get_source` (whitelist, fail-closed); `/engine/start` accepts+validates `source` (422); promote-override API single (`PATCH /atrativos/{id}/promote`) + batch (`POST /atrativos/promote-batch`), steward-auth, non-`mar_ready` → 409.
- **TA-07** — Dashboard: source + UF selector on EngineControl; new `/mar-ready` route (nav `SURFACES`) with optimistic single + bulk multi-select promote (mirror DLQ actions); MSW/Vitest.
- **TA-08** — Compliance: `data/tripadvisor/README` legal-risk note (ToS, mitigations, operator-gated), lane docstring note, root `SOURCES.md` index. 100% offline tests by default; live scrape only via opt-in `@pytest.mark.real_browser`.

**Phase 12 — TripAdvisor session-injection seam (TA-09 … TA-13):**
> Validated by spike 2026-06-24: a browser-earned DataDome cookie survives `httpx` replay (200 + real data, same IP, different TLS/JA3). Real persisted-query format is `extensions.preRegisteredQueryId` (batch array), NOT `{"query": queryId}`. Design doc: `~/.gstack/projects/norteia-brave/leandro-main-design-20260624-121942.md`.
- **TA-09** — Operator session-acquisition runbook: capture a TripAdvisor session (cookies incl. `datadome` + `preRegisteredQueryId`) from a real logged-in browser (DevTools Copy-as-cURL / `/browse` handoff), since automated browsers (httpx, headless, headed+stealth) are 403-walled by DataDome from a datacenter/home IP. Ships as repo runbook (`data/tripadvisor/README` operator-gate section + a `scripts/ta_bootstrap` helper).
- **TA-10** — `POST /api/v1/tripadvisor/session` endpoint: `require_steward_or_bearer` auth; Pydantic body (`cookies`, `query_ids`, `user_agent`, `client_hints`, `locale`, `acquisition_ip`, `acquired_at`; `extra="forbid"`; 64 KB size limit; 422 on malformed); writes Redis `BRAVE_TA_SESSION_KEY` with `BRAVE_TA_SESSION_TTL`; never logs cookie values (audit-log keys/counts only).
- **TA-11** — Canary validation gate: on inject, synchronously run ONE real `graphql/ids` request through the production `httpx` path (15 s hard timeout) → `ready` (200 + non-empty data) or `invalid_session` (403/captcha/empty-payload/timeout → delete the Redis key, return 422). `GET /api/v1/tripadvisor/session/status` → `{present, expires_in, query_ids}` for dashboard session health.
- **TA-12** — Client refactor: `_get_session()` reads Redis only, raises `SessionMissingError` on miss/expiry; **fix the persisted-query payload to `{"variables": {...}, "extensions": {"preRegisteredQueryId": "<id>"}}`** (batch-array shape); remove the Playwright `_bootstrap_session` + thread-offload + the `scraper` optional dependency; verify real attraction `geoId`s (seed ES 303516 redirected to MG 303380).
- **TA-13** — Sweep fail-fast + visibility: `sweep_tripadvisor` catches `SessionMissingError` and the FIRST mid-sweep 403/captcha/empty-payload/stale-`queryId` → stop, mark `needs_bootstrap`, no retry-storm; surface session state (`needs_bootstrap`/`ready`/`invalid`/`expired`) to the operator dashboard instead of silent 0-records. Sweeps capped by record count + wall-clock budget under the session TTL. Operator-gated; NOT on the autonomous beat.

**Phase 14 — Coordless attraction geo-resolution via Nominatim (TA-14 … TA-15):**
> Spike-validated 2026-06-25 (`scripts/spike_nominatim_geo.py`, 10 real BR attractions): forward-geocoding `name + UF` through OpenStreetMap Nominatim geocoded 10/10; `addressdetails=1` → `address.municipality|city|town|village|county` name-matched to IBGE gave the exact município 9–10/10 (lone miss = multi-município national park). IBGE coords are the município *seat*, so haversine needs a relaxed (~50 km) radius for natural attractions (15–25 km from seat). Free, no API key, HTTP (not browser-scraping). Closes the Phase-13 `ibge_unmatched` quarantine gap.
- **TA-14** — Typed, mockable Nominatim geocoding client behind the network boundary (Null + Fake, respx in tests, `RUN_REAL_EXTERNALS` opt-in, never in CI): forward-geocode `name + UF` via the public Nominatim `search` API with `addressdetails=1`, a custom User-Agent, and ≥1 req/s rate limiting; results cached by `locationId` (Redis). LGPD-safe: persist only lat/lon + OSM place id, never address PII.
- **TA-15** — Atrativos geo-enrichment integration: in `_ingest_one`/`resolve_municipio`, before quarantining `ibge_unmatched`, geocode the card → extract the município name (primary) → exact/fuzzy IBGE name-match within the UF; fall back to haversine on the returned lat/lon with a relaxed radius (~50 km). Quarantine `ibge_unmatched` only after BOTH name-match and geo-enrichment fail. Offline tests (respx-mocked Nominatim) + Level-3 re-validation: a real MG sweep yields Nascente `entity_type='attraction'` > 0 with municípios resolved (not mass-quarantined).

**Coverage:**
- v1 requirements: 48 total (CORE 12 · SCORE 3 · OBS 4 · CNTR 2 · DEST 5 · ATR 6 · DASH 6 · COMP 3 · TEST 3 · ORCH 4)
- Mapped to phases: 44 ✓
- Unmapped: 0 ✓

> Note: the prior header count of "38" was a miscount of the enumerated IDs; the canonical v1 set is the 44 IDs above, all mapped.

---
*Requirements defined: 2026-06-11*
*Last updated: 2026-06-25 — added TA-14/TA-15 (Phase 14: coordless attraction geo-resolution via Nominatim, follow-up to Phase 13)*
