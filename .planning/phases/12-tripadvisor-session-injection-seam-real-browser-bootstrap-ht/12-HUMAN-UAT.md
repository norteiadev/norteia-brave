---
status: partial
phase: 12-tripadvisor-session-injection-seam-real-browser-bootstrap-ht
source: [12-VERIFICATION.md]
started: 2026-06-24T00:00:00Z
updated: 2026-06-24T00:00:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Real cURL parsing (ta_bootstrap end-to-end)
expected: Pasting an actual DevTools "Copy as cURL" string into `scripts/ta_bootstrap.py`
  extracts cookies + `extensions.preRegisteredQueryId` correctly and POSTs to
  `POST /api/v1/tripadvisor/session` without printing cookie values.
result: [pending]

### 2. Live canary validation (three branches)
expected: Inject a real DataDome session captured from a residential-IP browser.
  Confirm 200 `ready` on a valid session; 422 `invalid_session` on an expired
  session (key deleted); 503 `canary_unverified` on an infra fault (key preserved).
result: [pending]

### 3. Sweep operability (one UF, real externals)
expected: With `RUN_REAL_EXTERNALS=1` run one UF; confirm the EngineControl session
  pill transitions (Pronta / Precisa bootstrap / Expirada) and non-zero Nascente
  records are ingested (no silent 0-record retry-storm).
result: [pending]

## Summary

total: 3
passed: 1
issues: 1
pending: 0
skipped: 0
blocked: 1

## Gaps

### GAP-12-A: Real GraphQL listing contract unverified — graphql/ids on Tourism pages is telemetry-only
status: failed
severity: blocking-for-data-collection
discovered: 2026-06-24 (Level 3 live UAT)

**What passed:** Cookie/DataDome portability is CONFIRMED — operator-captured
DataDome + TA session cookies replay through the worker's httpx and return
**HTTP 200** on the same residential IP (validates the spike premise and the
whole session-injection seam). Auth gate, 64KB guard, Redis write, canary gate
(incl. empty-result deletion), and sweep fail-fast all verified live.

**What failed:** Two separate operator captures from a TripAdvisor Tourism state
page (`/Tourism-g303380-Minas_Gerais_State.html`) both yielded **telemetry**
persisted queries, not location listings:
  - capture 1: `qid=636d0b9184b2fc29`, variables `{events:[page_viewed__2,...]}`
  - capture 2: `qid=d3d4987463b78a39`, variables `{locationId, eventType:PAGEVIEW, isGeoPage}`
    → verbatim replay returned `data.gtmData` (GTM/audience tracking), 200 OK.

The `client.py` data-fetch path assumes a persisted query taking
`{locationId, offset, limit}` and returning `data.locations` / `data.attractions`.
That contract (queryId + variable shape + response path) was never captured from a
real TripAdvisor data response — on Tourism overview pages the listing data is
SSR-rendered in the initial HTML and graphql/ids carries only analytics/GTM beacons.
The canary's empty-result guard correctly rejected the telemetry queryId
(`invalid_session`), so no bad session persisted — but the lane cannot actually
collect destination/attraction records until the real listing query is characterized.

**Capture 3 (Attractions page `/Attractions-g303380-Activities-Minas_Gerais.html`):**
`qid=343a07f958a70310`, variables `{request:[{numberOfContents:12,
randomContentWithGeoInput:{locationIds:[303380,303370]}}]}`. Verbatim replay →
**200 OK, 12 KB**, `data.response[0].randomContentList[12]` — but every item is
`contentType:"BRANDED"` SPONSORED content (campaignId/sponsorId, e.g. "Universal
Orlando Resort", geoId 24971875 in Florida), NOT organic Minas Gerais attractions.

**Key technical finding — cookie portability is XHR-only:**
- `graphql/ids` XHR POST with the injected cookies → **200 OK** (telemetry, GTM,
  and sponsored-ad queries all return data).
- A plain **GET of the listing HTML document** (`/Attractions-g...html`) with the
  same cookies → **403** (DataDome challenge page, 775 bytes).

So httpx can replay TripAdvisor's XHR APIs but NOT the HTML document navigation,
and across 3 operator captures the only graphql/ids XHR queries surfaced were
telemetry + GTM + sponsored ads. The organic per-UF attraction/destination listing
appears to be SSR-rendered in the (DataDome-403'd) HTML — meaning neither the
assumed `{locationId,offset,limit}` persisted query NOR httpx HTML scraping reaches
it. The session-injection seam is sound; the data-source assumption (Phase 11) is not.

**Next action (follow-up phase — scoped investigation, NOT a quick fix):**
1. Determine the real organic-listing data path:
   - hunt for an organic-listing graphql/ids XHR query (try filtering/sorting/"see
     all" interactions on the Attractions page that may fire an XHR list fetch with
     `offset`/`limit` and return organic `attractions`); OR
   - confirm listings are SSR-only → then httpx cannot reach them under DataDome,
     and the lane needs a managed-browser/residential-proxy fetch (which the office-
     hours design explicitly deferred as out-of-scope), or a different TA surface.
2. Once the real contract is known: fix `client.py` (queryId + variable shape +
   response path), make `ta_bootstrap` reject telemetry/ad queryIds, fix the TA-09
   runbook to point operators at the correct request.
3. Re-run Level 3 to confirm Nascente records > 0.

This likely intersects the deferred "autonomous 24/7 TA needs paid/licensed source
or managed browser+proxy" decision — the data-fetch half may be harder than the
session seam assumed.
