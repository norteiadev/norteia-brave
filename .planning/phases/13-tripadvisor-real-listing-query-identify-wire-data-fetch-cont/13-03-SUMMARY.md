---
phase: 13-tripadvisor-real-listing-query-identify-wire-data-fetch-cont
plan: 03
subsystem: docs
tags: [tripadvisor, runbook, operator-gate, attractionsfusion, tasid]

requires:
  - phase: 13-01
    provides: fetch_attractions wired to AttractionsFusion qid a5cb7fa004b5e4b5 + TASID session_id
  - phase: 13-02
    provides: canary probes fetch_attractions; atrativos._ingest_one reads normalized card shape
provides:
  - RUNBOOK-NIVEL3.md Passo 1 corrected to the Attractions-g<geoId> listing page
  - SingleFlexCardSection POST-identification guidance (RUNBOOK + README)
  - TASID / session_id login requirement + missing-TASID troubleshooting
  - Level 3 acceptance criteria (Nascente entity_type='attraction' > 0)
affects: [operator-onboarding, phase-verification, tripadvisor-level3]

tech-stack:
  added: []
  patterns: [operator-runbook reflects live-validated capture flow]

key-files:
  created: []
  modified:
    - data/tripadvisor/RUNBOOK-NIVEL3.md
    - data/tripadvisor/README

key-decisions:
  - "Operator must capture on Attractions-g<geoId> listing page, not the Tourism state page (Tourism fires only telemetry/ad qids)."
  - "Correct listing POST identified by WebPresentation_SingleFlexCardSection in the Response; telemetry/ad/trips requests skipped."
  - "TASID cookie (→ session_id) requires the operator be logged in; ta_bootstrap warns 'session_id: NOT FOUND' when absent."
  - "Level 3 acceptance refined to entity_type='attraction' count > 0, not bare source count."

patterns-established:
  - "Runbook acceptance gate mirrors the §7.6 Nascente outcome, not just an HTTP 'started' response."

requirements-completed: [TA-12]

duration: 12min
completed: 2026-06-25
---

# Phase 13 / Plan 03: Operator runbook + README rewired for the real AttractionsFusion listing query

**TA-09 runbook and TripAdvisor README now point operators at the Attractions listing page and the SingleFlexCardSection POST, with TASID/session_id guidance and a Nascente>0 Level-3 acceptance gate.**

## Performance

- **Duration:** ~12 min
- **Tasks:** 3 (2 doc tasks auto; 1 human-verify checkpoint — operator-approved)
- **Files modified:** 2

## Accomplishments

- **Task 1 — RUNBOOK-NIVEL3.md:** header now cites Fase 13 data-fetch contract (qid a5cb7fa004b5e4b5); Passo 1 capture URL changed from `Tourism-g303380-...` to the `Attractions-g<geoId>-Activities-...` listing page (national 294280 + MG 303380 examples); added SingleFlexCardSection POST-identification + TASID login note; Passo 2 expected output now shows `session_id: found`; CRITÉRIOS item 3 refined to `entity_type='attraction'` count check; two new TROUBLESHOOTING entries (wrong-qid, missing-TASID).
- **Task 2 — README OPERATOR GATE:** step 4 → Attractions listing page; step 5 → SingleFlexCardSection/`listType:POI` identification + TASID login note; step 8 → `session_id: found` expected output; CURRENT FILES notes geoId reuse as `routeParameters.geoId` in AttractionsFusion. LEGAL RISK / MITIGATIONS / LGPD sections unchanged.
- **Task 3 — Level 3 human checkpoint:** operator-approved.

## Verification

- `grep -c SingleFlexCardSection RUNBOOK-NIVEL3.md` = 3 (≥1)
- `grep -c Attractions-g RUNBOOK-NIVEL3.md` = 4 (≥1)
- `grep -c TASID RUNBOOK-NIVEL3.md` = 6 (≥1)
- `grep -c Attractions-g README` = 1 (≥1)
- `grep -c session_id README` = 3 (≥1)

## Carry-forward risk (out of scope for phase 13)

AttractionsFusion listing cards carry no lat/lng; `_ingest_one` resolves each attraction to a municipality by fuzzy-matching the **attraction name** against IBGE municipality names (`resolve_municipio`, threshold 88), with haversine fallback only when coords exist. Real attraction names rarely match municipality names → `ibge_unmatched` / `parent_destino_absent` quarantine is the likely dominant outcome of a real sweep. The 13-01/13-02 fetch wiring is correct; attraction→municipality geo-resolution for coordless listing cards is a candidate follow-up phase, flagged at the Level-3 checkpoint.
