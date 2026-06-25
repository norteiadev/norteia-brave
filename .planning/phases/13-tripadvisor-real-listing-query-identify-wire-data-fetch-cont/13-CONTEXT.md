# Phase 13: TripAdvisor real listing query — identify + wire data-fetch contract — Context

**Gathered:** 2026-06-24
**Status:** Ready for planning
**Source:** GAP-12-A-FINDINGS.md (live-validated contract from Phase 12 Level-3 UAT)

<domain>
## Phase Boundary

Phase 12 shipped the TripAdvisor session-injection **seam** (auth, canary, Redis,
fail-fast — all verified live) but the lane still collects **zero records** because the
client's data-fetch contract was a wrong Phase-11 assumption. Phase 13 wires the lane to
the **real, live-validated** AttractionsFusion listing query so `sweep_tripadvisor`
actually ingests destinations/attractions into Nascente.

IN SCOPE: rewire `client.py` fetch to the real query (qid + variables + response parse);
extend `ta_bootstrap` + session payload to capture `sessionId`; fix the canary to probe
the real listing query; pagination; update the TA-09 runbook; re-run Level 3 to prove
Nascente > 0.

OUT OF SCOPE: 24/7 autonomous TA; residential-proxy automation; managed-browser fetch
(operator-gated posture unchanged — cookie portability via httpx is confirmed for the
graphql/ids XHR). The session-injection seam itself is done (Phase 12) — do not rebuild it.
</domain>

<decisions>
## Implementation Decisions (LOCKED — live-validated 2026-06-24)

### Listing query
- Use **`preRegisteredQueryId: a5cb7fa004b5e4b5`** (AttractionsFusion list) — NOT the
  Phase-11 placeholder, NOT the telemetry/ad/trips ids (`636d0b9184b2fc29`,
  `986742f2dd8b0ec8`, `42bec0ee6ec0bfd1`, `46dcf3e69ea8ba5a`, `25f9ddb1ce629144`).
- Request variables shape (replaces `{locationId, offset, limit}`):
  `{request:{tracking:{screenName:"AttractionsFusion",pageviewUid:<uuid>},
  routeParameters:{geoId:<UF geoId>, contentType:"attraction",
  webVariant:"AttractionsFusion", filters:[{id:"allAttractions",value:["true"]}]},
  updateToken:null}, commerce:{attractionCommerce:{pax:[{ageBand:"ADULT",count:2}]}},
  tracking:{...}, sessionId:<TASID>, unitLength:"MILES", currency:"USD",
  currentGeoPoint:null, mapSurface:false, debug:false, polling:false}`.
- `geoId` = the per-UF geoId from `data/tripadvisor/uf_geoids.json` (the lane is UF-scoped).
- `pageviewUid` = any uuid (telemetry correlation only).

### Response parsing
- Path: `data.Result[0].sections[]`; keep only
  `__typename == "WebPresentation_SingleFlexCardSection"` (skip AdPlaceholder,
  PaginationLinksList, WideCardsCarousel, WebSortDisclaimer). 30 cards/page.
- Per card `section.singleFlexCardContent` (WebPresentation_FlexCard) → TripAdvisorReviewSignals:
  - name = `cardTitle.text`
  - locationId = `cardLink.webRoute.typedParams.detailId` (int)
  - rating = `bubbleRating.rating` (float)
  - review_count = `bubbleRating.reviewCount` (int)
  - category = `primaryInfo.text`
- `totalResults` (cap 10000) is informational only.

### Session payload + bootstrap
- The session injected to Redis MUST carry `session_id` (the TASID cookie value) — the
  listing query requires it in `variables.sessionId`. Add `session_id` to the session
  model + `SessionInjectBody` (optional/derived from cookies if absent).
- `ta_bootstrap` must extract the **listing** qid `a5cb7fa004b5e4b5` and `sessionId`,
  and REJECT telemetry/ad/trips qids (warn the operator they captured the wrong request).

### Canary
- `_run_canary` must probe the **real listing query** (so empty-result detection is
  meaningful) using a single page; preserve the Phase-12 422/503/empty semantics.

### Runbook (TA-09)
- Update `data/tripadvisor/README` + `RUNBOOK-NIVEL3.md`: capture on
  `/Attractions-g<geoId>-Activities-...`, pick the POST whose Response contains
  `WebPresentation_SingleFlexCardSection`.

### Claude's Discretion
- Code structure of the parser; uuid generation; how `sessionId` is threaded (session
  payload vs derived from TASID cookie); test fixture shape (use a trimmed real response).
- Whether `_run_canary` uses destinos or atrativos as the probe.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Live-validated contract (primary source)
- `.planning/phases/13-tripadvisor-real-listing-query-identify-wire-data-fetch-cont/GAP-12-A-FINDINGS.md` — full request/response contract, field map, wiring checklist, live evidence.

### Phase 12 seam (do not rebuild; integrate with)
- `brave/lanes/tripadvisor/client.py` — `fetch_destinations`/`fetch_attractions` to rewire; `SessionMissingError`/`SessionExpiredError`; `_get_session` Redis-only.
- `brave/api/routers/tripadvisor_session.py` — `SessionInjectBody`, `_run_canary`, status endpoint.
- `scripts/ta_bootstrap.py` — cURL parser/injector to extend (sessionId + listing qid).
- `brave/lanes/tripadvisor/destinos.py`, `atrativos.py` — ingest lanes consuming the parsed cards.
- `data/tripadvisor/README`, `data/tripadvisor/RUNBOOK-NIVEL3.md`, `data/tripadvisor/uf_geoids.json`.
- `.planning/phases/12-.../12-HUMAN-UAT.md` — GAP-12-A history.

### Memory
- `tripadvisor-graphql-real-shape` (auto-memory) — the real qid + the mislabeled-spike-qids correction.
</canonical_refs>

<specifics>
## Specific Ideas

- First card validated: "Iguazu Falls", detailId 312332, rating 4.9, reviewCount 45811,
  category "Waterfalls" — use a trimmed copy of this real response as the offline test fixture.
- 30 cards/page; pagination via `WebPresentation_PaginationLinksList` (URL `oa0`→`oa30`).
</specifics>

<deferred>
## Deferred Ideas

- **`most_recent_review_at`**: NOT in the listing card; needs a per-POI detail fetch or
  accept null at Nascente. Decide during planning — prefer null-at-Nascente unless cheap.
- **Destinations (`destinos`) contentType/webVariant**: confirmed shape is for `attraction`;
  the destinations variant may differ — capture a destinations page to confirm, or scope
  Phase 13 to attractions first and follow up for destinos.
- **Pagination param**: confirm whether paging is a `routeParameters` offset field or via
  following PaginationLinksList route params (capture an `oa30` request).
</deferred>
