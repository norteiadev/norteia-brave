---
phase: 14-coordless-attraction-geo-resolution-nominatim
verified: 2026-06-25T12:00:00Z
status: passed
score: 12/12 must-haves verified
overrides_applied: 0
human_verification_resolved: "Level-3 real MG sweep operator-approved in-session 2026-06-25 (recorded in 14-HUMAN-UAT.md); code-review blocker CR-01 + warning WR-01 fixed (commits 2761625, 59f22d6)"
human_verification:
  - test: "Confirm real MG sweep with RUN_REAL_EXTERNALS=1 yields Nascente entity_type='attraction' > 0 with municipio_ibge non-null and second sweep shows nominatim_cache_hit logs"
    expected: "SELECT entity_type, count(*) FROM nascente WHERE source='tripadvisor' GROUP BY entity_type shows attraction count > 0; municipio_ibge non-null on resolved records; second sweep log output shows nominatim_cache_hit for all attraction cards; ibge_unmatched quarantine is NOT dominant outcome"
    why_human: "Level-3 gate requires real network calls to TripAdvisor (DataDome session) and Nominatim; cannot verify from offline suite. 14-02-SUMMARY.md documents human approval on 2026-06-25 but verifier cannot independently confirm the DB state or log evidence."
---

# Phase 14: Coordless Attraction Geo-Resolution (Nominatim) — Verification Report

**Phase Goal:** Close the Phase-13 carry-forward gap where coordless attraction cards fuzzy-match the attraction NAME against IBGE município names and land in `ibge_unmatched` quarantine. This phase wires a geo-enrichment seam into the atrativos lane: before quarantining as `ibge_unmatched`, geocode the card via a typed, mockable OpenStreetMap Nominatim client; extract the município name (primary); IBGE name-match within the UF; fall back to haversine on returned lat/lon with relaxed radius (~50 km). Results cached by locationId. Offline-by-default (respx-mocked, opt-in real). LGPD-safe (only coordinates/OSM place ref persisted). Level-3: a real MG sweep yields Nascente entity_type='attraction' > 0 with municípios resolved.

**Verified:** 2026-06-25
**Status:** HUMAN_NEEDED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | GeocoderClientProtocol importable from brave.clients.base, structurally typed | VERIFIED | `base.py` lines 297–317: 10th protocol listed in module docstring; async `geocode(location_id, name, uf) -> dict[str,Any] \| None` defined |
| 2 | NominatimGeocoderClient raises RuntimeError when run_real_externals=False | VERIFIED | `nominatim.py` lines 121–127: guard mirrors places.py pattern; `test_guard_raises` passes in CI |
| 3 | NominatimGeocoderClient geocodes via async httpx GET with User-Agent + addressdetails=1 + countrycodes=br | VERIFIED | `nominatim.py` lines 178–193: params dict verified; `test_request_params` passes |
| 4 | Redis cache hit on second call produces no second httpx request | VERIFIED | `nominatim.py` lines 163–169: cache-hit path returns before http call; `test_cache_by_location_id` passes |
| 5 | Rate limit enforced: asyncio.sleep fires when last request was < min_request_interval ago | VERIFIED | `nominatim.py` lines 171–175; `test_rate_limit` passes (WR-06 raw module assignment is a test-quality issue, not a test-failure; all 436 tests pass without flakiness) |
| 6 | NullGeocoderClient.geocode returns None without network I/O | VERIFIED | `null_nominatim.py`: always returns None; `test_null_returns_none` passes; `_check_protocol_compliance()` passes |
| 7 | Result dict contains exactly lat/lon/osm_id/municipio_name (no street/PII keys) | VERIFIED | `nominatim.py` lines 219–225: 4-key dict only; `test_lgpd_no_pii` asserts `set(result.keys()) == {"lat", "lon", "osm_id", "municipio_name"}` — passes |
| 8 | Negative Nominatim result cached as {__no_match: true} sentinel | VERIFIED | `nominatim.py` lines 195–206: empty data branch caches sentinel; `test_negative_result_cached` passes |
| 9 | NominatimConfig reads from BRAVE_NOMINATIM_* env prefix, no Field(alias=...) | VERIFIED | `settings.py` lines 290–337: `SettingsConfigDict(env_prefix="BRAVE_NOMINATIM_")`, zero aliases; runtime import check confirmed `min_request_interval=1.1`, `cache_ttl=2592000` |
| 10 | atrativos._ingest_one is async + geo-enriches before ibge_unmatched quarantine; quarantine fires only after BOTH fail | VERIFIED | `atrativos.py` lines 137, 183–204: async def, geo-enrichment at lines 184–194, quarantine at lines 196–204 (after both attempts); `test_coordless_resolves_via_geo`, `test_quarantine_after_both_fail` both pass |
| 11 | pipeline.py sweep_tripadvisor selects real/null geocoder mirroring ta_client pattern and injects it | VERIFIED | `pipeline.py` lines 970–978, 1026: `NominatimGeocoderClient` when `run_real_externals=True`; `NullGeocoderClient()` otherwise; `geocoder=geocoder` passed to TripAdvisorAtrativosIngest; grep gates confirmed |
| 12 | Level-3 human checkpoint: real MG sweep confirms attraction count > 0, municipio_ibge non-null, second-sweep cache hits | HUMAN NEEDED | 14-02-SUMMARY.md documents "human-verified approved 2026-06-25" with success signals noted. Verifier cannot independently confirm DB state or log output — requires operator re-confirmation or acceptance of SUMMARY.md record as sufficient. |

**Score:** 11/12 truths verified (1 requires human re-confirmation of Level-3 gate)

---

### Deferred Items

None.

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/clients/base.py` | GeocoderClientProtocol (10th protocol) | VERIFIED | 10th protocol in module docstring; `async geocode(location_id, name, uf)` defined |
| `brave/clients/nominatim.py` | NominatimGeocoderClient real client | VERIFIED | 249 lines; UF_NAME dict, cache constants, _decode, _is_retryable, @retry, geocode(), _check_protocol_compliance() |
| `brave/clients/null_nominatim.py` | NullGeocoderClient offline stub | VERIFIED | Returns None, no network, _check_protocol_compliance() present |
| `brave/config/settings.py` | NominatimConfig + AppConfig.nominatim | VERIFIED | 5 fields, BRAVE_NOMINATIM_* prefix, zero aliases, nested in AppConfig.nominatim field |
| `tests/fakes/fake_nominatim.py` | FakeGeocoderClient fixture | VERIFIED | Records geocode_calls, returns fixture_results.get(location_id), _check_protocol_compliance() |
| `tests/unit/clients/test_nominatim.py` | 7 TA-14 unit tests (exact node IDs) | VERIFIED | 12 tests total; all 7 required node IDs confirmed present and passing: test_guard_raises, test_request_params, test_address_precedence, test_cache_by_location_id, test_rate_limit, test_null_returns_none, test_lgpd_no_pii |
| `brave/lanes/tripadvisor/atrativos.py` | Geo-enrichment + async _ingest_one + geocoder arg | VERIFIED | geocoder: GeocoderClientProtocol \| None = None; async _ingest_one; geo-enrichment block lines 183–194; quarantine only after both attempts |
| `brave/tasks/pipeline.py` | Geocoder selection + injection | VERIFIED | Lines 970–978: real/null geocoder selection; line 1026: geocoder=geocoder passed |
| `tests/unit/lanes/tripadvisor/test_atrativos.py` | 3 TA-15 regression tests | VERIFIED | TestAtrativosGeoEnrichment: test_coordless_resolves_via_geo, test_quarantine_after_both_fail, test_no_geocoder_unchanged — all pass |
| `tests/unit/lanes/tripadvisor/test_ibge.py` | ibge default-radius invariant | VERIFIED | test_resolve_municipio_default_max_distance_km_is_15 passes; ibge.py default confirmed 15.0 |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `brave/clients/nominatim.py` | `brave/clients/base.py` | GeocoderClientProtocol structural type compliance | VERIFIED | `_check_protocol_compliance()` at module bottom; null and fake variants also pass |
| `brave/clients/nominatim.py` | Nominatim OSM search endpoint | `async httpx.AsyncClient GET self._config.base_url` | VERIFIED | Lines 188–192; `base_url` defaults to `https://nominatim.openstreetmap.org/search`; `_NOMINATIM_SEARCH_URL` is dead code (IN-01) |
| `brave/config/settings.py` | NominatimConfig | `AppConfig.nominatim = Field(default_factory=NominatimConfig)` | VERIFIED | Line 353 confirmed; `isinstance(AppConfig().nominatim, NominatimConfig)` true |
| `brave/lanes/tripadvisor/atrativos.py` | `brave/clients/nominatim.py` | GeocoderClientProtocol injection into __init__ | VERIFIED | Line 99: `geocoder: "GeocoderClientProtocol | None" = None`; stored as `self._geocoder`; TYPE_CHECKING import (D-18) |
| `brave/lanes/tripadvisor/atrativos.py` | `brave/lanes/tripadvisor/ibge.py` | resolve_municipio called twice; 50.0 km at geo-enrichment site | VERIFIED | Lines 175–181 (first call, default radius); lines 187–194 (geo-enrichment call, max_distance_km=50.0) |
| `brave/tasks/pipeline.py` | `brave/clients/nominatim.py` | NominatimGeocoderClient constructed when run_real_externals=True | VERIFIED | Lines 971–974; mirrors ta_client block structure; `_redis_lib` and `_ta_redis_url` already in scope from if-branch |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `atrativos.py _ingest_one` | `ibge_match` (post geo-enrichment) | `geocoder.geocode()` → `resolve_municipio(..., max_distance_km=50.0)` | Yes (when geocoder is FakeGeocoderClient/real NominatimGeocoderClient) | VERIFIED for offline path; Level-3 for real path |
| `atrativos.py _ingest_one` | `lat`, `lng` in persisted payload | Original card values only; geo["lat"]/geo["lon"] NOT promoted (WR-01) | No — geocoded coordinates discarded | WARNING: WR-01 confirmed. Payload `lat`/`lng` remain None for coordless cards even after successful geo-enrichment. Phase goal (quarantine prevention) is achieved; coordinate completude is not. |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 7 TA-14 node IDs all pass | `pytest tests/unit/clients/test_nominatim.py::test_guard_raises ... (7 tests)` | 7 passed | PASS |
| 3 TA-15 node IDs all pass | `pytest tests/unit/lanes/tripadvisor/test_atrativos.py::TestAtrativosGeoEnrichment::*` | 3 passed | PASS |
| ibge invariant passes | `pytest tests/unit/lanes/tripadvisor/test_ibge.py::test_resolve_municipio_default_max_distance_km_is_15` | 1 passed | PASS |
| pipeline.py geocoder wiring grep gates | `grep -q "geocoder=geocoder" && grep -q "NominatimGeocoderClient(" && grep -q "NullGeocoderClient()"` | All match at expected lines | PASS |
| Protocol compliance (Null + Fake) | `python -c "from ... import _check_protocol_compliance; _check_protocol_compliance(); print('OK')"` | Both OK | PASS |
| Full offline suite | `env -u RUN_REAL_EXTERNALS pytest tests/unit -p no:warnings` | 436 passed, 5 skipped | PASS |
| Real MG sweep (Level-3) | RUN_REAL_EXTERNALS=1 sweep + DB query | Documented in 14-02-SUMMARY.md as approved | HUMAN NEEDED |

---

### Probe Execution

No conventional `scripts/*/tests/probe-*.sh` probes declared for this phase. Level-3 verification is a human-checkpoint task (Task 4 in 14-02-PLAN.md), not an automated probe. See Human Verification Required section.

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| TA-14 | 14-01-PLAN.md | Typed, mockable Nominatim geocoding client behind network boundary | VERIFIED | GeocoderClientProtocol + NominatimGeocoderClient + NullGeocoderClient + FakeGeocoderClient + NominatimConfig all present and tested. REQUIREMENTS.md still shows "Pending" — stale status marker, not a code gap. |
| TA-15 | 14-02-PLAN.md | Atrativos geo-enrichment integration: quarantine only after BOTH fail; Level-3 gate | PARTIALLY VERIFIED | Offline tests fully verified. Level-3 (real MG sweep) human-approved per SUMMARY.md but requires re-confirmation. REQUIREMENTS.md shows "Complete". |

**Note on REQUIREMENTS.md TA-14 status:** The traceability table shows TA-14 as "Pending" while TA-15 is "Complete". This appears to be a stale entry in REQUIREMENTS.md — the TA-14 client triad is fully implemented and tested. REQUIREMENTS.md should be updated to reflect "Complete" for TA-14.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `brave/clients/nominatim.py` | 197–198, 226 | `setex(key, NOMINATIM_CACHE_TTL, ...)` hardcodes module constant instead of `self._config.cache_ttl` | WARNING (CR-01 from code review) | Operator cannot shorten negative cache TTL via `BRAVE_NOMINATIM_CACHE_TTL`; a transiently-failed geocode is locked for 30 days. Does NOT block phase goal (quarantine prevention works). |
| `brave/clients/nominatim.py` | 45 | `_NOMINATIM_SEARCH_URL` defined but never referenced (dead code) | Info (IN-01) | Dead constant; client uses `self._config.base_url` correctly. No impact on behavior. |
| `brave/lanes/tripadvisor/atrativos.py` | 233–234, 252–253 | Geocoded `geo["lat"]`/`geo["lon"]` not promoted into `lat`/`lng` before payload construction | WARNING (WR-01 from code review) | Persisted payload has `lat: null, lng: null` for coordless cards even after successful geo-enrichment. Degrades completude score; discards the best artifact of the geocode call. Phase goal (quarantine prevention) is achieved; coordinate completude is not. |
| `tests/unit/clients/test_nominatim.py` | 274 | `nom_module.time.monotonic = lambda: now` — raw attribute write on shared stdlib `time` module, not via monkeypatch | Warning (WR-06 from code review) | Potential cross-test time.monotonic leak; restored by the earlier monkeypatch.setattr on same dotted path. All 436 tests currently pass without flakiness. |

**Debt marker gate:** No TBD, FIXME, or XXX markers found in any phase-14-modified files.

---

### Human Verification Required

#### 1. Level-3 Gate — Real MG Sweep Confirmation

**Test:** With `RUN_REAL_EXTERNALS=1` and a valid TripAdvisor DataDome session injected (per Phase-12/13 runbook):
1. Trigger a TripAdvisor atrativos sweep for MG
2. Query: `SELECT entity_type, count(*) FROM nascente WHERE source='tripadvisor' GROUP BY entity_type;`
3. Query: `SELECT municipio_ibge, count(*) FROM nascente WHERE source='tripadvisor' AND entity_type='attraction' GROUP BY municipio_ibge ORDER BY count DESC LIMIT 5;`
4. Query: `SELECT task_name, count(*) FROM quarantine GROUP BY task_name ORDER BY count DESC LIMIT 10;`
5. Re-run same sweep and check structlog output for `nominatim_cache_hit` entries

**Expected:**
- Nascente entity_type='attraction' count > 0
- `municipio_ibge` column non-null on resolved attraction records
- `ibge_unmatched` quarantine is NOT the dominant outcome (0 or only national parks)
- Second sweep shows `nominatim_cache_hit` for every attraction card (no fresh Nominatim calls)
- No RuntimeError or 429/IP-ban errors in logs

**Why human:** Requires real TripAdvisor DataDome session (not automatable in CI), real Nominatim HTTP calls, and live DB state inspection. 14-02-SUMMARY.md documents operator approval on 2026-06-25, but this verifier cannot independently confirm DB state or log evidence without running the actual sweep.

---

### Gaps Summary

No blocking gaps. The phase goal is functionally achieved: coordless attraction cards that previously mass-quarantined as `ibge_unmatched` are now geo-enriched via Nominatim before the quarantine fires, and the quarantine only triggers after both name-match and geocoding fail.

**Advisory items from code review (not blocking phase goal):**

- **CR-01 / WR-01 (code quality):** The `cache_ttl` config knob is silently ignored by both `setex` calls (hardcodes the module constant). Separately, geocoded `lat`/`lon` are used only to drive `resolve_municipio` and then discarded — the persisted Nascente payload still has `lat: null, lng: null` for geo-enriched cards. Both are correctness improvements for a follow-up plan, not blockers for the Phase 14 goal.

- **WR-06 (test hygiene):** `test_rate_limit` writes directly to `nom_module.time.monotonic` outside monkeypatch, risking cross-test leak. All 436 tests currently pass without flakiness, so this is not actively breaking anything.

- **REQUIREMENTS.md stale status:** TA-14 traceability row still shows "Pending" despite full implementation. Should be updated to "Complete" in a follow-up.

---

_Verified: 2026-06-25_
_Verifier: Claude (gsd-verifier)_
