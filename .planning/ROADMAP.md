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
**Plans:** 3 plans

Plans:
- [ ] 13-01-PLAN.md — Rewire fetch_attractions to AttractionsFusion query (qid a5cb7fa004b5e4b5 + sections[] parse) + session_id model + ta_bootstrap TASID/qid-reject (TA-12)
- [ ] 13-02-PLAN.md — _run_canary probes real listing query + atrativos._ingest_one card-field mapping (most_recent_review_at=None) (TA-12)
- [ ] 13-03-PLAN.md — Update README + RUNBOOK-NIVEL3 capture instructions + operator Level-3 checkpoint (TA-12)

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
