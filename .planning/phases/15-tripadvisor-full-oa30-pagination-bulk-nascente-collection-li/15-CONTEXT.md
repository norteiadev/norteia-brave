# Phase 15: TripAdvisor full oa30 pagination + bulk Nascente collection + live sweep dashboard panel - Context

**Gathered:** 2026-06-26
**Status:** Ready for planning
**Source:** Direct capture (operator session — pagination mechanism reverse-engineered + live-validated this session)

<domain>
## Phase Boundary

Close the multi-page pagination follow-up that Phase 13 explicitly deferred. Phase 13 wired the
single-page AttractionsFusion listing query (qid `a5cb7fa004b5e4b5`) and proved 30 real
attractions/page reach Nascente. This phase makes the lane collect **all ~10,000 Brazil
attractions** (geoId 294280) by paginating, and gives the operator a **live visual progress
panel** on the dashboard while the sweep runs.

In scope:
- A paginating fetch over the 334 pages of the all-Brazil AttractionsFusion listing.
- Bulk ingest of every page's 30 cards through the existing Nascente path (`_ingest_one` →
  store_raw + §7.6 score), reusing `_parse_attractions_page`.
- A Redis-backed live progress state + a FastAPI status endpoint + a new Next.js dashboard panel
  that polls it.
- Slice-first validation (run a small page range end-to-end first), then scale to full 334.
- Resume-from-offset so a mid-run DataDome/session expiry does not force a restart from page 1.

Out of scope:
- Per-UF pagination (this phase is whole-Brazil g294280 only).
- Destinos-lane pagination.
- Autonomous 24/7 TA beat (stays operator-gated, best-effort — matches Phase 12 posture).
- Residential-proxy automation.
</domain>

<decisions>
## Implementation Decisions

### Pagination mechanism (discovered + live-validated 2026-06-26)
- Offset is **path-based, NOT a GraphQL variable.** The `a5cb7fa004b5e4b5` persisted query
  **rejects** any offset/oa/page/skip field added to `request.routeParameters` — TripAdvisor
  returns `{"message": "Variable \"$request\" got invalid value."}` and an empty `data`. The
  routeParameters input type has a fixed field set (geoId, contentType, webVariant, filters).
- `totalResults: 10000` (TA hard display cap; the all-attractions filter count is 10347).
  `limit: 30` per page → **334 pages** (oa0 … oa9990).
- The page offset lives in the URL **path segment** `-oa{N}-` where `N = pageIndex * 30`
  (page 1 = no segment / oa0, page 2 = oa30, … page 334 = oa9990). This is sourced from the
  response's `WebPresentation_PaginationLinksList.links[].webRoute.webLinkUrl`
  (`/ClientLink?value=<base64>` decodes to `Attractions-g294280-...-oa30-Brazil.html`).

### Transport — HTML SSR extract (LOCKED)
- Fetch each page via `GET https://www.tripadvisor.com/Attractions-g{geoId}-Activities-a_allAttractions.true-oa{N}-Brazil.html`
  with the **full operator cookie jar** (datadome + TA session cookies) and the captured UA.
- Returns HTTP 200, ~1.5 MB HTML, embedding the **same 30 FlexCard `sections[]`** as the
  GraphQL response (verified: `cardTitle`×30, `reviewCount`×30, `bubbleRating`). The embedded
  JSON must be extracted from the HTML and fed to the **existing `_parse_attractions_page`**.
- This contradicts the older "HTML navigation → 403" finding (memory `tripadvisor-graphql-real-shape`):
  that was a plain GET. **With the captured datadome+session jar the HTML page is NOT walled.**
- Distinctness proven: oa0 vs oa30 d-id sets overlap by only 6 (sponsored/ad cards repeat);
  the 30 organic cards differ per page.
- Rationale for HTML over GraphQL: the GraphQL listing query cannot paginate (offset rejected);
  reverse-engineering the real next-page persisted query would need another browser capture not
  available. HTML SSR is the proven, available transport and reuses the existing parser.

### Run scope — slice-first (LOCKED)
- Validate a **small slice (~5–10 pages / 150–300 attractions)** end-to-end into Nascente FIRST.
  This proves DataDome endurance over sequential requests, the throttle, resume, and the
  dashboard panel before committing the full 334-page run.
- Then scale to the full 334 pages.
- The fetch MUST be parameterized by a page range / max-pages so the slice and the full run use
  the same code path (no separate "test" code).
- Throttle between page requests (politeness + DataDome endurance); exact delay is Claude's
  discretion but must be configurable.
- **Resume-from-offset:** persist the last successfully-ingested offset so a re-run continues
  from there rather than re-fetching page 1. Mid-run `SessionExpiredError` (403/429) must stop
  cleanly and record where it stopped (consistent with Phase 12 fail-fast `needs_bootstrap`).

### Dashboard — NEW live progress panel (LOCKED)
- The sweep writes live progress to a **Redis key** (e.g. `brave:ta:sweep:progress`): pages
  done / 334, attractions ingested, current offset, error count, start time / rate.
- A **FastAPI status endpoint** (bearer/steward auth, consistent with the existing TA session
  endpoints) exposes that progress as JSON.
- A **new Next.js dashboard panel** polls the endpoint and renders live: progress bar
  (pages/334), attractions ingested, current offset, errors, rate, and terminal state
  (running / done / stopped-needs-bootstrap). Mirror existing Brave-monitor panel patterns,
  Bearer-header auth, MSW + Vitest coverage.

### Reuse / boundaries (LOCKED)
- Reuse `_parse_attractions_page` (no new parser) and `_ingest_one` / the existing Nascente
  ingest + §7.6 path (no new scoring).
- Honor the existing engine depth gating and operator-gated posture; the TA sweep never
  auto-promotes to Mar and TA attractions never enter WhatsApp (Phase 11 locked decisions).
- LGPD: only aggregate review fields (review_count, rating) — never author/text (unchanged).
- Testing stays 100% offline by default: no test hits TripAdvisor unless `RUN_REAL_EXTERNALS=1`;
  the HTML transport must be mockable (respx / fake client), like the existing client.

### Blocker resolutions (operator-decided 2026-06-26, after research)
- **National-UF / parent-destino gate (BLOCKING, resolved):** `_ingest_one` today requires a
  `uf` + a parent destino in `destino_rio_map` and quarantines BEFORE `store_raw` — a whole-Brazil
  attraction-only run would write 0 Nascente + ~10k quarantine. **Resolution: add a bulk-lane
  ingest path that BYPASSES the parent-destino requirement and derives `uf` + município from the
  attraction's geocoded IBGE code** (reuse the existing Nominatim + IBGE resolver). Attractions
  land in Nascente fully município-resolved, no parent destino required. The existing per-UF
  `_ingest_one` parent-linkage path must remain intact for the destinos-driven lane — the bulk
  lane is a distinct path, not a mutation of the old contract.
- **Full-run geocoding cost (resolved): slice-first.** Build + validate the ~5–10 page slice
  end-to-end NOW (geocoding 150–300 attractions = minutes). **Defer the full 334-page run AND its
  ~3h geocode-resilience strategy (batching/caching/parallelism) to a follow-up after the slice
  proves out.** Do not over-engineer full-run geocoding before the slice validates the transport,
  session endurance, resume, and dashboard. The fetch/ingest code path must still be the same for
  slice and full run (parameterized by page range), but the full national run is a later trigger.

### Resume / TTL note (from research)
- The sweep currently commits once at the end (`pipeline.py`) — a mid-run 403 would roll back
  everything. Bulk lane MUST commit per-page (or per small batch) and persist the last completed
  offset so a re-run resumes. Session TTL (30 min) < full run (3h+), so resume across operator
  re-injections is the happy path for the full run; the slice fits within one session.

### Claude's Discretion
- Exact Redis progress key name + schema, throttle delay default, batch/commit cadence to
  Nascente, the embedded-JSON extraction technique from the HTML (regex vs script-tag parse),
  and the precise dashboard panel layout — provided they satisfy the locked decisions above.
- Whether pagination lives as a new `fetch_attractions_paginated` / a `page_range` arg on the
  client vs a thin loop in the sweep task — provided the slice and full run share one code path
  and the single-page `fetch_attractions` contract (Phase 13) is not silently broken.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### TripAdvisor lane (transport + parse + ingest)
- `brave/lanes/tripadvisor/client.py` — `TripAdvisorClient`, `fetch_attractions` (single-page
  contract + WR-02 `max_pages>1` NotImplementedError), `_parse_attractions_page`, `_get_session`,
  `SessionExpiredError` / `SessionMissingError`.
- `brave/lanes/tripadvisor/atrativos.py` — `TripAdvisorAtrativosIngest.produce` / `_ingest_one`
  (Nascente payload, §7.6 origem, IBGE municipio resolution, parent destino linkage).
- `brave/tasks/pipeline.py` — `sweep_tripadvisor` (fail-fast, `needs_bootstrap`,
  `run_real_externals` Null vs real client branch).

### Session + config + dashboard wiring
- `brave/api/routers/tripadvisor_session.py` — session injection + `_run_canary` + status
  endpoint patterns (auth, Pydantic, Redis) — the new progress status endpoint should mirror these.
- `brave/config/settings.py` — `TripAdvisorConfig` (env prefix `BRAVE_TA_`), `AppConfig.run_real_externals`.
- Dashboard: the existing Brave-monitor / EngineControl panels (Next.js + Bearer auth + MSW/Vitest)
  — locate the closest analog panel + its API client + its MSW handlers to mirror.

### Roadmap + prior phases
- `.planning/ROADMAP.md` Phase 15 entry (this phase), Phase 13 (single-page query wired),
  Phase 12 (session-injection seam — do not rebuild), Phase 11 (lane + scoring + locked decisions).
- Phase 13 `GAP-12-A-FINDINGS.md` — the live-validated listing contract.
</canonical_refs>

<specifics>
## Specific Ideas

- All-Brazil geoId = **294280**. Page URL template:
  `https://www.tripadvisor.com/Attractions-g294280-Activities-a_allAttractions.true-oa{N}-Brazil.html`
  (page 1 may omit the `-oa0-` segment; oa0 also works).
- Offset formula: `oa = (page - 1) * 30`; pages 1..334; last offset 9990.
- Embedded card markers present in the HTML: `cardTitle`, `bubbleRating`, `reviewCount`,
  `WebPresentation_SingleFlexCardSection` — same shape `_parse_attractions_page` already consumes.
- Validation harnesses from this session live in the scratchpad (`ta_real_test.py`,
  `ta_probe_pagination.py`, `ta_probe_html.py`) — reference only; production code goes in the lane.
- Requirement: extends **TA-12** (data-fetch correctness; Phases 12/13).
</specifics>

<deferred>
## Deferred Ideas

- Per-UF attraction pagination (state-scoped geoIds).
- Destinos-lane pagination.
- Autonomous 24/7 TA scheduling on the beat.
- Residential-proxy automation / managed-browser session refresh.
- Going past the TA 10,000 display cap (TA itself does not paginate beyond oa9990).
</deferred>

---

*Phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li*
*Context gathered: 2026-06-26 via direct operator-session capture*
