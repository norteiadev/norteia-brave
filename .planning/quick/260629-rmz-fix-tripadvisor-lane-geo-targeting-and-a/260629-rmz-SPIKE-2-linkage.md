# 260629-rmz SPIKE-2 — atrativo→município linkage, live findings (2026-06-30)

Live probing of the TA detail/listing surface with a real session (operator-supplied
cURLs). Goal: nail how a TA attraction resolves to its IBGE município (for the
atrativo→destino vínculo). **No production code changed** — scope decision deferred
(operator chose "synthesize only").

## Decisive reframing

**The destino/município catalog is already solved, offline, and IBGE-authoritative.**
- The IBGE *Localidades* API (`servicodados.ibge.gov.br/api/v1/localidades`) is the same
  data already vendored at `data/ibge/ibge_municipios.csv` (5570 rows:
  `ibge_code, nome, uf, lat, lng`). Loaded as `ibge_records`; `resolve_municipio`
  already does name-match + haversine fallback.
- `MturSeedIngest` seeds **every** município into Nascente at **origem=100**
  (authoritative). So the destino universe needs **no TripAdvisor**.
- ⇒ **Bug 2 (TA `fetch_destinations`=0) is NON-BLOCKING.** TA destinos was only secondary
  corroboration. The atrativo's parent destino comes from the Mtur/IBGE destino, keyed by
  IBGE code in `destino_rio_map`.

**TA's real job = atrativos + linking each to an IBGE município.** The hard part is
getting each attraction's município. Verified live:

| Source | Carries município? | Notes |
|--------|-------------------|-------|
| Listing FlexCard (`_parse_attractions_page`) | ❌ | `cardLink.webRoute.typedParams` has ONLY `detailId` — no geoId, no city. |
| graphql `444040f131735091` (`{locationId}`) | ⚠️ geoId only | Returns `parents[]` = **bare geoIds, NO `localizedName`**. `parents[0]` = parent **city** geoId (Foz=303444). **The shipped rmz code reads `parents[0].localizedName`, which does NOT exist in live data → linkage silently fails.** |
| Detail HTML `Attraction_Review-g{cityGeoId}-d{locId}.html` | ✅ authoritative | Slugless form **200-redirects** to canonical (follow_redirects). Embedded JSON-LD `LocalBusiness`: `name`, `address.addressLocality` (**município**), `address.addressRegion` (state), `geo.latitude/longitude` (**coords**), `aggregateRating`. `-d{id}` **alone → 403** (needs cityGeoId in URL). |
| graphql `daebddd2c711c5fb` (`{geoId, shelfRequests:[BrowseByCategory]}`) | ✅ coords + name-via-slug | `Content__location.detail.info.{latitude,longitude}` = geo center; `shelfItems[].filteredPageUrl.relativeUrl` carries the city slug (`...Foz_do_Iguacu_State_of_Parana.html`). A GraphQL **geoId→{coords,name}** resolver (alternative to the HTML fetch). |

## What is BLOCKED (why full "sweep-by-city" / option c is not reachable now)

- **Legacy `TypeAheadJson`** (name→geoId) is **DataDome-blocked now**, even with the session
  jar (returns the `geo.captcha-delivery.com` interstitial). The
  `scripts/ta_discover_state_geoids.py` "no auth required" premise is **stale**.
- **State Tourism page** (`Tourism-g{stateGeoId}-...`) lists only ~10 **popular** child
  cities (geoId + name slug) — not a complete per-UF catalog.
- The **state→child-cities enumeration** query (the clean source for a full city catalog
  AND the real destinos QID) is **not present in any captured cURL** — it lives only on the
  destinations-page XHR. Needs a fresh DevTools capture (or `/browse`) to obtain its
  `preRegisteredQueryId`.

## Reachable design (same end result as option c, without the blocked pieces)

Per-attraction, amortized by a city cache:

```
listing card → locationId
  → graphql 444040f131735091 → parents[0] = cityGeoId
  → detail HTML g{cityGeoId}-d{locId}.html → JSON-LD {municipio_name, region, lat, lng}
     (or daebddd2c711c5fb for coords + city slug name)
  → resolve_municipio(municipio_name, uf, lat, lng) → IBGE município
  → destino_rio_map[ibge_code] → Mtur destino (origem=100)
cache cityGeoId → município   ⇒ the HTML GET is ~1 per DISTINCT city, not per attraction
```

- Coords now arrive for free → improves `completude` + the haversine match.
- Fixes the real rmz bug (the non-existent `parents[0].localizedName` read).
- `_DESTINATIONS_QID` stays `None`; TA destinos can be dropped/deferred (Mtur covers it).

## ✅ VALIDATED UPGRADE (2026-06-30, fresh session) — single-query name linkage

The two-hop (graphql-parents → detail HTML) is **superseded**. A single GraphQL query
returns the attraction's parent município NAME directly:

**`d3d4987463b78a39`** — variables `{locationId, eventType:"PAGEVIEW", isGeoPage:true}`
→ `data.gtmData.locationData`:

```json
{"cityName":"Foz do Iguacu","stateName":"State of Parana","stateId":303435,
 "countryName":"Brazil","countryId":294280,
 "locationHierarchy":":312332:1:13:294280:303435:303444:"}
```

- `cityName` = parent **município** (ASCII-folded, e.g. "Foz do Iguacu") → match IBGE by name
  (the IBGE CSV has the accented "Foz do Iguaçu"; `resolve_municipio` accent-folds).
- `stateName` = "State of {X}" → derive UF (strip prefix, or reverse-map `stateId` via the
  corrected `uf_geoids.json`). `stateId` = TA state geoId.
- `locationHierarchy` last id before the trailing `:` = parent **city geoId** (303444).
- **One request per attraction, GraphQL** (no HTML-surface DataDome exposure, no parents hop).
  Cacheable by `cityId` → município.

Validated across 4 attractions in 2 cities (fresh session):

| locationId | attraction | cityName | cityId |
|-----------|-----------|----------|--------|
| 312332 | Cataratas do Iguaçu | Foz do Iguacu | 303444 |
| 737277 | Parque das Aves | Foz do Iguacu | 303444 |
| 318113 | Itaipu | Foz do Iguacu | 303444 |
| 1493739 | Jardim Botânico | Curitiba | 303441 |
| 553398 | Parque Barigui | Curitiba | 303441 |

**Linkage chain (by name, per operator decision destino←atrativo←município←UF):**

```
listing card → locationId
  → graphql d3d4987463b78a39 → {cityName, stateName, cityId}
  → resolve_municipio(cityName, uf(stateName)) → IBGE município (name-fold match)
  → destino_rio_map[ibge_code] → Mtur destino (origem=100)
```

Coords: NOT in this query (link is name-based). If coords wanted later:
`daebddd2c711c5fb {geoId:cityId, shelfRequests:[BrowseByCategory]}` →
`Content__location.detail.info.{latitude,longitude}` (city center), or the detail-HTML
JSON-LD `geo` for attraction-precise coords.

Re-confirmed unchanged this session: `444040f131735091` still returns parents = bare geoIds
(no names); `7e78ac4bbce2f255` still only `placeType`. So the rmz code's
`parents[0].localizedName` is still broken — replace it with `d3d4987463b78a39`.

## Net recommendation (for whenever scope is reopened)

1. Re-implement `fetch_attraction_detail` (or add `fetch_attraction_geo`) to call the
   **single** query `d3d4987463b78a39` `{locationId, eventType:"PAGEVIEW", isGeoPage:true}`
   → return `{location_id, city_name, state_name, city_geo_id, state_geo_id}` from
   `data.gtmData.locationData`. (Replaces the broken `parents[0].localizedName` AND the
   two-hop HTML fetch from the earlier draft.)
2. `atrativos._ingest_one`: derive UF from `state_name` ("State of X") or `state_geo_id`,
   then `resolve_municipio(city_name, uf, ...)` → IBGE → `destino_rio_map[ibge_code]` →
   Mtur destino. Name-based, accent-folded match.
3. Cache `city_geo_id → IBGE município` (Redis or per-sweep) so the resolve is ~1 per
   distinct city; the GraphQL call itself stays per-attraction (cheap, no HTML surface).
4. Drop/defer TA destinos; source destinos from Mtur/IBGE (already the case). `_DESTINATIONS_QID`
   stays `None`.
5. Coords optional (link is name-based). If needed: `daebddd2c711c5fb` (city center) or
   detail-HTML JSON-LD (attraction-precise).
6. Sweep stays at corrected STATE geoIds (rmz `uf_geoids.json` + discovery script) — the
   per-attraction `d3d4987463b78a39` provides the município, so no per-city TA catalog and
   no blocked typeahead are needed. Full "sweep-by-city" is now moot.

Offline-testable with a scrubbed `gtmData.locationData` fixture (captured this session).
Real-TA opt-in. ToS/LGPD posture unchanged (aggregate geo only; no PII).
