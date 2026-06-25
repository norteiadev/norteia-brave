---
phase: 14-coordless-attraction-geo-resolution-nominatim
fixed_at: 2026-06-25T00:00:00Z
review_path: .planning/phases/14-coordless-attraction-geo-resolution-nominatim/14-REVIEW.md
iteration: 1
findings_in_scope: 2
fixed: 2
skipped: 0
status: all_fixed
---

# Phase 14: Code Review Fix Report

**Fixed at:** 2026-06-25
**Source review:** `.planning/phases/14-coordless-attraction-geo-resolution-nominatim/14-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 2 (CR-01 + WR-01, per explicit constraint)
- Fixed: 2
- Skipped: 0

## Fixed Issues

### CR-01: NominatimConfig.cache_ttl and base_url are silently ignored — config knobs are dead

**Files modified:** `brave/clients/nominatim.py`, `tests/unit/clients/test_nominatim.py`
**Commit:** `2761625`
**Applied fix:**
- Removed dead `_NOMINATIM_SEARCH_URL` module constant (never referenced; GET uses `self._config.base_url`).
- Added `self._cache_ttl: int = config.cache_ttl` in `__init__` so the TTL is resolved from config at construction time.
- Replaced both `setex(key, NOMINATIM_CACHE_TTL, ...)` calls (negative sentinel at line 198 and positive result at line 226) with `setex(key, self._cache_ttl, ...)`.
- Added `test_cache_ttl_from_config`: constructs a `NominatimConfig(cache_ttl=300)`, wraps FakeRedis with a MagicMock spy, and asserts both setex paths emit `300` as the TTL (not the 2592000 module default). Tests both positive-result and negative-sentinel branches independently.
- Unit suite: 437 passed, 5 skipped (was 436+5; +1 is the new test).

### WR-01: Geocoded coordinates are discarded — persisted lat/lng stay None for geo-enriched cards

**Files modified:** `brave/lanes/tripadvisor/atrativos.py`, `tests/unit/lanes/tripadvisor/test_atrativos.py`
**Commit:** `59f22d6`
**Applied fix:**
- Re-ordered `_ingest_one` so the geo-enrichment block runs before `completude_entity` construction.
- When `geocoder.geocode` returns a non-None result, `lat = geo["lat"]` and `lng = geo["lon"]` are now promoted into the working variables before the second `resolve_municipio` call and before `completude_entity` is built.
- `completude_entity` and `TripAdvisorAtrativoPayload` therefore receive the resolved coordinates, and the persisted Nascente payload carries real lat/lng instead of None.
- Existing behavior preserved: the `geocoder=None` path is unchanged; the both-fail quarantine path is unchanged.
- Extended `test_coordless_resolves_via_geo` with explicit assertions: `payload["lat"] == -19.047` and `payload["lng"] == -43.426`, locking the geocoded coordinates into the persisted payload.
- Unit suite: 437 passed, 5 skipped (stable after both fixes applied).

## Skipped Issues

None — both in-scope findings were fixed.

---

_Fixed: 2026-06-25_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
