---
phase: 13-tripadvisor-real-listing-query-identify-wire-data-fetch-cont
verified: 2026-06-25T00:00:00Z
status: passed
score: 11/11 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: null
  previous_score: null
gaps: []
deferred:
  - truth: "Multi-page (oa30 via PaginationLinksList) attractions sweep"
    addressed_in: "Documented follow-up (ROADMAP Phase 13 goal: 'multi-page oa30 pagination are documented follow-ups')"
    evidence: "PAGINATION GAP code comment (client.py:304-307) + ROADMAP goal text explicitly defers oa30 pagination"
  - truth: "Coordless listing card → municipality geo-resolution (lat/lng absent → resolve_municipio fuzzy-matches attraction name → likely ibge_unmatched quarantine)"
    addressed_in: "Candidate follow-up phase"
    evidence: "13-03-SUMMARY.md 'Carry-forward risk' section flags attraction→municipality geo-resolution for coordless cards as a follow-up; per verification instructions this is a known documented carry-forward gap, not a phase blocker"
---

# Phase 13: TripAdvisor real listing query — wire data-fetch contract (GAP-12-A) Verification Report

**Phase Goal:** Close GAP-12-A — wire the TripAdvisor lane to the live-validated AttractionsFusion listing query (qid a5cb7fa004b5e4b5) with `request.routeParameters{geoId,contentType,webVariant,filters}` + sessionId, parsing `WebPresentation_SingleFlexCardSection` cards. Five sub-deliverables: (1) rebuild fetch_attractions; (2) fix _run_canary; (3) extend ta_bootstrap (capture TASID sessionId + listing qid, REJECT ad/telemetry/trips qids); (4) update TA-09 runbook; (5) Level-3 Nascente>0 (operator human-gate, approved this run).
**Verified:** 2026-06-25
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | fetch_attractions POSTs preRegisteredQueryId a5cb7fa004b5e4b5 + AttractionsFusion variables (routeParameters.contentType=attraction, sessionId threaded) | ✓ VERIFIED | `client.py:291` `_LISTING_QID="a5cb7fa004b5e4b5"` (hardcoded, not from session — qid-injection guard); `client.py:321-325` routeParameters.contentType="attraction", webVariant, geoId, filters; `client.py:338` `"sessionId": session_id`; `client.py:346` extensions.preRegisteredQueryId=_LISTING_QID |
| 2 | Parsed cards are dicts with keys name, locationId, rating, review_count, category from real response paths | ✓ VERIFIED | `client.py:126-179` `_parse_attractions_page` filters `__typename=="WebPresentation_SingleFlexCardSection"`, reads cardTitle.text / cardLink.webRoute.typedParams.detailId / bubbleRating.rating / bubbleRating.reviewCount / primaryInfo.text; sections extracted from `data[0]["data"]["Result"][0]["sections"]` (line 370) |
| 3 | ta_bootstrap extracts TASID from cookies and rejects telemetry/ad/trips qids with operator warning | ✓ VERIFIED | `ta_bootstrap.py:24-32` KNOWN_NON_LISTING_QIDS (5 qids); `:149-156` reject pass emits stderr warning + `continue` (not in query_ids); `:187` `session_id = cookies.get("TASID", "")`; `:295-302` main prints found/NOT FOUND |
| 4 | SessionInjectBody accepts optional session_id; Redis session dict includes session_id (auto-derived from TASID) | ✓ VERIFIED | `tripadvisor_session.py:75-82` session_id Field(default=None); `:222` `body.session_id or body.cookies.get("TASID") or ""`; `:228` stored in session dict; `:268` audit `session_id_present: bool` (value never logged) |
| 5 | _run_canary probes the real AttractionsFusion query (fetch_attractions, qid a5cb7fa004b5e4b5), not fetch_destinations; empty result → invalid_session | ✓ VERIFIED | `tripadvisor_session.py:139-142` `client.fetch_attractions(geo_id=303380, max_pages=1)`; SessionExpiredError/Timeout→422+delete (`:143-154`); infra→503 keep key (`:155-167`); empty result→422+delete (`:170-177`) |
| 6 | atrativos._ingest_one reads review_count (underscore), name, locationId, rating; most_recent_review_at=None | ✓ VERIFIED | `atrativos.py:144` `entity.get("review_count", 0)`; `:146` `most_recent_dt=None`; `:139` category; `:240` category in raw payload; grep `mostRecentReviewDate` = 0 matches |
| 7 | RUNBOOK-NIVEL3.md instructs capture on Attractions listing page + pick POST with SingleFlexCardSection | ✓ VERIFIED | `RUNBOOK-NIVEL3.md:53-62` Attractions-g294280/303380 URLs, SingleFlexCardSection identification, telemetry/ad skip instruction |
| 8 | README OPERATOR GATE reflects Attractions URL, listType:POI, TASID/session_id guidance | ✓ VERIFIED | `README:136-151` step 4 Attractions-g URL, step 5 SingleFlexCardSection + listType:POI + TASID login note, step 8 session_id:found |
| 9 | Level 3 acceptance (Nascente entity_type='attraction' > 0) defined + runnable | ✓ VERIFIED | `RUNBOOK-NIVEL3.md:137-141` CRITÉRIOS item 3 with psql query for entity_type='attraction' count > 0 |
| 10 | Level-3 live sweep (Nascente>0) operator human-gate satisfied | ✓ VERIFIED (human-gate) | Operator-approved this run (known_context); 13-03-SUMMARY.md Task 3 "operator-approved"; treated as human-verified per verification instructions |
| 11 | All offline tests pass without hitting TripAdvisor | ✓ VERIFIED | Targeted: 104 passed (lanes/tripadvisor + test_tripadvisor_session). Full offline: 418 passed, 5 skipped, 0 failures (RUN_REAL_EXTERNALS unset) |

**Score:** 11/11 truths verified

### Deferred Items

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | Multi-page oa30 pagination | Documented follow-up | PAGINATION GAP comment (client.py:304-307) + ROADMAP goal defers oa30 pagination |
| 2 | Coordless card → municipality geo-resolution (likely ibge_unmatched quarantine) | Candidate follow-up phase | 13-03-SUMMARY.md carry-forward risk section; known documented gap per verification instructions |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/lanes/tripadvisor/client.py` | fetch_attractions + AttractionsFusion vars + SingleFlexCardSection parser | ✓ VERIFIED | qid grep=3, SingleFlexCardSection grep=2; substantive (179-line parser + 120-line fetch); WIRED (imported by atrativos.produce + canary) |
| `brave/api/routers/tripadvisor_session.py` | SessionInjectBody.session_id + canary probes fetch_attractions | ✓ VERIFIED | session_id grep=6, 303380 grep=2; WIRED (router registered, inject_session calls _run_canary) |
| `scripts/ta_bootstrap.py` | TASID extraction + qid reject list | ✓ VERIFIED | KNOWN_NON_LISTING_QIDS + LISTING_QID present; reject pass + session_id extraction wired into parse_curl |
| `brave/lanes/tripadvisor/atrativos.py` | Card-field mapping to normalized dict | ✓ VERIFIED | review_count underscore, most_recent=None, category stored; WIRED via produce→_ingest_one |
| `data/tripadvisor/RUNBOOK-NIVEL3.md` | Attractions-g capture + Level-3 acceptance | ✓ VERIFIED | Attractions-g + SingleFlexCardSection + TASID + entity_type='attraction' gate |
| `data/tripadvisor/README` | OPERATOR GATE Attractions URL + TASID | ✓ VERIFIED | Attractions-g + SingleFlexCardSection + listType:POI + session_id |
| `tests/unit/lanes/tripadvisor/test_client.py` | AttractionsFusion + Bootstrap reject tests | ✓ VERIFIED | TestTripAdvisorAttractionsFusionContract (:678), TestBootstrapQueryIdRejectList (:829) |
| `tests/unit/lanes/tripadvisor/test_atrativos.py` | Card-field mapping tests | ✓ VERIFIED | TestAtrativosIngestCardFields (:93) |
| `tests/unit/api/test_tripadvisor_session.py` | session_id + canary-probe tests | ✓ VERIFIED | test_inject_session_stores_session_id (:292), test_canary_probes_fetch_attractions (:444) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| client.py fetch_attractions | graphql/ids POST | httpx POST with variables.sessionId + qid a5cb7fa004b5e4b5 | ✓ WIRED | Payload built lines 313-348; sessionId=session["session_id"]; qid hardcoded |
| client.py _parse_attractions_page | data.Result[0].sections[] | filter __typename==SingleFlexCardSection | ✓ WIRED | Lines 145/370 — sections path + typename filter both present |
| _run_canary | fetch_attractions | asyncio.wait_for max_pages=1 | ✓ WIRED | Line 139-142; test_canary_probes_fetch_attractions asserts fetch_destinations is NOT called |
| atrativos._ingest_one | TripAdvisorReviewSignals | review_count/rating, most_recent_review_at=None | ✓ WIRED | Lines 144-153 |
| RUNBOOK Passo 1 | Attractions-g<geoId> page | operator navigates listing, not Tourism page | ✓ WIRED | RUNBOOK:53-62 explicit Attractions-g URLs + "NÃO a página Tourism" |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| fetch_attractions | `results` (cards) | live graphql/ids POST → `data[0].data.Result[0].sections` | Yes (live-validated 2026-06-24: HTTP 200, 314 KB, 30 FlexCards, Iguazu Falls detailId 312332) | ✓ FLOWING |
| atrativos._ingest_one | `payload` → store_raw | normalized card dict from fetch_attractions | Yes — operator-confirmed Nascente>0 this run (human-gate) | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Targeted offline suite (lane + session) | `pytest tests/unit/lanes/tripadvisor/ tests/unit/api/test_tripadvisor_session.py` | 104 passed | ✓ PASS |
| Full offline suite (RUN_REAL_EXTERNALS unset) | `pytest tests/unit/` | 418 passed, 5 skipped, 0 failures | ✓ PASS |
| client.py contains real qid | `grep -c a5cb7fa004b5e4b5 client.py` | 3 (≥1) | ✓ PASS |
| canary probes fetch_attractions geoId | `grep 303380 tripadvisor_session.py` | line 140 | ✓ PASS |
| atrativos no stale camelCase recency | `grep mostRecentReviewDate atrativos.py` | 0 matches | ✓ PASS |
| Live data-fetch (real graphql/ids) | Operator Level-3 sweep | Approved (human-gate) | ? SKIP→human (already satisfied this run) |

### Probe Execution

No `scripts/*/tests/probe-*.sh` declared or conventional for this phase. Live data-fetch verification is the operator Level-3 human-gate (RUNBOOK-NIVEL3.md), not a scripted probe — already approved this run.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| TA-12 | 13-01, 13-02, 13-03 | Client refactor / data-fetch correctness — fix the persisted-query payload to real `{variables, extensions.preRegisteredQueryId}` shape; extends Phase 12 | ✓ SATISFIED | fetch_attractions rebuilt around real qid+variables+sections parse (truths 1-2); canary fixed (5); atrativos mapping (6); ta_bootstrap TASID+reject (3); runbook (7-9). REQUIREMENTS.md maps TA-12 to Phase 12 (marked Complete) but Phase 13 explicitly extends it (ROADMAP: "TA-12 — data-fetch correctness — extends Phase 12"); no orphaned requirement |

No orphaned requirements: REQUIREMENTS.md Phase 13 maps only TA-12, which all 3 plans declare.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| brave/lanes/tripadvisor/atrativos.py | 158 | `completude_from_fields(entity, …)` fed raw camelCase card dict (`locationId`, no `uf`/`lat`/`lng`/`address`/`description`/`location_id`) → only 4/10 fields match → completude_value permanently 40.0 (WR-01) | ⚠️ Warning | Depresses every attraction §7.6 score by a fixed ~12 pts. Scoring-QUALITY defect, NOT a data-fetch goal failure — records still reach Nascente. Out-of-scope quality follow-up for a phase whose goal is data-fetch wiring + Nascente>0. |
| brave/lanes/tripadvisor/client.py | 382-384 | Dead `len(cards)<30` partial-page guard + `if not sections: break` in single-page-default fetch (WR-02 review) | ⚠️ Warning | Latent duplicate-ingestion risk only if a future caller passes max_pages>1 (identical payload re-POSTs page 1). Default path (max_pages=None→1) is correct. Robustness follow-up. |
| brave/api/routers/tripadvisor_session.py | 146,171 | redis.delete inside except can raise → 500 instead of 422 (WR-03) | ⚠️ Warning | Edge-case (Redis blip during cleanup). Canary classification itself is correct + tested. Robustness follow-up. |
| brave/api/routers/tripadvisor_session.py | 250-286 | Audit calls get_db() directly, bypassing FastAPI override; silently skipped when BRAVE_DB_URL unset (WR-04) | ⚠️ Warning | Compliance-audit gap, not a data-fetch goal item. Pre-existing pattern; inject still succeeds. Follow-up. |
| scripts/ta_bootstrap.py | 158-176 | qid classified by ATTRACTION substring heuristic, not LISTING_QID constant (WR-05) | ⚠️ Warning | query_ids["attractions"] is dead (client hardcodes qid), so no fetch impact; only operator-facing print could mislead. Follow-up. |

No 🛑 Blockers. No debt markers (TBD/FIXME/XXX = 0 in all phase-modified files). The 5 Warnings (matching 13-REVIEW.md) are quality/robustness follow-ups that do not block the phase goal (data-fetch wiring + Nascente>0 capability).

### Human Verification Required

None outstanding. The single human-gate item — Level-3 live sweep confirming Nascente entity_type='attraction' count > 0 — was an operator checkpoint (13-03 Task 3, `checkpoint:human-verify`) that was **approved by the operator this run** (per known_context and 13-03-SUMMARY.md "operator-approved"). Treated as already satisfied; no new human verification is requested.

### Gaps Summary

No gaps blocking the phase goal. All 11 observable truths verified against the actual source (not SUMMARY claims): fetch_attractions is rebuilt around the live-validated AttractionsFusion qid a5cb7fa004b5e4b5 with the real routeParameters+sessionId variables and a SingleFlexCardSection parser; _run_canary probes the real query; ta_bootstrap extracts TASID and rejects the 5 telemetry/ad/trips qids; atrativos consumes the normalized card shape; the TA-09 runbook + README point operators at the Attractions listing page; and the Level-3 Nascente>0 operator gate was approved this run. Full offline suite is green (418 passed / 5 skipped / 0 failures, RUN_REAL_EXTERNALS unset).

WR-01 (completude permanently capped at 40.0) is the most material code-review finding but is a §7.6 **scoring-quality** defect — it does not prevent records from being fetched, parsed, or written to Nascente, which is this phase's goal. It and the other 4 Warnings are recorded as out-of-scope quality/robustness follow-ups. The lat/lng→municipality fuzzy-match quarantine risk and oa30 multi-page pagination are documented carry-forward items (deferred), explicitly not phase blockers per the phase scope.

---

_Verified: 2026-06-25_
_Verifier: Claude (gsd-verifier)_
