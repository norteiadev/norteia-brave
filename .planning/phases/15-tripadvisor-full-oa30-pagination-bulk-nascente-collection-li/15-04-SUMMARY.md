---
phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
plan: 04
subsystem: tripadvisor-lane
tags: [tripadvisor, pagination, html-ssr, scraping, lgpd, ssrf, config]
requires:
  - "15-01: protocol stub TripAdvisorClientProtocol.fetch_attractions_paginated"
  - "15-02: null + fake fetch_attractions_paginated implementations"
  - "_parse_attractions_page (Phase 13, reused unchanged)"
provides:
  - "TripAdvisorClient._extract_sections_from_html — recovers the embedded FlexCard sections[] JSON island from a captured SSR page (stdlib re+json only)"
  - "TripAdvisorClient.fetch_attractions_paginated — real HTML-SSR paginated transport (async generator yielding (offset, cards))"
  - "TripAdvisorConfig.page_throttle_seconds (BRAVE_TA_PAGE_THROTTLE_SECONDS)"
affects:
  - "brave/lanes/tripadvisor/atrativos.py bulk ingest path (15-05 will drive this generator)"
  - "brave/tasks/pipeline.py sweep_tripadvisor bulk branch"
tech-stack:
  added: []
  patterns:
    - "Embedded-JSON island recovery: locate data: URI script blob -> URL-decode -> json.loads longest marker-bearing JS string literal -> recursive walk re-parsing inner JSON-string chunks"
    - "Async-generator paged transport reusing single-page cookie/proxy/UA/403-raise wiring"
    - "Loop clamp to a hard provider cap independent of caller-supplied range"
key-files:
  created:
    - "tests/unit/lanes/tripadvisor/test_pagination.py"
  modified:
    - "brave/lanes/tripadvisor/client.py"
    - "brave/config/settings.py"
decisions:
  - "Extractor recovers the multiply-escaped flight payload by peeling one JS-string-literal level then recursively re-parsing inner JSON-string chunks — the GraphQL envelope path is NOT hardcoded (SSR embedding differs)."
  - "geo_id int-only guard raises TypeError before any GET (bool rejected as int subclass) — SSRF mitigation T-15-04-02."
  - "URL always includes -oa0- for page 1 (oa0 is valid per CONTEXT) — uniform template, no special-casing."
  - "scraper-dep test uses an AST import check, not a raw grep: the words 'Playwright'/'beautifulsoup' legitimately appear in negative-context comments/docstrings."
metrics:
  tasks: 2
  commits: 4
  files-changed: 3
  tests-added: 17
  completed: 2026-06-26
requirements-completed: [TA-12]
---

# Phase 15 Plan 04: Real HTML-SSR Pagination Transport Summary

Implemented the locked HTML-SSR pagination transport for the TripAdvisor attractions lane: a stdlib-only embedded-JSON extractor that recovers the FlexCard `sections[]` island from a captured `-oa30-` page, and `fetch_attractions_paginated` — an async generator that GETs each `-oa{N}-` page, parses via the unchanged `_parse_attractions_page`, throttles between pages, clamps to the 334-page/oa9990 cap, and fails fast on 403/429 — all offline-tested against the Wave-0 fixture and respx.

## What Was Built

### Task 1 — `_extract_sections_from_html` (test-first)
- `@staticmethod _extract_sections_from_html(html) -> list[dict]` plus a `_find_flexcard_sections` recursive helper, using ONLY `re` + `json` + `urllib.parse.unquote` (no lxml/beautifulsoup/selectolax/playwright).
- The real fixture embeds the listing as a chunked, multiply-escaped flight payload inside a `<script src="data:text/javascript,...">` island. Recovery: find the marker-bearing script blob → URL-decode the `data:` URI → `json.loads` the longest JS string literal still carrying the FlexCard marker (peels one escape level) → recursively walk, re-parsing string leaves that are themselves JSON, until the 30 section dicts surface.
- Recovers **30 FlexCard sections → 30 normalized cards** (e.g. `Parque Lage`, locationId 311472, rating 4.4, 5924 reviews) when fed to `_parse_attractions_page` unchanged. LGPD aggregate-only posture preserved (cards carry exactly name/locationId/rating/review_count/category).
- Never raises: empty/garbage HTML → `[]`.

### Task 2 — `fetch_attractions_paginated` + page-throttle config
- New `TripAdvisorConfig.page_throttle_seconds: float = 2.0` (`BRAVE_TA_PAGE_THROTTLE_SECONDS`), CR-02-compliant (no `Field(alias=...)`; docstring env list updated).
- Module constants `_TA_HTML_URL`, `_TA_MAX_PAGE = 334`, `_TA_MAX_OFFSET = 9990`, `_TA_FLEXCARD_TYPENAME`.
- Async generator reuses `fetch_attractions`' exact session/cookie/UA/proxy wiring and the `SessionExpiredError` 403/429 fail-fast. Per page: GET `-oa{(page-1)*30}-` URL → extract → parse → `yield (offset, cards)` → `asyncio.sleep(throttle)` between pages (never after the last).
- Loop clamped: `last_page = min(start + max_pages, 335)`, so `start_page=333, max_pages=334` GETs only offsets 9960 + 9990 and stops at the cap.
- SSRF guard (T-15-04-02): non-int `geo_id` (incl. `bool`) raises `TypeError` before any GET.
- Logging discipline (T-15-04-01): per-page logs carry only offset/page/status/card_count.
- Single-page `fetch_attractions` and its WR-02 `NotImplementedError` left byte-for-byte intact (verified: no signature/diff change to existing methods).

## Tests
`tests/unit/lanes/tripadvisor/test_pagination.py` (17 tests, all offline, `RUN_REAL_EXTERNALS` unset):
extractor recovers ≥25 sections + feeds parser; empty/garbage → []; AST no-scraper-import; config field present + env-resolves + no-alias; paginated offsets [0,30] and [30,60]; cap clamp to [9960,9990]; 403 + 429 fail-fast; non-int geo_id raises before any GET; throttle [1.5,1.5] for 3 pages; AST no-secret-logging in the new method.

Verify run (plan filter `paginated or max_pages or throttle or contract or cap or geoid or extract`): **22 passed**. Full `tests/unit/lanes/tripadvisor/ + tests/unit/clients/`: **147 passed**.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] scraper-dependency test changed from raw grep to AST import check**
- **Found during:** Task 1 GREEN.
- **Issue:** The plan's acceptance check (`grep -Eiq 'lxml|beautifulsoup|selectolax|playwright' client.py` → no match) is impossible to satisfy: the word "Playwright" pre-exists in the class docstring ("Constructor does NOT import Playwright"), and the new extractor docstring lists the forbidden tools precisely to document that they are NOT used. A raw grep produces a false positive on negative-context prose.
- **Fix:** The test now parses `client.py` with `ast` and asserts none of those modules are actually **imported** — the correct semantic check, matching the existing `test_client.py` import-AST convention.
- **Files modified:** tests/unit/lanes/tripadvisor/test_pagination.py
- **Commit:** 5593ba6

### Notes (not deviations)
- The plan/acceptance mentions "Existing test_client.py test asserting fetch_attractions(max_pages=2) raises NotImplementedError STILL passes." No such test currently exists in the repo; the WR-02 contract code is untouched and the full `test_client.py` suite stays green (34 tests).
- Pre-existing ruff findings in `client.py` (quoted `__init__` annotation; `_LISTING_QID` N806 inside the untouched `fetch_attractions`) are out of scope and were left as-is.

## Known Stubs
None — both methods are fully wired against the real fixture + respx; no placeholder data paths introduced.

## Self-Check: PASSED
- `brave/lanes/tripadvisor/client.py` — FOUND (`_extract_sections_from_html`, `fetch_attractions_paginated`)
- `brave/config/settings.py` — FOUND (`page_throttle_seconds`)
- `tests/unit/lanes/tripadvisor/test_pagination.py` — FOUND
- Commits 149fcf9, 5593ba6, 0d88155 — FOUND in git log
