---
phase: 15-tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
plan: 01
subsystem: testing
tags: [tripadvisor, fixture, pagination, oa30, html, datadome]

requires:
  - phase: 13
    provides: real AttractionsFusion listing contract (qid a5cb7fa004b5e4b5, FlexCard sections)
  - phase: 12
    provides: operator session-injection seam + capture runbook
provides:
  - Scrubbed offline HTML fixture of the all-Brazil -oa30- listing page (page 2)
  - Wave-0 unblock for the embedded-JSON extractor (15-04)

affects: [15-04, 15-06]

tech-stack:
  added: []
  patterns: ["offline HTML fixture for SSR-embedded card JSON; secret-scrub grep gate before commit"]

key-files:
  created: [tests/fixtures/tripadvisor/attractions_oa30.html]
  modified: [data/tripadvisor/README]

key-decisions:
  - "Captured the real -oa30- HTML page (not a GraphQL XHR) — the HTML DataDome surface is the one 15-04 parses."
  - "Confirmed live: HTML page returns 200 with the full operator cookie jar (not 403) — contradicts the older HTML-walled finding."

patterns-established:
  - "Secret-scrub: redact every cookie value + datadome/Set-Cookie/__Secure-/sessionid patterns; grep gate blocks any secret-bearing fixture."

requirements-completed: [TA-12]

duration: 5min
completed: 2026-06-26
---

# Phase 15 — Plan 01: Wave-0 oa30 HTML fixture Summary

**Captured and scrubbed the real all-Brazil `-oa30-` AttractionsFusion HTML page as a committed offline fixture, unblocking the embedded-JSON extractor (15-04).**

## What was done
- Fetched `https://www.tripadvisor.com/Attractions-g294280-Activities-a_allAttractions.true-oa30-Brazil.html`
  with the live operator cookie jar → **HTTP 200, 1.51 MB**.
- Scrubbed: every cookie value redacted, plus `datadome=`/`Set-Cookie`/`__Secure-`/`sessionid=`
  patterns and operator email. Zero residual secret strings.
- Saved `tests/fixtures/tripadvisor/attractions_oa30.html` (1,512,516 bytes).
- Appended a Wave-0 capture note to `data/tripadvisor/README` (HTML-navigation capture, not GraphQL-only; scrub requirement).

## Verification
Plan `<verify>` gate passed: fixture exists (>100 KB), contains `WebPresentation_SingleFlexCardSection` (60×)
and `cardTitle` (30×), and `grep -Eiq 'datadome=|set-cookie|__secure-|sessionid='` returns NO match → `FIXTURE_OK`.

## must_haves
- ✅ Real captured `-oa30-` page exists offline, scrubbed of cookies/PII.
- ✅ Embeds the 30 FlexCard sections the extractor must recover.
- ✅ No datadome/session cookie value or Set-Cookie header survives in the committed fixture.

## Notes for downstream
- The page carries 30 organic FlexCards (page 2 of the listing); `_parse_attractions_page` shape applies.
- 15-04 should load this fixture in `tests/unit/lanes/tripadvisor/test_pagination.py` for the extractor unit test.
