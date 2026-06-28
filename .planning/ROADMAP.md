### Phase 10: Engine Stage-Depth Selector (cost-gated collection)

**Goal**: An operator selects the pipeline depth — `Apenas nascente` | `Nascente → Rio` | `Nascente → Rio → Mar` — on the /processo engine control before starting the sweep, and the selection is required to enable the "Ligar motor" button. The engine honors the depth as a real cost boundary: `Apenas nascente` runs only the free Mtur seed (store_raw + §7.6 score; no `process_nascente_record`/Rio, no Desmembramento LLM, no atrativos Places — zero external cost), `Nascente → Rio` runs producers + validation up to Rio routing (mar-eligible/dlq/descarte) without promoting to Mar or dispatching WhatsApp, and `Nascente → Rio → Mar` runs the full pipeline including the idempotent norteia-api Mar push. A per-entity "nascente" StageBadge variant renders for records that exist only in Nascente. Net effect: an operator can populate the territorial base (destinos↔municípios) at zero external cost and ramp spend (Google Places 1000/mo free tier, LLM budget) deliberately — "rodar aos poucos".
**Requirements**: ENG-01, ENG-02, ENG-03, ENG-04, ENG-05, ENG-06, ENG-07
**Depends on:** Phase 9
**Plans:** 4/4 plans complete

Plans:
- [x] 10-01-PLAN.md — Engine depth state (Redis `brave:engine:depth`) + server-side required-depth validation on `/start` + depth on `/status` (ENG-01/02)
- [x] 10-02-PLAN.md — Orchestrator + destinos-lane depth gating: nascente=Mtur-only/no-Rio/no-LLM/no-atrativos, nascente_rio=Rio-no-gate, nascente_rio_mar=full (ENG-03/04/05)
- [x] 10-03-PLAN.md — Dashboard depth selector + disabled-until-chosen "Ligar motor" + depth in start body + MSW/Vitest (ENG-01/02 client)
- [x] 10-04-PLAN.md — StageBadge "nascente" variant for Nascente-only records + Vitest (ENG-06)

**Locked decisions (see 10-CONTEXT.md):** stage map as cost checkpoints (Nascente = free ingest+score; Nascente→Rio = Places+LLM; Rio→Mar = WhatsApp+human); atrativos have no free source today, so `Apenas nascente` runs destinos (Mtur) only; keep table-per-layer model (no `incoming_attractions(status)` migration); WhatsApp stays single-channel. Out of scope (future phases): gov source for atrativos + moving Places to the Nascente→Rio edge (Phase B); structured hours/price (Phase C); free LLM model for Desmembramento; dedicated contacts table.

### Phase 13: TripAdvisor real listing query — identify + wire data-fetch contract (GAP-12-A)

**Goal:** Close GAP-12-A (12-HUMAN-UAT.md): the TripAdvisor lane does not yet collect real data because `client.py`'s Phase-11 `{locationId, offset, limit}` variables match NO real query. The REAL listing query was **identified + live-validated 2026-06-24** (4th operator capture, see `GAP-12-A-FINDINGS.md`): qid **`a5cb7fa004b5e4b5`** (AttractionsFusion) with `request.routeParameters{geoId,contentType:"attraction",webVariant,filters}` + `sessionId`; response parsed from `data.Result[0].sections[]` → `WebPresentation_SingleFlexCardSection` (30 cards/page, mapping name/locationId/rating/reviewCount/category). Phase 12's session-injection seam is DONE (do not rebuild). This phase wires the lane to that query: (1) rebuild `fetch_attractions` around the real qid + variables + `sections[]` parse, threading `sessionId`; (2) fix `_run_canary` to probe the real query; (3) extend `ta_bootstrap` to capture TASID `sessionId` + the listing qid and REJECT ad/telemetry/trips qids; (4) update the TA-09 runbook; (5) re-run Level 3 to confirm Nascente records > 0. (Destinos contentType + multi-page `oa30` pagination are documented follow-ups.)
**Requirements**: TA-12 (data-fetch correctness — extends Phase 12)
**Depends on:** Phase 12
**Plans:** 3/3 plans complete

Plans:
- [x] 13-01-PLAN.md — Rewire fetch_attractions to AttractionsFusion query (qid a5cb7fa004b5e4b5 + sections[] parse) + session_id model + ta_bootstrap TASID/qid-reject (TA-12)
- [x] 13-02-PLAN.md — _run_canary probes real listing query + atrativos._ingest_one card-field mapping (most_recent_review_at=None) (TA-12)
- [x] 13-03-PLAN.md — Update README + RUNBOOK-NIVEL3 capture instructions + operator Level-3 checkpoint (TA-12)

---

### Phase 11: TripAdvisor source lane (GraphQL scraper → Nascente)

**Goal**: A new `brave/lanes/tripadvisor/` data source produces **destinos and atrativos** per UF from a self-hosted GraphQL hybrid scraper (Playwright bootstraps a DataDome session → cookies injected into `httpx` → persisted-query POSTs to TripAdvisor's `/data/graphql/ids`, with the rotating `queryId` captured live, never hardcoded). It resolves each UF to TripAdvisor's internal `geoId` (cached) and links attractions to their município via a local `data/ibge/ibge_municipios.csv` table (fuzzy name + haversine), with no-match → quarantine. Reviews feed §7.6 (`corroboracao` from volume/rating + `atualidade` from recency), so records land in DLQ by score (never auto-Mar). This is the first **free** attraction source (no per-record API fee; infra cost only) and fills the deferred "gov source" gap. Review-validated attractions are flagged **`mar_ready`** and an operator promotes them to Mar — single or bulk — via a new `/mar-ready` dashboard route through an **audited human promote-override** that bypasses the ≥85 gate only for that flagged, operator-authorized set (the canonical gate is untouched for everything else). Operator selects the TripAdvisor source + UFs on `/processo` with the existing depth gating; sweep never auto-promotes; TA attractions never enter WhatsApp outreach; LGPD-safe (only aggregate review fields persisted, never author/text).
**Requirements**: TA-01, TA-02, TA-03, TA-04, TA-05, TA-06, TA-07, TA-08
**Depends on:** Phase 10
**Plans:** 5/5 plans complete

Plans:
- [x] 11-01-PLAN.md — Client + anti-bot session (Playwright bootstrap, live queryId capture, cookie→httpx GraphQL, proxy seam) + geo UF→geoId cache + `TripAdvisorConfig` + Null/Fake clients + `scraper` optional dep (TA-01)
- [x] 11-02-PLAN.md — Schemas (LGPD-safe) + IBGE table/resolver + destinos/atrativos producers + reviews→§7.6 scoring + `mar_ready` in `route_by_score` + parent-via-RioRecord linking (TA-02/03/04)
- [x] 11-03-PLAN.md — Migration 0006 (`rio_records.mar_ready`) + `sweep_tripadvisor` task + `engine_sweep_run` source branch + engine `set_source/get_source` + `/engine/start` source + audited promote-override API (single/batch) (TA-05/06)
- [x] 11-04-PLAN.md — Dashboard: source+UF selector on EngineControl + new `/mar-ready` route with optimistic single/bulk multi-select promote + MSW/Vitest (TA-06/07 client)
- [x] 11-05-PLAN.md — Compliance/docs: `data/tripadvisor/README` legal-risk note + lane docstring + root `SOURCES.md` index (TA-08)

**Locked decisions (see 11-CONTEXT.md):** GraphQL hybrid acquisition (Playwright session bootstrap + live queryId capture + httpx persisted queries; residential-proxy seam; Playwright lazy-imported, never in CI); per-UF via cached geoId; IBGE link = local table only; reviews → corroboração+atualidade calibrated to DLQ; new lane `source='tripadvisor'`; parent = destino **RioRecord produced in the same sweep** (diverges from Places' Mar-only resolution — quarantine only when no destino RioRecord exists); audited human promote-override gated to `mar_ready` (bypasses ≥85 only for operator-authorized review-validated attractions); `/mar-ready` dashboard route + `SURFACES` entry; no WhatsApp for TA attractions; LGPD = aggregate review fields only. Out of scope: structured hours/price; multichannel contact; auto-scheduling TA on the autonomous beat (operator-gated this phase).

### Phase 12: TripAdvisor session-injection seam (real-browser bootstrap → httpx)

**Goal**: Make the TripAdvisor lane actually collect data by splitting **session acquisition** (hard, anti-bot, operator-gated) from **bulk fetch** (cheap, deterministic). Validated by a 2026-06-24 spike: a `datadome` cookie + TA session cookies captured from a real logged-in browser **survive replay through the worker's `httpx`** (HTTP 200 + real GraphQL data, same IP, different TLS/JA3) — so acquisition and fetch can be decoupled. A new `POST /api/v1/tripadvisor/session` endpoint (steward/bearer auth, Pydantic-validated, size-limited, cookie-redacted) writes an operator-captured session into Redis (`BRAVE_TA_SESSION_KEY`); a **canary gate** immediately validates it through the production `httpx` path (`ready` vs `invalid_session`, key deleted on fail); `GET /tripadvisor/session/status` surfaces health. The client is refactored to read the injected session only (`SessionMissingError` on miss), the **persisted-query payload is corrected to `extensions.preRegisteredQueryId`** (the real batch-array format — the Phase 11 `{"query": queryId}` shape was wrong), and the Playwright `_bootstrap_session` + `scraper` dependency are removed. `sweep_tripadvisor` fails fast on a missing/expired/stale session (`needs_bootstrap`, no retry-storm) and surfaces session state to the dashboard instead of silently ingesting 0 records. Operator-gated best-effort, **not** a 24/7 autonomous lane.
**Requirements**: TA-09, TA-10, TA-11, TA-12, TA-13
**Depends on:** Phase 11
**Plans:** 4/4 plans complete

Plans:
- [x] 12-01-PLAN.md — Operator acquisition runbook (data/tripadvisor/README) + scripts/ta_bootstrap helper (cURL → POST /tripadvisor/session) (TA-09)
- [x] 12-02-PLAN.md — POST /api/v1/tripadvisor/session endpoint (Pydantic-validated, size-limited, Redis write + canary gate) + GET /tripadvisor/session/status + offline fakeredis tests (TA-10, TA-11)
- [x] 12-03-PLAN.md — Client refactor: SessionMissingError + _get_session Redis-only + fix extensions.preRegisteredQueryId payload + remove _bootstrap_session/Playwright/scraper dep + geoId verification (TA-12)
- [x] 12-04-PLAN.md — sweep_tripadvisor fail-fast (SessionMissingError/SessionExpiredError → needs_bootstrap, no retry-storm) + EngineControl session-health pill (TA-13)

**Locked decisions (design doc `~/.gstack/projects/norteia-brave/leandro-main-design-20260624-121942.md`, office-hours stress-tested 9/10, spike-validated):** split acquisition from fetch (Approach A, spike confirmed cookie portability → no need for browser-side fetch); session acquired by a real human browser (DevTools Copy-as-cURL / `/browse` handoff) — automated browsers are DataDome-walled from this IP; injection via `POST /tripadvisor/session` → Redis (reuse `BRAVE_TA_SESSION_KEY`); mandatory canary gate (sync httpx, 15 s timeout, delete key on fail); client reads Redis only, no auto-bootstrap, Playwright removed; fix payload to `extensions.preRegisteredQueryId`; fail-fast sweep with explicit operator state; runbook + `scripts/ta_bootstrap` helper. Out of scope: residential-proxy automation; sweep-level checkpointing (deferred — sweeps sized under TTL); autonomous 24/7 TA (would need a paid/licensed source or managed browser+proxy stack); the `5-attraction cap` test (handled separately later). Known follow-up: characterize real DataDome token lifetime; verify real attraction geoIds.

### Phase 15: TripAdvisor full oa30 pagination + bulk Nascente collection + live sweep dashboard panel

**Goal:** Collect ALL ~10,000 Brazil attractions from TripAdvisor (geoId 294280) into Nascente by paginating the AttractionsFusion listing, with a live visual progress panel on the dashboard while the sweep runs. Phase 13 wired the single-page real listing query (qid `a5cb7fa004b5e4b5`) but explicitly deferred multi-page `oa30` pagination; this phase closes that gap end-to-end.

**Pagination mechanism (discovered + live-validated 2026-06-26, see memory `tripadvisor-graphql-real-shape`):** offset is **path-based, NOT a GraphQL variable**. `totalResults:10000` (TA hard cap), `limit:30`/page → **334 pages**. Offset lives in the URL path segment `-oa{N}-` where N=page×30 (`oa0`,`oa30`…`oa9990`), sourced from the response's `WebPresentation_PaginationLinksList`. The `a5cb7fa004b5e4b5` persisted query **rejects** any offset/oa/page/skip field in `request.routeParameters` (`"Variable $request got invalid value"` — fixed input schema). Working transport = **HTML SSR page** `GET /Attractions-g{geoId}-Activities-a_allAttractions.true-oa{N}-Brazil.html` **with the full operator cookie jar → HTTP 200 ~1.5 MB**, embedding the same 30 FlexCards/page; reuse the existing `_parse_attractions_page` on the embedded `sections[]`. (Contradicts the older "HTML navigation → 403" note: that was a plain GET; with the captured datadome+session jar the HTML page is NOT walled.)

**Locked scope decisions (operator-chosen this session):**
1. **Transport:** HTML SSR extract — GET the `-oa{N}-` page, pull the embedded `sections[]`, reuse `_parse_attractions_page`. One transport for all pages.
2. **Run scope — slice-first:** validate a small slice (~5–10 pages / 150–300 attractions) end-to-end into Nascente FIRST to prove DataDome endurance over sequential requests + throttle + the dashboard panel, THEN scale to the full 334. Requires resume-from-offset to recover from mid-run session expiry.
3. **Dashboard — NEW live progress panel:** polls a Redis progress key (pages done/334, attractions ingested, current offset, errors, rate) via a FastAPI status endpoint + a Next.js page. Real-time visual of the running sweep.

**Requirements**: extends TA-12 (data-fetch correctness — Phases 12/13)
**Depends on:** Phase 13 (real listing query wired), Phase 14
**Out of scope:** per-UF pagination (this is whole-Brazil g294280); destinos-lane pagination; autonomous 24/7 TA beat (stays operator-gated); residential-proxy automation.
**Plans:** 8/8 plans complete

Plans:
- [x] 15-01-PLAN.md — Capture + scrub the real -oa30- AttractionsFusion HTML fixture (Wave-0 extractor blocker)
- [x] 15-02-PLAN.md — Interface contracts: fetch_attractions_paginated + geocode_national (protocol/null/fake)
- [x] 15-03-PLAN.md — Live progress backend: sweep_progress Redis module + GET /sweep/progress endpoint
- [x] 15-04-PLAN.md — Real HTML-SSR transport: _extract_sections_from_html + fetch_attractions_paginated + throttle config
- [x] 15-05-PLAN.md — National geo-resolution: geocode_national + resolve_municipio_national (A1 blocker)
- [x] 15-06-PLAN.md — Bulk Nascente ingest: _ingest_one_bulk (no parent gate) + produce_paginated (per-page commit + progress)
- [x] 15-07-PLAN.md — sweep_tripadvisor bulk national branch + resume + fail-fast progress + operator slice trigger
- [x] 15-08-PLAN.md — Dashboard live sweep progress panel (mirror EngineControl; MSW + Vitest)

### Phase 17: Painel Brave redesign — light-theme single-shell + Painel Kanban view (slice 1)

**Goal:** Implement the first slice of the "Painel Brave" CMS redesign (Claude Design import `Painel Brave.dc.html`, saved at `design/Painel-Brave.dc.html`): a **light-theme single-shell** (232px white sidebar with 6 nav items + Geist logo + operator footer; 58px topbar with page title, TripAdvisor session pill, source modal trigger, and the motor on/off switch) hosting a **view-switcher**, and the first real view — **Painel (Kanban)**: two metric cards (Destinos / Atrativos: total no escopo, sincronizados, falhas, progresso %), a type-filter segmented control (Tudo / Destinos / Atrativos), a UF-scope dropdown, and horizontal-scroll **stage columns** of draggable record cards (chip, score band, name, UF, município, source label, "possível duplicado" flag, and ⚠ falha + ↺ reprocessar on failed cards). Wired to the EXISTING API clients/endpoints (engine status, destinos, atrativos) — NOT a static mock. Lands at a NEW route `/painel` ALONGSIDE the existing 10 dark routes (incremental, non-breaking).

**Approach (operator-decided 2026-06-27):** incremental new shell at `/painel` side-by-side with the current routes (do not replace them this slice); first slice = shell + Painel/Kanban wired to real data; the two view s with no backend (Duplicados dedup-pairs, Mapeamento data-mapper) are MSW-mocked in LATER slices, not now; the remaining views (Conversas, Custo, Varreduras) are later slices reusing `conversations-api`/`cost-api`. Design language: Geist + Geist Mono (already loaded), light cream bg `oklch(0.98 0.01 90)`, brand navy `#15315e`, borders `#e6e4e0`/`#f0eee9`, status green `oklch(0.55 0.15 150)` / red `oklch(0.55 0.20 27)` / yellow `oklch(0.72 0.15 75)`.

**Requirements**: UI redesign (Painel Brave CMS) — slice 1
**Depends on:** Phase 8 (ops CMS), Phase 10 (engine depth/source/UF), Phase 15 (TA sweep + panel)
**Out of scope (later slices):** full replace of the 10 dark routes; Duplicados, Mapeamento, Conversas, Custo, Varreduras views; the record-edit drawer (Dados/Conversa tabs); the source/depth modal beyond the topbar trigger; new backend endpoints (dedup-pairs, data-mapper); dark/light theme toggle.
**Plans:** 5/5 plans complete

Plans:
- [x] 17-01-PLAN.md — Scoped light tokens + single-shell (sidebar/topbar/view-switcher) at /painel; topbar wired to engine-api (motor switch, TA pill, source) [wave 1]
- [x] 17-02-PLAN.md — Painel data layer: unified PainelCard model + pure selectors (columns/metrics/filter) + usePainelBoard hook over destinos/atrativos lists [wave 1]
- [x] 17-03-PLAN.md — Metric cards (Destinos/Atrativos) + type segmented control + UF-scope multi-select dropdown [wave 2]
- [x] 17-04-PLAN.md — Kanban: 5 stage columns + draggable record cards reusing StageBadge score bands [wave 2]
- [x] 17-05-PLAN.md — Wire drag-drop + ↺ Reprocessar to REAL mutations (promote/descarte/reprocess); unmapped drops revert+toast; optimistic + invalidate [wave 3]

---

### Phase 17.1: Painel Brave — remaining pages + real backend (slice 2)

**Goal:** Complete the "Painel Brave" CMS (Claude Design `Painel Brave.dc.html`): finish ALL six views against REAL data and make the **destinos+atrativos synchronization work through the Kanban structure** end-to-end. Upgrade the Kanban to the design's **6 stage columns** (nascente, rio·validação, whatsapp·contato, mar·publicado, dlq·revisão, falha) with the Destinos/Atrativos metric cards, type filter, UF-scope menu, and **full-pipeline drag-drop** (drops fire real backend stage transitions, incl. transitions not yet supported — move-backward/force-stage — with safe revert+toast on reject). Build the two NET-NEW backend features: **Duplicados** (dedup-pairs list from pgvector candidate-vs-Mar + resolve merge/keep/discard, Pact contract) and **Varreduras** (persisted runs-history table written by the engine + list endpoint by UF/source/depth with total/synced/failed + reprocess-failures, Pact contract). Wire the **Origem** modal (mtur/tripadvisor/google_places + TripAdvisor cURL session paste, session pill/toast TTL) and the **Motor** on/off toggle to existing engine/TA endpoints. Cross-wire the already-built Mapeamento/Conversas/Custo/record-drawer views into the finished shell.

**Approach (operator-decided 2026-06-27):** continuation of closed Phase 17 (slice 1 shell+Kanban shipped). User-confirmed MAXIMUM scope: full backend for Duplicados + Varreduras (DB migrations + endpoints + Celery write paths + Pact), and full-pipeline drag (new stage-transition endpoints, not just the safe allow-list). Reuse existing wiring: promote/descarte/reprocess, DLQ validate, atrativos advance/promote-batch/whatsapp-gate, cost, conversations, engine status, TA session. Backend stage values confirmed present: routing (in_progress/mar/dlq/descarte) + atrativos sub_state (discovered/contacts_found/signals_gathered/aguardando_consulta_whatsapp/whatsapp_in_progress). 100%-offline test mandate holds (respx/MSW; no real externals in CI). LGPD phone masking preserved in Conversas.

**Requirements**: UI redesign (Painel Brave CMS) — slice 2 (all views + dedup-pairs backend + runs-history backend + full-pipeline kanban sync)
**Depends on:** Phase 17 (shell + Kanban slice 1), Phase 8 (ops CMS endpoints), Phase 10 (engine depth/source/UF), Phase 11–15 (TripAdvisor lane + session)
**Out of scope:** replacing the 10 legacy dark routes; dark/light theme toggle; new collection lanes; changing the §7.6 score engine.
**Plans:** 1/7 plans executed

Plans:
- [x] 17.1-01-PLAN.md — Duplicados backend: compute-on-read field-similarity dedup pairs + resolve merge(union provenance)/keep/discard, audited [wave 1]
- [ ] 17.1-03-PLAN.md — Generic audited per-entity `transition` endpoint + server-side edge allow-list (mar→* stays 409) [wave 1]
- [ ] 17.1-02-PLAN.md — Varreduras backend: RunHistory model + migration 0007 + engine/sweep write points + runs list/reprocess + offline write-path test [wave 2]
- [ ] 17.1-04-PLAN.md — Duplicados frontend: dedup client + MSW + PainelDuplicados view [wave 2]
- [ ] 17.1-06-PLAN.md — Board 6-column model (whatsapp/falha sourcing) + client transition allow-list mirroring server [wave 2]
- [ ] 17.1-05-PLAN.md — Varreduras frontend: runs client + MSW + PainelVarreduras table [wave 3]
- [ ] 17.1-07-PLAN.md — Origem modal + TA cURL inject + Motor depth toggle + two-group nav + view-switcher wiring [wave 4]

**Plans:** 7 plans

Plans:
- [ ] 17.1-01-PLAN.md — Duplicados backend: compute-on-read dedup-pairs list + merge/keep/discard resolve (audited) [wave 1]
- [ ] 17.1-03-PLAN.md — Generic audited stage-transition endpoint per entity + server-side edge allow-list (mar→* 409) [wave 1]
- [ ] 17.1-02-PLAN.md — Varreduras backend: RunHistory model + 0007 migration + engine/sweep write points + runs list/reprocess [wave 2]
- [ ] 17.1-04-PLAN.md — Duplicados frontend: dedup client + MSW handler + PainelDuplicados view [wave 2]
- [ ] 17.1-06-PLAN.md — Board 6-column model + whatsapp/falha sourcing + full-pipeline transition allow-list (client) [wave 2]
- [ ] 17.1-05-PLAN.md — Varreduras frontend: runs client + MSW handler + PainelVarreduras table view [wave 3]
- [ ] 17.1-07-PLAN.md — Origem modal + Motor depth toggle + TA TTL pill + two-group nav + view-switcher (all 6 views) [wave 4]

---

### Phase 14: Coordless attraction geo-resolution via OpenStreetMap Nominatim (close Phase-13 quarantine gap)

**Goal:** Close the Phase-13 carry-forward gap (see 13-VERIFICATION.md / 13-03-SUMMARY.md): AttractionsFusion listing cards carry **no lat/lng**, and `resolve_municipio` fuzzy-matches the *attraction name* against IBGE município names — so real attraction names ("Cachoeira do Tabuleiro", "Instituto Inhotim") miss and land in `ibge_unmatched` quarantine, keeping a real sweep's Nascente near 0 despite a correct fetch. **Spike-validated 2026-06-25** (`scripts/spike_nominatim_geo.py`, 10 real BR attractions): forward-geocoding the attraction name + UF through **OpenStreetMap Nominatim** (free, no API key, not scraping) geocoded **10/10**; reading `address.municipality|city|town|village|county` (`addressdetails=1`) and name-matching to IBGE gave the exact município on 9–10/10; the lone ambiguity (Lençóis Maranhenses) is a multi-município national park. This phase wires a **geo-enrichment seam** into the atrativos lane: before quarantining as `ibge_unmatched`, geocode the card via a typed, mockable Nominatim client → extract the município name (primary) → IBGE name-match within the UF; fall back to haversine on the returned lat/lon with a **relaxed radius** (~50 km — IBGE coords are the município *seat*, so natural attractions sit 15–25 km out). Results cached by `locationId` to respect Nominatim's ≥1 req/s policy. Offline-by-default (respx-mocked, opt-in real); LGPD-safe (only coordinates / OSM place ref persisted — no personal data). Re-run Level 3 to confirm a real MG sweep yields Nascente `entity_type='attraction'` > 0 with municípios resolved (not quarantined).

**Requirements**: TA-14, TA-15 (extends Phase 13 — atrativos geo-resolution correctness)
**Depends on:** Phase 13
**Plans:** 2/2 plans complete

Plans:
- [x] 14-01-PLAN.md — GeocoderClientProtocol + NominatimGeocoderClient (real, httpx, tenacity, cache) + NullGeocoderClient + NominatimConfig (BRAVE_NOMINATIM_*) + FakeGeocoderClient + TA-14 unit tests (TA-14)
- [x] 14-02-PLAN.md — atrativos.py async _ingest_one + geo-enrichment before ibge_unmatched quarantine + regression/both-fail/no-geocoder tests + Level-3 operator checkpoint (TA-15)

**Locked decisions (see 14-CONTEXT.md — spike-validated 2026-06-25):** geocoder = **OpenStreetMap Nominatim public API** (free, no key, HTTP not browser-scraping) — NOT Google Places (avoids per-request billing) and NOT Google-Maps browser scraping (fragile + ToS); primary resolution = `addressdetails=1` → `address.municipality|city|town|village|county` → exact/fuzzy name-match to IBGE within the UF; secondary = haversine on returned lat/lon with relaxed radius (~50 km, calibrated from spike: naturals 15–25 km from seat); `ibge_unmatched` quarantine only after BOTH name-match and geo-enrichment fail; cache geocode results by `locationId` (Redis); rate-limit ≥1 req/s with a custom User-Agent per Nominatim policy; new typed client behind the network boundary (Null + Fake, respx in tests, `RUN_REAL_EXTERNALS` opt-in, never in CI); LGPD = persist only lat/lon + OSM place id, never address PII. Out of scope: self-hosted Nominatim for all-BR bulk scale (documented future op — public instance OK for operator-gated sweeps); destinos geo-resolution (destinos already carry município context); reverse-geocoding a pre-known coordinate (cards have none); the `oa30` multi-page pagination follow-up (separate phase).
