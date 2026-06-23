---
phase: 11-tripadvisor-source-lane-graphql-scraper
plan: "05"
subsystem: tripadvisor-compliance-docs
tags: [tripadvisor, lgpd, compliance, docs, sources, legal-risk]
dependency-graph:
  requires:
    - "11-02" (destinos.py + atrativos.py producer files must exist)
  provides:
    - data/tripadvisor/README (legal-risk register: ToS, mitigations, LGPD basis, operator gate)
    - SOURCES.md (root index of all pipeline data sources)
    - brave/lanes/tripadvisor/destinos.py module docstring (ToS + LGPD + operator-gate warning)
    - brave/lanes/tripadvisor/atrativos.py module docstring (same + no-WhatsApp note)
  affects: []
tech-stack:
  added: []
  patterns:
    - Five-section data/README convention (SOURCE, LEGAL RISK, MITIGATIONS, LGPD BASIS, OPERATOR GATE)
    - Root SOURCES.md index pattern (source / requirements / origem_value / license / cost / notes)
    - Module-level compliance docstring pattern for ToS-violation lanes
key-files:
  created:
    - data/tripadvisor/README (legal-risk note: ToS, mitigations, LGPD basis, opt-in gate, CURRENT FILES)
    - SOURCES.md (root pipeline source index — mtur, places, tripadvisor rows)
  modified:
    - brave/lanes/tripadvisor/destinos.py (module docstring expanded with ToS warning, LGPD, operator-gate)
    - brave/lanes/tripadvisor/atrativos.py (module docstring expanded with ToS warning, LGPD, operator-gate, no-WhatsApp)
decisions:
  - "data/tripadvisor/README mirrors data/ibge/README five-section style rather than data/mtur/README seven-section style — TripAdvisor has no CSV schema to document"
  - "SOURCES.md uses origem_value column (not 'Origin Score') to match the pipeline's internal field name"
  - "atrativos.py docstring adds explicit NO WHATSAPP OUTREACH section to make the promote_override-only promotion path unambiguous at the module level"
metrics:
  duration: "~5min"
  completed: "2026-06-23"
  tasks: 1
  files: 4
requirements_completed:
  - TA-08
---

# Phase 11 Plan 05: Compliance Docs Summary

**One-liner:** TripAdvisor legal-risk register (five-section README with ToS warning, mitigations, LGPD legitimate-interest basis, operator gate), root SOURCES.md pipeline index, and ToS/LGPD/operator-gate module docstrings for destinos and atrativos producers.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | data/tripadvisor/README + root SOURCES.md + lane docstrings | f0d468d | data/tripadvisor/README, SOURCES.md, brave/lanes/tripadvisor/destinos.py, brave/lanes/tripadvisor/atrativos.py |

## What Was Built

### data/tripadvisor/README

Five mandatory sections (TA-08 requirement):

1. **SOURCE** — TripAdvisor public website, GraphQL persisted-query endpoint (`https://www.tripadvisor.com/data/graphql/ids`), Playwright + DataDome session bootstrap, no API key. Also documents the single current file: `uf_geoids.json`.

2. **LEGAL RISK** — Explicit statement that TripAdvisor ToS Section 5 prohibits systematic scraping and that this source constitutes a ToS violation. Enumerates IP bans, cease-and-desist risk, account suspension.

3. **MITIGATIONS** — Five named mitigations: (a) low request rate (one UF/min), (b) residential proxy seam (`config.proxy_url`), (c) no PII stored (`review_count`, `rating`, `most_recent_review_at` only — `extra="forbid"` on `TripAdvisorReviewSignals`), (d) operator-gated (not on Celery beat), (e) human promote-override before Mar.

4. **LGPD BASIS** — Legitimate interest (Art. 7º, IX, LGPD); data minimisation via `TripAdvisorReviewSignals.extra="forbid"`; no data-subject rights obligations (no personal data collected).

5. **OPERATOR GATE** — Step-by-step checklist: set `RUN_REAL_EXTERNALS=1`, install `norteia-brave[scraper]` + Playwright chromium, optionally configure `BRAVE_TA_PROXY_URL`, POST `/api/v1/engine/start` with `source="tripadvisor"`. Explicit "NOT scheduled on the Celery beat" note.

### SOURCES.md (repo root)

Markdown table with columns: Source / Requirements / origem_value / License-Terms / Cost / Notes.

| Source | Requirements | origem_value | Notes |
|--------|-------------|:---:|-------|
| mtur | DEST-01 | 100 | Official BR gov data (CC-BY), free, bundled CSV |
| places | ATR-02/03/04 | 60 | Google ToS, ~USD 0.003/req, first-party validated |
| tripadvisor | TA-01/02 | 65 | ToS violation — operator-gated, LGPD-safe aggregate, human promote-override required |

Includes a preamble note explaining the "ToS-violation / operator-gated" posture for documentation consumers.

### brave/lanes/tripadvisor/destinos.py — module docstring

Expanded from the original four-line implementation note to include:
- "This lane scrapes TripAdvisor via the GraphQL hybrid client" context line
- ToS WARNING referencing `data/tripadvisor/README`
- LGPD section (aggregate fields only, `extra="forbid"` enforcement)
- OPERATOR GATE section (RUN_REAL_EXTERNALS, explicit engine start)
- Original D-04 / D-18 implementation notes preserved

### brave/lanes/tripadvisor/atrativos.py — module docstring

Same as destinos.py, with the addition of:
- NO WHATSAPP OUTREACH section: TA attractions never enter WhatsApp outreach — review-signal validated only; promotion to Mar requires human steward `promote_override` action; no automated Mar push path.

## Deviations from Plan

None — plan executed exactly as written.

The docstrings are prepended/replacing the existing module docstrings (not inserting before imports) to match Python convention and the existing destinos.py / atrativos.py file structure.

## Known Stubs

None — this is a documentation-only plan. No data flows or UI rendering are involved.

## Threat Flags

No new threat surface introduced. This plan only adds documentation files and expands docstrings — no new network endpoints, no new auth paths, no schema changes.

Threat register from plan verified:

| Threat | Disposition | Status |
|--------|-------------|--------|
| T-11-05-01 (Information Disclosure — README legal-risk exposure) | accept | README is a risk-acknowledgement document; making risk explicit is the correct posture; no credentials or PII exposed |
| T-11-05-02 (Tampering — SOURCES.md accuracy drift) | accept | Human-maintained index; accuracy by convention; low-frequency; acceptable for internal ops tool |

## Self-Check: PASSED

Verified created files exist:
- [x] data/tripadvisor/README — FOUND
- [x] SOURCES.md — FOUND
- [x] brave/lanes/tripadvisor/destinos.py (modified) — FOUND
- [x] brave/lanes/tripadvisor/atrativos.py (modified) — FOUND

Verified commits exist:
- [x] f0d468d — docs(11-05): TA-08 compliance — tripadvisor README, SOURCES.md, lane docstrings

Acceptance criteria verified:
- [x] test -f data/tripadvisor/README — EXIT 0
- [x] test -f SOURCES.md — EXIT 0
- [x] grep -q "Terms of Service\|ToS" data/tripadvisor/README — EXIT 0
- [x] grep -q "LGPD\|legitimate interest" data/tripadvisor/README — EXIT 0
- [x] grep -q "RUN_REAL_EXTERNALS\|operator" data/tripadvisor/README — EXIT 0
- [x] grep -q "review_count.*rating\|aggregate" data/tripadvisor/README — EXIT 0
- [x] grep -c "mtur\|places\|tripadvisor" SOURCES.md — returns 3 (one line per source)
- [x] grep -q "ToS\|scraping" brave/lanes/tripadvisor/destinos.py — EXIT 0
- [x] grep -q "WhatsApp" brave/lanes/tripadvisor/atrativos.py — EXIT 0
