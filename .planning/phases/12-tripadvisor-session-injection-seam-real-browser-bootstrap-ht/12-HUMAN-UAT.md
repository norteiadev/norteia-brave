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

**Next action (follow-up phase / investigation):**
1. Determine HOW TripAdvisor serves the destinations/attractions LIST for a UF:
   - capture a graphql/ids POST during **pagination** on the dedicated Attractions
     page (`/Attractions-g<geoId>-Activities-<State>.html` → scroll / "ver mais"),
     inspecting the Response tab for an actual `locations`/`attractions` array; OR
   - if listings are SSR-only, switch the lane to HTML parsing or the correct
     data endpoint instead of the assumed graphql/ids persisted query.
2. Correct `client.py` queryId source + variable shape + response-path parsing to
   match the real contract; update `ta_bootstrap` to extract the LISTING queryId
   (reject telemetry queryIds) and the TA-09 runbook to point at the right request.
3. Re-run Level 3 to confirm Nascente records > 0.
