# Quick Task 260629-rmz: Fix TripAdvisor lane geo-targeting + atrativo→destino linkage — Context

**Gathered:** 2026-06-29
**Status:** Spike-first — implementation plan deferred until spike resolves unknowns

<domain>
## Task Boundary

The per-UF TripAdvisor sweep collects wrong/zero data. Live test (UF=AC) proved 3 UF-agnostic
bugs (see Specifics). Fix geo-targeting (get UF-scoped attractions), atrativo→município linkage
(listing card has no município), and the empty destinos query — so a per-UF TA sweep produces
real UF destinos in Rio + atrativos linked to their parent destino.

Out of scope: the bulk_national path, the session-refresh feature (done in 260629-p2v), and the
Painel source routing (done in 260629-qny).
</domain>

<decisions>
## Implementation Decisions (LOCKED via --discuss)

### Approach — SPIKE-FIRST
- The geoId-scoping mechanism is a genuine technical unknown. Do a spike with the REAL session to
  validate transport + linkage + the destinos-empty cause against live data BEFORE writing the
  implementation plan. The implementation plan is authored only after the spike confirms the
  mechanics. Spike findings recorded in `260629-rmz-SPIKE.md`.

### Transport for UF-scoped attractions — HTML-SSR per-UF
- Use the HTML SSR page `Attractions-g{geoId}-...` (geoId in the URL path), already proven to scope
  by geo in Phase 15 (whole-Brazil oa30 sweep; memory [[tripadvisor-graphql-real-shape]]). Reuse
  `_parse_attractions_page` + path-based `-oa{N}-` pagination. The GraphQL AttractionsFusion query
  (qid a5cb7fa004b5e4b5) does NOT scope by the routeParameters.geoId we send — it returns national
  popular attractions. Spike must confirm the per-UF HTML page returns UF attractions (not national).

### Atrativo→município linkage — detail-parents per attraction
- For each attraction, call the DETAIL query (preRegisteredQueryId `444040f131735091`,
  variables{locationId}) → `parents[]` geo hierarchy; `parents[0]` is the city geoId. Map city
  geoId → IBGE município. Accepts +1 request per attraction (cost/DataDome exposure at scale — the
  spike should measure/throttle). Spike must confirm: parents[0] is reliably the city, and that a
  city-geoId→IBGE mapping is feasible (build a map, or fuzzy-match the parent city NAME to IBGE).

### Destinos — include the fix
- Investigate + fix why `fetch_destinations(uf)` returns 0 (AC) in this task. Atrativos depend on
  the parent destino being present in Rio (`destino_rio_map` keyed by IBGE), so destinos must work.
  Spike captures the raw destinos response for AC (and a denser UF) to find the cause.

### Parser null-safety — include
- Fix `_parse_attractions_page` null-unsafe `.get(k, {}).get(...)` (client.py:172-181): when
  `bubbleRating`/`cardTitle`/`primaryInfo` is present-but-null (review-less attractions), the chain
  raises AttributeError and the card is dropped (`ta_parse_skip_malformed_card`). Use
  `(card.get(k) or {})` style. Small, include in implementation.

### Testing
- Offline by default (RUN_REAL_EXTERNALS unset); real TA opt-in only. New parsing/linkage logic
  unit-tested with captured fixtures (scrubbed of cookies/PII). Spike uses the real session.
</decisions>

<specifics>
## Live evidence (2026-06-29, UF=AC sweep + live captures)

- **Bug 1 — listing not UF-scoped:** `fetch_attractions(geo_id=303509 [AC])` via AttractionsFusion
  (qid a5cb7fa004b5e4b5) returned NATIONAL popular attractions ("Parque Nacional da Serra dos
  Órgãos"/RJ, 2145 reviews), not Acre. The persisted query ignores the geoId we pass.
  (client.py:401-532; uf_geoids.json AC=303509, sequential state geoIds 30350x–30353x.)
- **Bug 2 — destinos empty:** `fetch_destinations("AC")` → 0 locations (client.py:309-399).
- **Bug 3 — card lacks município/coords:** `_parse_attractions_page` emits only
  name/locationId/rating/review_count/category (client.py:147-200). No lat/lng/município. So
  `resolve_municipio` is fed the attraction NAME (atrativos.py:174), fuzzy-misses vs city names,
  haversine fallback skipped (no coords), Nominatim misses → 29/29 `ibge_unmatched`
  (atrativos.py:214-222, ibge.py:112-172).
- **Linkage path exists (detail):** live capture of qid `444040f131735091` (variables{locationId})
  for locationId 312332 (Iguazu) → `{locations:[{locationId:312332, parents:[{303444 Foz do
  Iguaçu},{303435 Paraná},{294280 Brasil},{13 S.America}]}]}`. parents[0]=city geoId. Detail page
  also carries lat/lng (Mapbox).
- **Parser AttributeError:** `ta_parse_skip_malformed_card error=AttributeError` fired live on
  review-less cards (bubbleRating null) — client.py:179-180.
- **Session refresh validated alongside:** `ta_session_writeback rotated_cookie_count=2`, TTL slid
  up — the p2v feature works; not part of this task.

## Files
- brave/lanes/tripadvisor/client.py (fetch_attractions, fetch_destinations, _parse_attractions_page,
  fetch_attractions_paginated/_TA_HTML_URL), destinos.py, atrativos.py, ibge.py, geo.py,
  data/tripadvisor/uf_geoids.json. Possibly a new city-geoId→IBGE mapping artifact.
</specifics>

<canonical_refs>
## Canonical References
- Memory [[tripadvisor-graphql-real-shape]] — HTML SSR oa30 page scopes by geoId, path-based pagination.
- Phase 13 GAP-12-A findings (AttractionsFusion qid identification), Phase 15 (oa30 whole-Brazil).
- Test rules: offline default, RUN_REAL_EXTERNALS opt-in; backend `.venv/bin/python -m pytest`.
</canonical_refs>
