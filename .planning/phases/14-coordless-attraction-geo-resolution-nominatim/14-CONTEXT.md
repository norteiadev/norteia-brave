---
phase: "14"
name: coordless-attraction-geo-resolution-nominatim
status: ready-to-plan
source: discussion + spike validation 2026-06-25
requirements: [TA-14, TA-15]
depends_on: ["13"]
---

# Phase 14 Context — Coordless attraction geo-resolution via OpenStreetMap Nominatim

## Problem (carry-forward from Phase 13)

AttractionsFusion listing cards carry **no lat/lng**. `_ingest_one` →
`resolve_municipio` (`brave/lanes/tripadvisor/ibge.py`) fuzzy-matches the
**attraction name** against IBGE município names (rapidfuzz `token_sort_ratio`,
threshold 88), with a haversine fallback that only runs when coordinates are
present. Real attraction names ("Cachoeira do Tabuleiro", "Instituto Inhotim")
do not match município names and have no coords → `ibge_unmatched` quarantine.
Net effect: a real sweep's Nascente stays near 0 even though the Phase-13 fetch
wiring is correct. Documented in `13-VERIFICATION.md` and `13-03-SUMMARY.md`.

## Spike evidence (2026-06-25)

`scripts/spike_nominatim_geo.py`, 10 real BR attractions across 8 UFs:

| Approach | Result |
|----------|--------|
| Nominatim forward-geocode `name + UF` → lat/lon | **10/10 geocoded** |
| haversine on returned coords → nearest IBGE seat | 9/10 exact município |
| `addressdetails=1` → `address.municipality\|city\|town` name-match | 4/5 exact (sampled) |

Key calibration: IBGE coords are the município **seat**, so natural attractions
sit 15–25 km out (Cataratas 22 km, Tabuleiro 16.8 km, Lençóis 25 km). The fixed
`max_distance_km=15` in `resolve_municipio` is too tight — relax to ~50 km for
the haversine fallback. The lone name-match miss (Lençóis Maranhenses → Santo
Amaro vs Barreirinhas) is a genuine multi-município national park, not a defect.

## Locked decisions

1. **Geocoder = OpenStreetMap Nominatim public API.** Free, no API key, HTTP
   (not browser-scraping). Chosen over Google Places (avoids per-request
   billing) and over Google-Maps browser scraping (fragile + ToS).
2. **Primary resolution = município name from the geocode address.** Request
   with `addressdetails=1`; read `address.municipality → city → town → village →
   county` (first present); exact/fuzzy name-match to IBGE **within the UF** →
   `ibge_code`. Robust against the seat-distance problem.
3. **Secondary = haversine fallback** on the returned lat/lon with a relaxed
   radius (~50 km, calibrated from spike) when the name is ambiguous/unmatched.
4. **Quarantine `ibge_unmatched` only after BOTH** the existing name fuzzy-match
   AND Nominatim geo-enrichment fail. Geo-enrichment is an enrichment step
   inserted before the quarantine, not a replacement of the current matcher.
5. **Cache geocode results by `locationId`** (Redis) — one Nominatim call per
   attraction at most; re-sweeps hit cache.
6. **Rate-limit ≥1 req/s** with a custom, identifiable User-Agent per Nominatim
   usage policy. ~30 attractions/page ⇒ ~30 s/UF — fine for the operator-gated,
   deliberately-slow sweep.
7. **Typed client behind the network boundary.** New geocoding client with a
   `NullGeocoder` (returns no match) + `FakeGeocoder` (fixture), respx-mocked in
   unit tests, real calls only under `RUN_REAL_EXTERNALS` opt-in, never in CI.
8. **LGPD-safe.** Persist only lat/lon + the OSM place id; never address PII.

## Scope

- New typed Nominatim geocoding client (TA-14): forward `search` +
  `addressdetails=1`, User-Agent, rate limit, Redis cache by `locationId`,
  Null/Fake variants, config (`BRAVE_NOMINATIM_*` / reuse `BRAVE_*` pattern).
- Atrativos integration (TA-15): geo-enrichment step before `ibge_unmatched`
  quarantine; relaxed haversine radius; name-from-address → IBGE match.
- Tests: offline respx-mocked Nominatim; a regression test proving a coordless
  card that previously quarantined now resolves to the correct município.
- Level-3 re-validation: real MG sweep → Nascente `entity_type='attraction'` > 0
  with municípios resolved (operator human-gate, mirrors Phase-13 runbook).

## Out of scope

- Self-hosted Nominatim for all-BR bulk scale (documented future op — the public
  instance is acceptable for operator-gated sweeps; revisit if volume/ToS forces
  it).
- Destinos geo-resolution (destinos already carry município context).
- Reverse-geocoding a known coordinate (cards have none — forward geocode only).
- `oa30` multi-page pagination (separate follow-up phase; needs an operator
  capture of an `oa30` request to find the real paging param).
- Touching the §7.6 scoring weights or the `mar_ready` promote path.

## Reference

- Spike: `scripts/spike_nominatim_geo.py`
- Existing matcher: `brave/lanes/tripadvisor/ibge.py` (`resolve_municipio`,
  `haversine_km`, `load_ibge_csv`); IBGE data `data/ibge/ibge_municipios.csv`.
- Ingest path: `brave/lanes/tripadvisor/atrativos.py` (`_ingest_one`).
- Network-boundary client pattern: `brave/clients/` (Null/Fake), respx tests.
