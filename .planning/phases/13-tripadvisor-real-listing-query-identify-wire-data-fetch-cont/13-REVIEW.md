---
phase: 13-tripadvisor-real-listing-query
reviewed: 2026-06-25T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - brave/lanes/tripadvisor/client.py
  - brave/api/routers/tripadvisor_session.py
  - brave/lanes/tripadvisor/atrativos.py
  - scripts/ta_bootstrap.py
  - brave/clients/base.py
  - brave/clients/null_tripadvisor.py
findings:
  critical: 0
  warning: 5
  info: 4
  total: 9
status: issues_found
---

# Phase 13: Code Review Report

**Reviewed:** 2026-06-25
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

Reviewed the Phase 13 TripAdvisor real-listing-query wiring: `fetch_attractions`
rewired to the AttractionsFusion persisted query (qid `a5cb7fa004b5e4b5`), the
new `_parse_attractions_page` SingleFlexCard parser, the session-injection
`session_id`/TASID threading, `_run_canary` retargeted to `fetch_attractions`,
the `atrativos._ingest_one` normalized-card field mapping, and the `ta_bootstrap`
reject-list. The security posture is solid: the qid is hardcoded (no
session-injection of a stale/telemetry qid), `session_id`/cookie values are kept
out of logs and audit (presence-as-boolean only), the bootstrap helper never
prints raw 422 bodies, the 64 KB body guard runs before parse, and the LGPD
boundary (`TripAdvisorReviewSignals` with `extra="forbid"`, no reviewer
names/IDs/text extracted) is intact. No Critical defects found.

Five Warnings concern correctness/robustness: a `completude_from_fields`
field-name mismatch that systematically caps attraction completude at 40, dead
partial-page pagination logic in the single-page-only `fetch_attractions`, a
canary key-deletion path that can throw a non-page-error past the WR-02 guard, a
brittle direct `get_db()` call that bypasses FastAPI's dependency override, and a
`ta_bootstrap` qid-classification heuristic that can misclassify the listing qid.

## Warnings

### WR-01: completude_from_fields silently caps attraction completude at 40

**File:** `brave/lanes/tripadvisor/atrativos.py:158` (with `brave/lanes/tripadvisor/scoring.py:112-141`)
**Issue:** `_ingest_one` passes the raw normalized card dict to
`completude_from_fields(entity, cap=100)`. The normalized card produced by
`_parse_attractions_page` only has keys `name`, `locationId`, `rating`,
`review_count`, `category`. But `_TA_COMPLETUDE_FIELDS` checks
`["name", "uf", "location_id", "lat", "lng", "rating", "review_count", "address", "category", "description"]`.
The card uses `locationId` (camelCase) so `location_id` never matches; `uf`,
`lat`, `lng`, `address`, `description` are never present in a listing card. Only
4 of 10 fields ever match, so a *fully populated* AttractionsFusion attraction is
permanently scored `completude_value == 40.0`, never higher. This depresses every
attraction's §7.6 score by a fixed ~12 points (`(100-40)*0.20`) and is invisible
because no test asserts the completude value for the real card shape.
**Fix:** Either add `uf` and `location_id` to the entity before scoring, or score
against the keys the card actually carries:
```python
# in _ingest_one, before completude_from_fields:
completude_entity = {
    **entity,
    "uf": uf,
    "location_id": location_id,
    "lat": lat,
    "lng": lng,
}
completude_value = completude_from_fields(completude_entity, cap=100)
```
Add a test asserting `completude_value` for a complete card so the cap regression
cannot recur silently.

### WR-02: Dead/misleading partial-page pagination guard in single-page fetch_attractions

**File:** `brave/lanes/tripadvisor/client.py:382-384`
**Issue:** `fetch_attractions` defaults to `page_limit = 1` (PAGINATION GAP — the
payload carries no page/offset param, so looping would re-POST page 1). The
`if len(cards) < 30: break` partial-page check and the `if not sections: break`
loop-termination logic are therefore dead for the default path, and actively
*wrong* if a caller ever passes `max_pages > 1`: every iteration sends the
identical payload (same `routeParameters`, no offset), so pages 2..N would
duplicate page 1's cards into `results` until a full 30-card page coincidentally
stops the partial-page guard. The `pageview_uid` is regenerated per loop but does
not change the result set. This is latent duplicate-ingestion risk masked by the
`max_pages=None → 1` default.
**Fix:** Make the duplication impossible rather than relying on the default.
Either hard-cap to one page until real pagination lands:
```python
# AttractionsFusion has no confirmed page param — one page only.
# Ignore max_pages>1 to prevent duplicate-page ingestion (see PAGINATION GAP).
del max_pages  # documented single-page contract
results = []
# ... single request, no loop ...
```
or raise `NotImplementedError` when `max_pages and max_pages > 1`. Remove the
`< 30` partial-page break (or document the literal 30 as a constant) since it has
no correct meaning for a non-paginating query.

### WR-03: Canary key-delete path can raise past the WR-02 "do not delete" guard

**File:** `brave/api/routers/tripadvisor_session.py:143-167`
**Issue:** The canary intends: provably-bad session (`SessionExpiredError` /
`TimeoutError`) → delete key + 422; infrastructure fault → keep key + 503. But
`fetch_attractions` resolves `geo_id` directly (no `resolve_geo_id` call, good),
yet `_get_session()` raises `SessionMissingError` if the key vanished, and
`redis.delete(BRAVE_TA_SESSION_KEY)` / `redis.json` operations inside the `except`
blocks can themselves raise (Redis blip) *after* the classification decision.
Concretely, if the `SessionExpiredError` branch's `redis.delete(...)` (line 146)
throws, the exception propagates as a raw 500 instead of the intended 422, and
the freshly-injected key may be left half-handled. The infra-vs-bad-session
contract is asserted by classification but not protected against a delete failing.
**Fix:** Guard the deletion so a Redis failure during cleanup cannot convert a
classified 422 into an unhandled 500:
```python
try:
    redis.delete(BRAVE_TA_SESSION_KEY)
except Exception:
    logger.warning("ta_session_canary_delete_failed")
raise HTTPException(status_code=422, detail="invalid_session") from exc
```
Apply the same wrap to the empty-result delete at line 171.

### WR-04: inject_session audit calls get_db() directly, bypassing FastAPI overrides

**File:** `brave/api/routers/tripadvisor_session.py:249-286`
**Issue:** The audit block does `from brave.api.deps import get_db; db_gen = get_db()`
and `next(db_gen)` rather than receiving a session via `Depends`. This bypasses
FastAPI's dependency-override mechanism: in tests `app.dependency_overrides[get_db]`
is set, but this direct call invokes the *original* generator, which raises
`RuntimeError("BRAVE_DB_URL not set...")` and is then swallowed by the broad
`except Exception`. So the audit is silently skipped in any environment where
`BRAVE_DB_URL` is unset — including potentially staging — and the test's
`get_db` override is a no-op for this path (the test passes only because the
`RuntimeError` is caught, not because the override took effect). The audit
record (`ta_session_injected`) is a compliance artifact; silently dropping it on
a DB hiccup is a gap.
**Fix:** Inject the DB session as a normal dependency
(`db: Session | None = Depends(get_optional_db)`) so overrides apply and audit
failures are observable, or at minimum log at WARNING (not swallow) when the
audit session cannot be obtained, distinguishing "no DB configured" from
"audit write failed".

### WR-05: ta_bootstrap qid classification can mislabel the listing qid as a destination

**File:** `scripts/ta_bootstrap.py:158-176`
**Issue:** The heuristic classifies a captured qid as `attractions` only if
`"ATTRACTION"` appears (case-insensitively) in the JSON-serialized `variables`.
The real AttractionsFusion listing payload carries
`"contentType": "attraction"` and `"webVariant": "AttractionsFusion"` — both
contain the substring `ATTRACTION`, so the happy path works. But if an operator
captures a listing request whose variables were trimmed/minified differently (or
a future variant uses `"contentType":"poi"` / a localized value), the qid falls
through to the `destinations` slot, and the single-qid fallback
(lines 168-171) then copies it into `attractions` too — meaning a non-listing or
mislabeled qid can still populate `query_ids["attractions"]`. Since the real
client hardcodes the listing qid anyway, `query_ids["attractions"]` is dead for
fetch, but the operator-facing `Parsed: ... query_ids={...}` print (line 294) can
falsely reassure the operator that they captured the listing query.
**Fix:** Classify positively on the listing qid constant rather than a substring
heuristic:
```python
if qid == LISTING_QID:
    attractions_qid = qid
elif "ATTRACTION" in variables_str and attractions_qid is None:
    attractions_qid = qid
```
and have `main()` print an explicit "listing qid confirmed / NOT the known
listing qid" line so the operator sees ground truth, not a heuristic guess.

## Info

### IN-01: Magic number 30 for page-size partial-page detection

**File:** `brave/lanes/tripadvisor/client.py:382`
**Issue:** `if len(cards) < 30` hardcodes the assumed AttractionsFusion page size.
The destinations path uses `20` (lines 261, 229). Neither is a named constant.
**Fix:** Define `_ATTRACTIONS_PAGE_SIZE = 30` / `_DESTINATIONS_PAGE_SIZE = 20`
constants alongside `_MAX_PAGES` and reference them, so the assumption is
documented and greppable.

### IN-02: Broad `except Exception` in _parse_attractions_page logs only the class name

**File:** `brave/lanes/tripadvisor/client.py:176-178`
**Issue:** Malformed cards are skipped with `logger.debug(..., error=type(exc).__name__)`.
This is correct for resilience and avoids leaking card content, but at debug level
with only the exception class it gives no signal when a *systematic* schema change
breaks every card (the function would silently return `[]` and pagination would
stop as "end of data"). A real qid/schema rotation would look identical to "no
attractions in this geo."
**Fix:** Count skipped-vs-parsed cards and emit one summary log per page at info
when the skip ratio is high (e.g. all sections skipped but sections were present),
so a schema break is distinguishable from an empty geo.

### IN-03: Inconsistent fetch_destinations vs fetch_attractions response-shape handling

**File:** `brave/lanes/tripadvisor/client.py:251-256` vs `367-372`
**Issue:** `fetch_destinations` handles both list-wrapped and dict response shapes;
`fetch_attractions` only handles the list-wrapped shape
(`data[0]["data"]["Result"][0]["sections"]`) and treats a dict response as empty.
If TripAdvisor ever returns a non-batch dict for the attractions endpoint, the
canary would report `invalid_session` (empty result) and delete a valid session.
**Fix:** Mirror the dict/list dual-shape extraction used by `fetch_destinations`,
or document why the attractions endpoint is guaranteed batch-array-only.

### IN-04: TripAdvisorClientProtocol.fetch_destinations omits the max_pages param it accepts

**File:** `brave/clients/base.py:247` vs `brave/lanes/tripadvisor/client.py:185-186`
**Issue:** The protocol declares `fetch_destinations(self, uf: str)` with no
`max_pages`, but the real client (and the canary/WR-06 path) calls it with
`max_pages=...`. `FakeTripAdvisorClient.fetch_destinations` also omits it. Calls
work at runtime (structural typing, keyword arg), but the protocol no longer
describes the true contract, and a type checker treating the protocol as the
source of truth would flag `max_pages=1`. Phase 13 updated
`fetch_attractions` in the protocol but left `fetch_destinations` stale.
**Fix:** Add `max_pages: int | None = None` to
`TripAdvisorClientProtocol.fetch_destinations` and to
`FakeTripAdvisorClient.fetch_destinations` for consistency with the attractions
signature already updated this phase.

---

_Reviewed: 2026-06-25_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
