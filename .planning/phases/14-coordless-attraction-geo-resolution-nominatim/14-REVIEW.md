---
phase: 14-coordless-attraction-geo-resolution-nominatim
reviewed: 2026-06-25T00:00:00Z
depth: standard
files_reviewed: 11
files_reviewed_list:
  - brave/clients/base.py
  - brave/clients/nominatim.py
  - brave/clients/null_nominatim.py
  - brave/config/settings.py
  - brave/lanes/tripadvisor/atrativos.py
  - brave/tasks/pipeline.py
  - tests/fakes/fake_nominatim.py
  - tests/unit/clients/test_nominatim.py
  - tests/unit/lanes/tripadvisor/test_atrativos.py
  - tests/unit/lanes/tripadvisor/test_ibge.py
  - tests/unit/tasks/test_sweep_tripadvisor.py
findings:
  critical: 1
  warning: 6
  info: 4
  total: 11
status: issues_found
---

# Phase 14: Code Review Report

**Reviewed:** 2026-06-25
**Depth:** standard
**Files Reviewed:** 11
**Status:** issues_found

## Summary

This phase adds the Nominatim geocoder client triad (`NominatimGeocoderClient`,
`NullGeocoderClient`, `FakeGeocoderClient`), a `NominatimConfig`, and wires
geo-enrichment into the TripAdvisor atrativos ingest path and the
`sweep_tripadvisor` Celery task.

The network-boundary discipline is sound (guard in the real client, Null stub for
offline, no test reaches the network), LGPD result shaping is correctly narrowed
to 4 keys and well covered by `test_lgpd_no_pii`, and the `ibge` default-radius
invariant is explicitly guarded by a test.

However there is one BLOCKER: the configurable `NominatimConfig` knobs
(`cache_ttl`, `base_url`) are silently ignored by the client, so a self-hosted
endpoint or operator-tuned TTL has no effect — and the documented 30-day cache is
applied to negative results regardless. There are also several WARNINGs around the
geo-enrichment data flow (geocoded coordinates are discarded from the persisted
payload), rate-limit correctness across event loops, and `osm_id`/`lat` coercion
robustness.

## Critical Issues

### CR-01: NominatimConfig.cache_ttl and base_url are silently ignored — config knobs are dead

**File:** `brave/clients/nominatim.py:43, 188-199, 226`
**Issue:**
`NominatimConfig` exposes `cache_ttl` (env `BRAVE_NOMINATIM_CACHE_TTL`) and
`base_url` (env `BRAVE_NOMINATIM_BASE_URL`) as tunable fields, and the client
constructor stores `self._config`. But the cache write paths hardcode the
module-level `NOMINATIM_CACHE_TTL` constant instead of `self._config.cache_ttl`:

```python
self._redis.setex(key, NOMINATIM_CACHE_TTL, json.dumps({"__no_match": True}))  # line 198
...
self._redis.setex(key, NOMINATIM_CACHE_TTL, json.dumps(result))                # line 226
```

Consequences:
- An operator who sets `BRAVE_NOMINATIM_CACHE_TTL` (e.g. to shorten the negative
  cache so a name that failed once is retried sooner) has **no effect**. A
  `__no_match` sentinel is pinned for the full 30 days no matter what. Because
  geocode is keyed by `location_id` and the negative cache is permanent-for-30d,
  a transiently-empty Nominatim response (or a name that later becomes
  resolvable) silently quarantines that attraction for a month with no operator
  override path short of flushing Redis.
- `base_url` *is* used for the actual GET (line 190 passes `self._config.base_url`),
  but the module constant `_NOMINATIM_SEARCH_URL` (line 45) is dead, and the
  retry/test surface (`respx.get("https://nominatim.openstreetmap.org/search")`)
  only works because the default happens to match — a self-hosted `base_url`
  override is plausibly correct but untested, while the TTL override is provably
  broken.

This is a correctness/data-availability defect: a documented, env-exposed control
knob does nothing, and the failure mode (30-day silent quarantine of a real
attraction) is exactly the kind of data-loss the phase is meant to prevent.

**Fix:**
Resolve TTL from config in both cache writes, and drop the dead constant:

```python
# in __init__
self._cache_ttl: int = config.cache_ttl

# negative cache
self._redis.setex(key, self._cache_ttl, json.dumps({"__no_match": True}))
# positive cache
self._redis.setex(key, self._cache_ttl, json.dumps(result))
```

Add a test asserting `setex` is called with `config.cache_ttl` (override it to a
non-default value and assert the TTL arg), mirroring how `test_request_params`
asserts query params.

## Warnings

### WR-01: Geocoded coordinates are discarded — persisted lat/lng stay None for geo-enriched cards

**File:** `brave/lanes/tripadvisor/atrativos.py:142-143, 184-194, 250-253`
**Issue:**
In `_ingest_one`, `lat`/`lng` are read once from the card (lines 142-143) and the
card is coordless by definition for the geo-enrichment path. When geocoding
succeeds, the resolved `geo["lat"]/geo["lon"]` are used **only** to drive
`resolve_municipio` (lines 191-194) and are then thrown away. The persisted
payload (lines 250-253) and `TripAdvisorAtrativoPayload` (lines 233-234) still use
the original `lat=None, lng=None`.

Result: an attraction that was successfully geolocated lands in Nascente with
`lat: null, lng: null` and a `completude_value` that never counts the 2 coordinate
fields — even though the pipeline just resolved real coordinates one step earlier.
This degrades completude scoring and discards the most valuable artifact of the
geocode call. The geocoder returns lat/lon precisely so downstream can use them.

**Fix:**
When geo-enrichment resolves, promote the geocoded coordinates into the working
lat/lng before building the completude entity and payload:

```python
if ibge_match is None and self._geocoder is not None:
    geo = await self._geocoder.geocode(location_id, name, uf)
    if geo is not None:
        lat = geo["lat"]
        lng = geo["lon"]
        ibge_match = resolve_municipio(
            geo.get("municipio_name") or name, uf, self._ibge_records,
            candidate_lat=geo["lat"], candidate_lng=geo["lon"],
            max_distance_km=50.0,
        )
```

Note this requires moving the geo-enrichment block above the `completude_entity`
construction (currently completude is computed at line 165-172, before the
geocode at 184). Re-order so completude reflects the enriched coordinates.

### WR-02: Rate-limit state (`_last_request_ts`) is unreliable across Celery's per-task event loops

**File:** `brave/clients/nominatim.py:130-131, 172-175`; `brave/tasks/pipeline.py:970-978, 1028`
**Issue:**
The client enforces ≥1 req/s via an instance attribute `self._last_request_ts`
updated with `time.monotonic()`. In `sweep_tripadvisor` a single
`NominatimGeocoderClient` instance is created per task invocation and is shared
across the atrativos `produce` loop, so within one sweep the limiter works. But:

1. The limiter is purely in-process per client instance. Nominatim's usage policy
   is a **global** ≥1 req/s across the whole application; multiple concurrent
   Celery workers each construct their own client and each keeps an independent
   `_last_request_ts`, so N workers can collectively exceed 1 req/s and risk a
   Nominatim IP ban. The TripAdvisor `geo.py` analog uses Redis for shared state;
   this client does not. For a 24/7 multi-worker service this is a real
   throttling gap, not a style nit.
2. `self._last_request_ts` is initialized to `0.0` (line 131), so the very first
   call in a process computes `elapsed = monotonic() - 0.0`, which is a large
   number — correct (no spurious sleep). That part is fine, but worth noting the
   limiter only constrains the *second+* call within a single client's lifetime.

**Fix:**
Move the rate-limit token to Redis (e.g. a short-TTL `SET key NX` gate or a
last-request timestamp key) so the limit is enforced across workers, mirroring the
shared-Redis pattern already used in `geo.py`. At minimum, document that the
limiter is per-process and ensure beat fan-out does not run multiple concurrent
Nominatim-enabled sweeps.

### WR-03: `float(hit["lat"])` / `float(hit["lon"])` can raise KeyError/ValueError and quarantine a valid attraction

**File:** `brave/clients/nominatim.py:221-222`
**Issue:**
The positive-result path does `float(hit["lat"])` and `float(hit["lon"])` with
direct subscript. Nominatim normally returns these, but a malformed or partial row
(missing `lat`, or a non-numeric value) raises `KeyError`/`ValueError` inside
`geocode`. Because `_is_retryable` only matches HTTP/timeout/connect errors, this
exception is **not** retryable and propagates up to `_ingest_one`'s broad
`except Exception` in `produce` (atrativos.py:127), quarantining the attraction as
a poison record rather than treating it as "no usable geocode → None". The
4-key contract documents `osm_id`/`municipio_name` as possibly-None but treats
lat/lon as always-present; a defensive guard is cheap.

**Fix:**
Guard the coordinate parse and treat unparseable coordinates as a no-match:

```python
try:
    lat = float(hit["lat"])
    lon = float(hit["lon"])
except (KeyError, TypeError, ValueError):
    self._redis.setex(key, self._cache_ttl, json.dumps({"__no_match": True}))
    return None
```

### WR-04: `_check_protocol_compliance` in nominatim.py binds the class, not an instance — weaker check than the Null/Fake variants

**File:** `brave/clients/nominatim.py:244-248`
**Issue:**
The compile-time protocol assertion assigns the **class object** to the protocol
type with a blanket `# type: ignore[assignment]`:

```python
_c: GeocoderClientProtocol = NominatimGeocoderClient  # type: ignore[assignment]
```

The Null and Fake variants correctly assign an **instance**
(`NullGeocoderClient()`, `FakeGeocoderClient()`). Assigning the class plus a blunt
`type: ignore` defeats the purpose — mypy will not actually verify that the
instance structurally satisfies the protocol (the ignore suppresses the real
error a class-vs-instance mismatch would surface), so a future signature drift on
`geocode` would not be caught here. The reason it cannot instantiate is the
`run_real_externals` guard in `__init__`; that is a design smell making the type
assertion ineffective.

**Fix:**
Use an explicit unbound-method/protocol structural check that does not require
instantiation, e.g. assert against the method type, or gate construction so the
assertion can build a real instance. Simplest: keep a module-level type alias
checked via `typing.assert_type` on the bound method signature rather than the
class object, so drift in `geocode` is actually caught.

### WR-05: Negative-cache sentinel cannot be distinguished from a future real key collision; `__no_match` is an unvalidated magic key

**File:** `brave/clients/nominatim.py:168-169, 196-199`
**Issue:**
Cache reads do `cached = json.loads(raw); return None if cached.get("__no_match") else cached`.
A positive result is the full 4-key dict; a negative is `{"__no_match": true}`.
This works, but the read path returns `cached` (the raw parsed dict) directly to
the caller without re-validating it has the expected 4 keys. If the cache schema
ever changes (e.g. CR-01's TTL fix is paired with a schema bump, or a stale entry
from a prior version is present), the caller silently receives a dict of a
different shape, and `_ingest_one` does `geo["lat"]`/`geo["lon"]` (atrativos.py:191-192),
which would `KeyError` and quarantine the record. There is no cache-version
namespace in the key (`brave:geo:{location_id}`), so a deploy that changes the
result shape cannot invalidate old entries except by waiting out 30 days.

**Fix:**
Add a version segment to the cache key (e.g. `brave:geo:v1:{location_id}`) so a
shape change is a clean cache miss, and validate the cached positive dict has the
expected keys before returning it (fall through to a fresh fetch on mismatch).

### WR-06: `test_rate_limit` mutates module global `nom_module.time.monotonic` without restoring it — cross-test leakage risk

**File:** `tests/unit/clients/test_nominatim.py:251-275`
**Issue:**
The test sets `monkeypatch.setattr("brave.clients.nominatim.time.monotonic", ...)`
(auto-restored) but then *also* directly assigns
`nom_module.time.monotonic = lambda: now` (line 274) — a raw attribute write on the
shared `time` module object, **not** via monkeypatch. Since `time` is the global
stdlib module, this mutation patches `time.monotonic` process-wide and is **not**
restored by pytest's monkeypatch teardown. The earlier `monkeypatch.setattr` on
the same dotted path will restore *its* version on teardown, which may mask the
leak, but relying on that ordering is fragile. Any test ordering change could
leave a stubbed `time.monotonic` leaking into unrelated tests (flaky timing
assertions elsewhere). The test also has dead/contradictory setup (lines 270 and
275 both set `_last_request_ts`, the first immediately overwritten).

**Fix:**
Remove the raw `nom_module.time.monotonic = ...` assignment and rely solely on
`monkeypatch.setattr` for `time.monotonic`. Delete the redundant first
`_last_request_ts` assignment (line 270). The `fake_monotonic` already installed
via monkeypatch is sufficient to drive the elapsed calculation.

## Info

### IN-01: `_NOMINATIM_SEARCH_URL` module constant is dead code

**File:** `brave/clients/nominatim.py:45`
**Issue:** `_NOMINATIM_SEARCH_URL` is defined but never referenced — the GET uses
`self._config.base_url` (line 190). Dead constant.
**Fix:** Remove `_NOMINATIM_SEARCH_URL`, or use it as the `NominatimConfig.base_url`
default to keep a single source of truth.

### IN-02: `_decode` helper handles `None`/bytes but the result is then truthiness-checked — minor redundancy

**File:** `brave/clients/nominatim.py:84-90, 164-166`
**Issue:** `_decode(self._redis.get(key))` returns `""` for a Redis miss, then
`if raw:` correctly skips. Works, but the `_decode` → `if raw` round-trip is a bit
indirect versus checking the raw `get` result directly. Not a bug; noted for
readability parity with `geo.py`.
**Fix:** Optional — inline the None check, or keep for analog consistency.

### IN-03: `UF_NAME.get(uf, uf)` falls back to the raw 2-letter code for an unknown UF

**File:** `brave/clients/nominatim.py:178`
**Issue:** For an invalid/unknown `uf`, the query becomes `"{name}, {uf}, Brazil"`
using the bare code (e.g. `"X, ZZ, Brazil"`). This will almost always return no
result and cache a 30-day `__no_match` (compounding CR-01). Upstream callers pass
validated 2-letter codes, so this is defensive-only, but a silent fallback to a
guaranteed-miss query is worth a debug log.
**Fix:** Log a warning when `uf not in UF_NAME` so an upstream UF typo is visible
rather than silently producing a permanent negative cache.

### IN-04: `test_atrativos.py` geo-enrichment tests do not assert persisted lat/lng — they would not catch WR-01

**File:** `tests/unit/lanes/tripadvisor/test_atrativos.py:337-394`
**Issue:** `test_coordless_resolves_via_geo` asserts `municipio_id` and the geocode
call, but never checks `payload["lat"]`/`payload["lng"]`. As written it passes even
though the persisted coordinates are `None` (WR-01). The test suite gives false
confidence that geo-enrichment is fully wired.
**Fix:** After the WR-01 fix, add
`assert payload["lat"] == -19.047 and payload["lng"] == -43.426` to lock the
geocoded coordinates into the persisted payload.

---

_Reviewed: 2026-06-25_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
