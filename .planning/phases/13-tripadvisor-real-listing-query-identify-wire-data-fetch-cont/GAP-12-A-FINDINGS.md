# GAP-12-A — Real TripAdvisor listing contract (CHARACTERIZED, 2026-06-24)

The organic per-UF attractions/destinations listing query was identified and
validated live with a real operator session. This de-risks Phase 13 to a wiring
task — the contract below is confirmed against HTTP 200 + 314 KB of real data.

## The listing query

- **Endpoint:** `POST https://www.tripadvisor.com/data/graphql/ids` (batch array)
- **preRegisteredQueryId:** `a5cb7fa004b5e4b5`  (operation: AttractionsFusion list)
- **NOT** the ids previously assumed/recorded — those were telemetry/ads/trips:
  - `636d0b9184b2fc29` = telemetry (user_navigated / page_viewed)
  - `986742f2dd8b0ec8` = pixel metrics
  - `42bec0ee6ec0bfd1` = a locationId-keyed aux query
  - `46dcf3e69ea8ba5a` = ad_mission_control.GetPageSlotSettings (ads)
  - `25f9ddb1ce629144` = Trips_ReferenceInput (saves)

## Request variables (the shape client.py must build — NOT {locationId,offset,limit})

```json
{
  "variables": {
    "request": {
      "tracking": {"screenName": "AttractionsFusion", "pageviewUid": "<uuid>"},
      "routeParameters": {
        "geoId": 294280,                       // UF geoId (294280 = Brazil national; use per-UF geoId from uf_geoids.json)
        "contentType": "attraction",           // "attraction" for atrativos
        "webVariant": "AttractionsFusion",
        "filters": [{"id": "allAttractions", "value": ["true"]}]
      },
      "updateToken": null
    },
    "commerce": {"attractionCommerce": {"pax": [{"ageBand": "ADULT", "count": 2}]}},
    "tracking": {"screenName": "AttractionsFusion", "pageviewUid": "<uuid>"},
    "sessionId": "<TASID cookie value, e.g. E75FBE95...>",
    "unitLength": "MILES", "currency": "USD",
    "currentGeoPoint": null, "mapSurface": false, "debug": false, "polling": false
  },
  "extensions": {"preRegisteredQueryId": "a5cb7fa004b5e4b5"}
}
```

Notes:
- `sessionId` mirrors the `TASID` cookie — the session-injection payload must carry it
  (today's `ta_bootstrap` does not capture `sessionId` separately; add it).
- `pageviewUid` can be any uuid (telemetry correlation only).
- Pagination is NOT offset/limit. The response carries a
  `WebPresentation_PaginationLinksList` section; the page URL pattern is
  `/Attractions-g<geoId>-Activities-oa0-...` → `oa30`, `oa60`. Phase 13 must determine
  whether to page via a `routeParameters` page/offset field or by following the
  pagination links' route params (capture an `oa30` request to confirm).

## Response path + field mapping (confirmed)

`data.Result[0]` = `WebPresentation_QueryAppListWebResponse`
  - `.totalResults` → 10000 (cap; real per-UF counts lower)
  - `.sections[]` (41 for Brazil page 1): mix of
    - `WebPresentation_SingleFlexCardSection` ×30 ← **the POI cards** (page size = 30)
    - `WebPresentation_AdPlaceholder` ×8 (skip)
    - `WebPresentation_PaginationLinksList` ×1 (pagination)
    - `WebPresentation_WideCardsCarousel` ×1, `WebPresentation_WebSortDisclaimer` ×1 (skip)

Per card: `section.singleFlexCardContent` (`WebPresentation_FlexCard`):

| Lane field (TripAdvisorReviewSignals) | Path |
|---|---|
| name | `cardTitle.text` |
| locationId | `cardLink.webRoute.typedParams.detailId` (int; also in `webLinkUrl` `-d<id>-`) |
| rating | `bubbleRating.rating` (float) |
| review_count | `bubbleRating.reviewCount` (int) |
| review_count (display) | `bubbleRating.numberReviews.text` ("45,811") |
| category | `primaryInfo.text` ("Waterfalls") |
| rank | `ordinalPrefix` ("1.") |
| photo | `cardPhoto.sizes.urlTemplate` |
| description | `descriptiveText.text.text` |

`most_recent_review_at` is NOT in the listing card — needs either a per-POI detail
fetch (extra query) or accept null at Nascente. Decide in Phase 13.

## Live validation evidence

`POST graphql/ids` with the captured cookies + qid `a5cb7fa004b5e4b5` + the request
above → **HTTP 200, 314 KB**, `listType:"POI"`, `totalResults:10000`, 30 FlexCard
sections. First card: "Iguazu Falls", detailId 312332, rating 4.9, reviewCount 45811,
category "Waterfalls". Cookie portability (httpx, same IP) reconfirmed.

## Phase 13 wiring checklist

1. `client.py`: replace `{locationId,offset,limit}` + bogus qid with the
   `request.routeParameters{geoId,contentType,webVariant,filters}` + `sessionId`
   shape and qid `a5cb7fa004b5e4b5`; parse `data.Result[0].sections[]`
   filtered to `WebPresentation_SingleFlexCardSection`. Determine `contentType`
   for destinos vs atrativos (attraction confirmed; geos/destinations may use a
   different `contentType`/`webVariant` — capture a destinations page to confirm).
2. Pagination: capture an `oa30` request; wire the real paging param.
3. `ta_bootstrap`: also capture `sessionId` (TASID) and the LISTING qid
   `a5cb7fa004b5e4b5` specifically (reject telemetry/ad qids).
4. Session model + Redis payload: add `session_id` field; `_run_canary` must use the
   real listing query so empty-result detection is meaningful.
5. TA-09 runbook: tell operators to capture on `/Attractions-g<geoId>-Activities-...`
   and pick the POST whose Response has `WebPresentation_SingleFlexCardSection`.
6. Re-run Level 3 → confirm Nascente records > 0.
