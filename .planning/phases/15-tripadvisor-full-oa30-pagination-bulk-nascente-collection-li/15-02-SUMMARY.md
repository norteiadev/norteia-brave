---
phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
plan: 02
subsystem: clients / protocol boundary
tags: [tripadvisor, geocoder, protocol, interface-first, offline-safe, TA-12]
requirements-completed: [TA-12]
dependency-graph:
  requires: []
  provides:
    - "TripAdvisorClientProtocol.fetch_attractions_paginated contract (async iterator of (offset, cards))"
    - "GeocoderClientProtocol.geocode_national contract (no-UF national forward-geocode)"
    - "NullTripAdvisorClient / NullGeocoderClient offline stubs for both new methods"
    - "FakeTripAdvisorClient.fetch_attractions_paginated + FakeGeocoderClient.geocode_national call-recorders"
  affects:
    - "15-04 (real TripAdvisorClient.fetch_attractions_paginated implementation)"
    - "15-05 (real NominatimClient.geocode_national implementation)"
    - "15-06 (bulk all-Brazil ingest lane consuming both contracts)"
tech-stack:
  added: []
  patterns:
    - "Interface-first protocol widening (all four implementers in one wave — Pitfall 6 avoidance)"
    - "Async-generator null stub (return-then-unreachable-yield) for empty async iterators"
    - "Call-recording fakes mirroring the existing fixture/recorder posture"
key-files:
  created: []
  modified:
    - brave/clients/base.py
    - brave/clients/null_tripadvisor.py
    - tests/fakes/fake_tripadvisor.py
    - brave/clients/null_nominatim.py
    - tests/fakes/fake_nominatim.py
decisions:
  - "geocode_national takes NO uf arg — the all-Brazil bulk lane derives UF downstream from the geocoded município/IBGE code, not from input."
  - "Null TripAdvisor paginated stub is an async generator (yields nothing) so it structurally satisfies the async-iterator protocol while crossing no network (T-11-01-03)."
  - "Protocol stub for fetch_attractions_paginated declared as a plain def returning AsyncIterator (covariant-compatible with the real async-generator implementations)."
  - "Single-page fetch_attractions (WR-02 NotImplementedError on max_pages>1) left byte-for-byte unchanged — the new method is a separate transport."
metrics:
  duration: ~12m
  completed: 2026-06-26
  tasks: 2
  files-changed: 5
---

# Phase 15 Plan 02: Widen TripAdvisor + Geocoder client protocols (interface-first) Summary

Locked the two new client contracts this phase needs — `fetch_attractions_paginated`
(async iterator of `(offset, cards)` over the HTML SSR transport) and `geocode_national`
(no-UF national forward-geocode) — across the protocol, null, and fake implementers in a
single wave, so plans 15-04/15-05/15-06 implement against fixed signatures with no
codebase scavenger hunt and the offline (null) CI path never breaks (Pitfall 6).

## What Was Built

### Task 1 — `fetch_attractions_paginated` on TripAdvisor protocol/null/fake
- `brave/clients/base.py` — added the `fetch_attractions_paginated(self, geo_id, start_page=1, max_pages=334) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]` stub as a sibling of `fetch_attractions`, documenting that it yields one `(offset, parsed_cards)` tuple per HTML SSR page and reuses `_parse_attractions_page`.
- `brave/clients/null_tripadvisor.py` — added an async-generator stub that yields nothing (`return` then an unreachable `yield`), preserving the no-Playwright/no-network posture (T-11-01-03).
- `tests/fakes/fake_tripadvisor.py` — added a `fixture_pages` constructor arg + `paginated_calls` recorder; the method records `{geo_id, start_page, max_pages}` then yields each `(offset, cards)` from `fixture_pages[geo_id]`.

### Task 2 — `geocode_national` on Geocoder protocol/null/fake
- `brave/clients/base.py` — added `geocode_national(self, location_id, name) -> dict[str, Any] | None` (NO `uf` arg) as a sibling of `geocode`, documented to return the SAME 4-key LGPD-safe shape `{"lat", "lon", "osm_id", "municipio_name"}` or `None`, derived from a national `"{name}, Brazil"` query.
- `brave/clients/null_nominatim.py` — added `geocode_national` returning `None` (no network, T-14-05).
- `tests/fakes/fake_nominatim.py` — added a `fixture_national_results` constructor arg + `geocode_national_calls` recorder mirroring the existing `geocode` recorder.

## Must-Haves

- [x] Every TripAdvisor client (protocol, null, fake) advertises `fetch_attractions_paginated`
- [x] Every geocoder client (protocol, null, fake) advertises `geocode_national`
- [x] Protocol-compliance checks pass for all four implementers of each protocol
      (real `TripAdvisorClient`, `NullTripAdvisorClient`, `FakeTripAdvisorClient`;
      real `NominatimClient`, `NullGeocoderClient`, `FakeGeocoderClient` — every
      `_check_protocol_compliance()` imports + executes cleanly)
- [x] `base.py` provides both stubs (`fetch_attractions_paginated` + `geocode_national`)
- [x] `FakeTripAdvisorClient.fetch_attractions_paginated` is a call-recorder (`paginated_calls`) yielding `fixture_pages`
- [x] Single-page `fetch_attractions` (WR-02 `max_pages>1` NotImplementedError) byte-for-byte unchanged

## Verification

Both plan `<verify>` commands run with `RUN_REAL_EXTERNALS` UNSET — all green:

- Task 1: `pytest tests/unit/lanes/tripadvisor/test_client.py tests/unit -k "protocol or compliance or fake_tripadvisor"` → passed
- Task 2: `pytest tests/unit -k "geocod or nominatim or compliance"` → passed
- Combined run → passed; all five `_check_protocol_compliance()` functions import + execute cleanly.

Acceptance greps confirmed: one `def fetch_attractions_paginated` per of the three TA files,
one `def geocode_national` per of the three geocoder files. `NullTripAdvisorClient`
paginated stub asserted as `inspect.isasyncgen` yielding zero items; `NullGeocoderClient.geocode_national`
asserted to return `None` offline.

## Deviations from Plan

None — plan executed exactly as written. No package installs, no schema changes, no
architectural decisions. `_check_protocol_compliance()` bodies were left at the existing
assignment-only form (the `TripAdvisorClientProtocol = X()` assignment structurally covers
the whole protocol, including the newly added methods); no per-method runtime assertion was
needed because the protocol is intentionally not `runtime_checkable`.

## Known Stubs

The two new methods are intentionally contract-only stubs on the protocol (`...`) and
no-op/yield-nothing on the null clients — this is the interface-first deliverable of plan
15-02. The real implementations land in 15-04 (`TripAdvisorClient.fetch_attractions_paginated`)
and 15-05 (`NominatimClient.geocode_national`); the bulk lane that consumes them is 15-06.
No stub blocks this plan's goal (locking the contracts).

## Self-Check: PASSED
- All created/modified files exist on disk and are committed.
- Both task commits present on the worktree branch (`51a880d`, `2adb04a`).
