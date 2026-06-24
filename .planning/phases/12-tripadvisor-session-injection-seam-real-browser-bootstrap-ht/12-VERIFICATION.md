---
phase: 12-tripadvisor-session-injection-seam-real-browser-bootstrap-ht
verified: 2026-06-24T16:00:00Z
status: human_needed
score: 22/22 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run scripts/ta_bootstrap --help on the server, then paste a real DevTools cURL string interactively"
    expected: "Script prints 'Parsed: N cookies, query_ids={...}' with the correct keys, then attempts injection"
    why_human: "parse_curl is tested by unit tests but end-to-end cURL-string parsing from actual DevTools output requires a real browser capture to exercise the exact quoting/escaping path"
  - test: "POST /api/v1/tripadvisor/session with a real DataDome session from a logged-in browser"
    expected: "Returns {status: 'ready', canary: 'ready'} and GET /session/status shows present=true"
    why_human: "DataDome blocks automated browsers from datacenter/home IPs; the canary runs a live httpx call to TripAdvisor — cannot be verified offline without RUN_REAL_EXTERNALS and a real session"
  - test: "Start engine with source=tripadvisor (after injecting a real session), let one UF sweep run"
    expected: "EngineControl pill shows 'Pronta' before sweep, then 'Precisa bootstrap' if session expires mid-sweep; pipeline ingests non-zero records into Nascente"
    why_human: "Full sweep operability depends on real DataDome cookies, proxy configuration, and TripAdvisor data availability — cannot be exercised in CI"
---

# Phase 12: TripAdvisor Session-Injection Seam Verification Report

**Phase Goal:** Make the TripAdvisor lane actually collect data by splitting session acquisition (operator-gated, real browser) from bulk fetch (httpx). A new POST /api/v1/tripadvisor/session endpoint (steward/bearer auth, Pydantic-validated, size-limited, cookie-redacted) writes an operator-captured session into Redis (BRAVE_TA_SESSION_KEY); a canary gate validates it through the production httpx path (ready vs invalid_session, key deleted on fail); GET /tripadvisor/session/status surfaces health. The client reads the injected session only (SessionMissingError on miss), the persisted-query payload is corrected to extensions.preRegisteredQueryId (batch-array format), and the Playwright _bootstrap_session + scraper dependency are removed. sweep_tripadvisor fails fast on a missing/expired/stale session (needs_bootstrap, no retry-storm) and surfaces session state to the dashboard. Operator-gated best-effort, NOT a 24/7 autonomous lane.
**Verified:** 2026-06-24T16:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Operator has step-by-step instructions to capture a TripAdvisor session via DevTools Copy-as-cURL | VERIFIED | `data/tripadvisor/README` OPERATOR GATE section (lines 118-197): acquisition steps 1-6, injection steps 7-9, sweep steps 10-12, session lifetime note |
| 2 | `scripts/ta_bootstrap.py` parses cURL string and POSTs to POST /api/v1/tripadvisor/session | VERIFIED | File exists, stdlib-only (argparse, json, re, os, datetime, urllib.request), `parse_curl()` extracts cookies + user_agent + preRegisteredQueryId from batch-array, `inject_session()` POSTs with Bearer auth; `--help` works |
| 3 | `scripts/ta_bootstrap` (shell entry) is executable and delegates to ta_bootstrap.py | VERIFIED | File exists at `scripts/ta_bootstrap`, permissions `-rwxr-xr-x`, 4-line shell wrapper with `exec python "$(dirname "$0")/ta_bootstrap.py" "$@"` |
| 4 | README distinguishes human acquisition gate from script injection step | VERIFIED | README separates ACQUISITION (human/browser) from INJECTION (script) from SWEEP (engine) sections; no stale Playwright install references in OPERATOR GATE |
| 5 | POST /api/v1/tripadvisor/session with valid body writes Redis key with TTL and returns {status: 'ready'} | VERIFIED | `tripadvisor_session.py:222` uses `redis.setex(BRAVE_TA_SESSION_KEY, ta_config.session_ttl, ...)`; returns `{"status": "ready", "canary": "ready"}`; `test_inject_valid_session_returns_ready` passes |
| 6 | POST /api/v1/tripadvisor/session with malformed body returns 422 before Redis write | VERIFIED | Pydantic `SessionInjectBody` with `extra="forbid"`, non-empty validators; `test_inject_malformed_body_422`, `test_inject_extra_field_forbidden_422`, `test_inject_empty_cookies_422`, `test_inject_empty_query_ids_422` all pass |
| 7 | Canary failure (SessionExpiredError / empty result) deletes Redis key and returns 422 invalid_session | VERIFIED | `_run_canary` catches `SessionExpiredError`/`asyncio.TimeoutError` → deletes key → 422; empty-result guard explicit at line 159; `test_canary_fail_deletes_key_returns_422` and `test_canary_empty_result_returns_422` pass |
| 8 | Infrastructure canary error (infra fault, not bad session) returns 503 and does NOT delete key | VERIFIED | `_run_canary` generic `except Exception` branch: logs `reason=type(exc).__name__` (never str(exc) — CR-01 fix), raises HTTP 503 `canary_unverified` without deleting key (WR-02 fix); `test_canary_infra_error_returns_503_and_keeps_key` passes |
| 9 | GET /api/v1/tripadvisor/session/status returns three-state response correctly | VERIFIED | `session_status()` handler: present=True with expires_in+query_ids when key exists; present=False + reason="needs_bootstrap" when marker set; present=False + reason=null when no key and no marker; 3 tests pass |
| 10 | Both endpoints require require_steward_or_bearer; unauthenticated callers get 401 | VERIFIED | Both routes use `dependencies=[Depends(require_steward_or_bearer)]`; `test_inject_unauthenticated_gets_401` and `test_status_unauthenticated_gets_401` pass |
| 11 | Audit log records only cookie_count + query_ids keys — never cookie values | VERIFIED | `inject_session()` logs `cookie_count=len(body.cookies)`, `query_ids_keys=list(body.query_ids.keys())`; no `str(exc)` in canary (CR-01 confirmed by grep returning 0 matches) |
| 12 | _get_session() raises SessionMissingError on Redis miss (no auto-bootstrap) | VERIFIED | `client.py:103-107` raises `SessionMissingError` when `self._redis.get(BRAVE_TA_SESSION_KEY)` returns None; `test_get_session_raises_on_redis_miss` passes |
| 13 | _get_session() returns injected session dict when key is present | VERIFIED | Lines 109-116: decodes bytes, JSON-parses, normalises Phase 11 list-cookies to flat dict; 3 session tests pass |
| 14 | fetch_destinations and fetch_attractions send payload with extensions.preRegisteredQueryId (not "query" key) | VERIFIED | Both methods build `[{"variables": {...}, "extensions": {"preRegisteredQueryId": query_id}}]`; `test_fetch_destinations_payload_shape` and `test_fetch_attractions_payload_shape` assert `"query" not in item`; grep confirms 2 occurrences of `"preRegisteredQueryId"` in client.py |
| 15 | TripAdvisorClient has no _bootstrap_session method; Playwright not importable from client.py | VERIFIED | `test_no_bootstrap_session_method` (hasattr returns False); AST test confirms no playwright import anywhere; `concurrent.futures` import absent; grep for `_bootstrap_session` returns 0 matches |
| 16 | scraper optional dep group removed from pyproject.toml | VERIFIED | `grep -n "playwright|scraper" pyproject.toml` returns 0 matches; `test_no_scraper_dep_in_pyproject` passes |
| 17 | Residential proxy (BRAVE_TA_PROXY_URL) is threaded into httpx.AsyncClient in both fetch methods | VERIFIED | Lines 158-171 and 227-237: `proxy = self._config.proxy_url or None` passed as `proxy=proxy` to `httpx.AsyncClient(...)` in both fetch_destinations and fetch_attractions (CR-02 fix); `test_fetch_destinations_threads_configured_proxy` and `test_fetch_destinations_no_proxy_passes_none` pass |
| 18 | Canary bounded to max_pages=1 to avoid destroying a valid session on a slow large-UF paginate | VERIFIED | `_run_canary` calls `client.fetch_destinations("RJ", max_pages=1)` (line 129); `fetch_destinations` signature accepts `max_pages: int | None = None`; `test_fetch_destinations_max_pages_one_stops_after_first` confirms exactly 1 request issued |
| 19 | sweep_tripadvisor catches SessionMissingError and stops immediately — no retry, no quarantine, needs_bootstrap set | VERIFIED | Lines 1021-1035: `except (SessionMissingError, SessionExpiredError)` → `session.rollback()` → `_mark_needs_bootstrap()` → `return`; 5 tests in `TestSweepTripAdvisorSessionFailFast` pass including no-retry and no-quarantine assertions |
| 20 | Generic exceptions still retry (existing behaviour unchanged) | VERIFIED | `test_normal_exception_still_retries` passes — RuntimeError triggers `self.retry` |
| 21 | EngineControl shows three-state session-health pill when source=tripadvisor (Pronta / Precisa bootstrap / Expirada) | VERIFIED | `EngineControl.tsx`: `sessionLabel()` and `sessionColor()` functions with three states; `data-testid="ta-session-status"` rendered conditionally; all 5 session-health tests pass |
| 22 | Session-health pill is NOT shown when source=default | VERIFIED | `showSessionStatus` guard: `selectedSource === "tripadvisor" || (state !== "idle" && data?.source === "tripadvisor")`; `test_does_NOT_render_the_session-health_pill_when_source_default` passes |

**Score: 22/22 truths verified**

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `data/tripadvisor/README` | Operator-gate section with DevTools cURL acquisition; no Playwright bootstrap refs | VERIFIED | Lines 118-197 OPERATOR GATE; grep for "playwright install chromium" returns 0; "ta_bootstrap" appears 4+ times |
| `scripts/ta_bootstrap` | Executable shell entry point | VERIFIED | Exists, `-rwxr-xr-x`, delegates to ta_bootstrap.py |
| `scripts/ta_bootstrap.py` | stdlib-only cURL parser + POST helper | VERIFIED | Contains `def main`, `def parse_curl`, `def inject_session`; only stdlib imports |
| `brave/api/routers/tripadvisor_session.py` | POST + GET endpoints, `router` exported | VERIFIED | 325 lines, `router = APIRouter()`, both endpoints present, `SessionExpiredError` imported, `_TA_NEEDS_BOOTSTRAP_KEY` defined |
| `brave/lanes/tripadvisor/client.py` | `SessionMissingError` + `_get_session` Redis-only + correct payload | VERIFIED | `SessionMissingError` at line 60, `_get_session` at line 91, `preRegisteredQueryId` payload in both fetch methods |
| `pyproject.toml` | scraper optional dep group removed | VERIFIED | 0 matches for "playwright" or "scraper" |
| `tests/unit/api/test_tripadvisor_session.py` | 11+ offline tests for session endpoints | VERIFIED | 13 test functions covering all specified cases |
| `tests/unit/lanes/tripadvisor/test_client.py` | SessionMissingError + payload shape + no real_browser class | VERIFIED | `TestTripAdvisorClientSessionInjection` (6 tests), `TestTripAdvisorClientPayloadShape` (4 tests), `TestTripAdvisorClientProxyAndPaging` (3 tests); no `TestTripAdvisorClientRealBrowser` class |
| `brave/tasks/pipeline.py` | `sweep_tripadvisor` with fail-fast + needs_bootstrap marker | VERIFIED | `except (SessionMissingError, SessionExpiredError)` at line 1021, `_mark_needs_bootstrap()` helper, `_TA_NEEDS_BOOTSTRAP_KEY` constant |
| `tests/unit/tasks/test_sweep_tripadvisor.py` | Offline fail-fast tests | VERIFIED | 5 tests in `TestSweepTripAdvisorSessionFailFast` all pass |
| `dashboard/components/engine/EngineControl.tsx` | Session-health pill with three states | VERIFIED | `showSessionStatus` guard, `useQuery` for `fetchTASessionStatus`, pill with `data-testid="ta-session-status"`, `expires_in > 0` guard (WR-03 fix) |
| `dashboard/lib/engine-api.ts` | `TASessionStatus` interface + `fetchTASessionStatus` + `taSessionKeys` | VERIFIED | Lines 112-127; all three exports present |
| `dashboard/mocks/handlers/engine.ts` | `taSessionStatus()` MSW handler | VERIFIED | Lines 64-73; returns default ready state; `TA_BASE` constant used |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `scripts/ta_bootstrap.py` | POST /api/v1/tripadvisor/session | `urllib.request.urlopen(req)` with cookies + query_ids JSON body | VERIFIED | `inject_session()` builds URL, sets Authorization header, POSTs |
| `tripadvisor_session.py` | Redis BRAVE_TA_SESSION_KEY | `redis.setex(BRAVE_TA_SESSION_KEY, ta_config.session_ttl, ...)` | VERIFIED | Line 222; key written before canary runs |
| `tripadvisor_session.py` | `_run_canary` → TripAdvisorClient | `TripAdvisorClient(config=ta_config, redis=redis).fetch_destinations("RJ", max_pages=1)` | VERIFIED | Lines 121-129; imports TripAdvisorClient inside `_run_canary` |
| `tripadvisor_session.py` GET handler | Redis `brave:ta:needs_bootstrap` | `redis.get(_TA_NEEDS_BOOTSTRAP_KEY)` | VERIFIED | Line 320; returns `reason="needs_bootstrap"` when set |
| `brave/api/main.py` | `tripadvisor_session.router` | `app.include_router(tripadvisor_session.router)` | VERIFIED | Lines 65-67 of main.py |
| `client.py _get_session` | Redis BRAVE_TA_SESSION_KEY | `self._redis.get(BRAVE_TA_SESSION_KEY)` → raises `SessionMissingError` on None | VERIFIED | Lines 102-107 |
| `client.py fetch_destinations` | `_TA_GRAPHQL_URL` POST body | `payload = [{"variables": {...}, "extensions": {"preRegisteredQueryId": query_id}}]` | VERIFIED | Lines 164-168 |
| `pipeline.py sweep_tripadvisor` | `SessionMissingError` → `_mark_needs_bootstrap()` → return | `except (SessionMissingError, SessionExpiredError)` guard | VERIFIED | Lines 1021-1035 |
| `EngineControl.tsx` | GET /api/v1/tripadvisor/session/status | `useQuery({ queryFn: fetchTASessionStatus, enabled: showSessionStatus })` | VERIFIED | Lines 107-113 |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `EngineControl.tsx` | `sessionStatus` | `fetchTASessionStatus()` → GET `/api/v1/tripadvisor/session/status` → `session_status()` → `redis.get(BRAVE_TA_SESSION_KEY)` | Real Redis read (or fakeredis in tests) | FLOWING |
| `tripadvisor_session.py inject_session` | `session` dict | `body.cookies`, `body.query_ids`, `body.user_agent`, `body.acquired_at` from operator-injected request body | Real operator data (not hardcoded) | FLOWING |
| `tripadvisor_session.py session_status` | `TASessionStatusResponse` | `redis.get(BRAVE_TA_SESSION_KEY)` + `redis.ttl(...)` | Real Redis reads | FLOWING |
| `client.py fetch_destinations` | `results` list | httpx POST to TripAdvisor with injected cookies — gated on `BRAVE_TA_PROXY_URL` | Real data in production (operator-gated; offline tests use respx mocks) | FLOWING (prod: operator-gated) |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `ta_bootstrap.py --help` | `python3 scripts/ta_bootstrap.py --help` | Shows usage with `--curl`, `--endpoint`, `--bearer` options | PASS |
| Syntax check | `python3 -c "import ast; ast.parse(open('scripts/ta_bootstrap.py').read()); print('ok')"` | `ok` | PASS |
| No third-party imports | `grep -E "^import|^from" scripts/ta_bootstrap.py \| grep -Ev "argparse|json|re|os|datetime|sys|urllib"` | 0 matches | PASS |
| No playwright in client.py | `grep -n "playwright" brave/lanes/tripadvisor/client.py` | 0 matches | PASS |
| No scraper/playwright in pyproject.toml | `grep -n "playwright\|scraper" pyproject.toml` | 0 matches | PASS |
| proxy= threaded into httpx | `grep -n "proxy=" brave/lanes/tripadvisor/client.py` | 2 matches (fetch_destinations + fetch_attractions) | PASS |
| No str(exc) in canary | `grep -n "str(exc)" brave/api/routers/tripadvisor_session.py` | 0 matches (CR-01 fix confirmed) | PASS |
| Backend phase 12 tests | `.venv/bin/python -m pytest tests/unit/api/test_tripadvisor_session.py tests/unit/lanes/tripadvisor/test_client.py tests/unit/tasks/test_sweep_tripadvisor.py -q` | 47 passed | PASS |
| Full offline backend suite | `.venv/bin/python -m pytest tests/unit/ -q -m "not real_browser"` | 405 passed, 5 skipped | PASS |
| Full dashboard suite | `cd dashboard && bun run test` | 158 passed (24 files) | PASS |

### Probe Execution

Step 7c: SKIPPED — no probe-*.sh files declared or present for this phase. The phase was validated via pytest and bun test suites rather than shell probes.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| TA-09 | 12-01-PLAN.md | Operator session-acquisition runbook: DevTools Copy-as-cURL, `data/tripadvisor/README` OPERATOR GATE, `scripts/ta_bootstrap` helper | SATISFIED | README OPERATOR GATE section complete; scripts exist and are functional |
| TA-10 | 12-02-PLAN.md | POST /api/v1/tripadvisor/session endpoint: steward/bearer auth, Pydantic body, size-limited, Redis write, never logs cookie values | SATISFIED | `tripadvisor_session.py` implements all constraints; 13 tests pass |
| TA-11 | 12-02-PLAN.md | Canary validation gate: synchronous single-page httpx probe → ready vs invalid_session/canary_unverified; GET /session/status | SATISFIED | `_run_canary` with max_pages=1, 503 for infra faults (WR-02), 422 for bad session; GET status handler |
| TA-12 | 12-03-PLAN.md | Client refactor: `_get_session()` Redis-only + SessionMissingError; fix persisted-query payload to extensions.preRegisteredQueryId; remove Playwright/_bootstrap_session/scraper dep | SATISFIED | All four truths verified by code and tests |
| TA-13 | 12-04-PLAN.md | sweep_tripadvisor fail-fast on SessionMissingError/SessionExpiredError; needs_bootstrap Redis marker; session-health pill in EngineControl (three states) | SATISFIED | Pipeline fail-fast implemented and tested; dashboard pill with all three states tested |

**All 5 requirements (TA-09..TA-13) satisfied.**

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `tripadvisor_session.py` | 258, 269 | `logger.warning("ta_session_audit_failed/skip", error=str(audit_exc))` — audit failure logs full exception string | INFO | WR-05 deferred item from review. The audit block wraps a non-credential exception (database connectivity); `str(audit_exc)` would not expose cookie values here. The severity in the review was about audit-trail completeness, not credential leakage. Not a blocker. |
| `tripadvisor_session.py` | 197-205 | 64 KB content-length guard checks only when `Content-Length` header is present | INFO | WR-01 deferred item. Chunked-encoding requests bypass the guard. This is a hardening gap, not a correctness failure — Pydantic still validates the body shape. Not a blocker. |

No `TBD`, `FIXME`, or `XXX` markers found in any of the 16 files modified by this phase.

### Human Verification Required

The automated checks (405 backend tests, 158 dashboard tests) all pass. Three items require a real browser session to verify end-to-end operability:

### 1. ta_bootstrap cURL Parsing with Real DevTools Output

**Test:** Capture a real `graphql/ids` POST from DevTools "Copy as cURL (bash)" in Chrome or Firefox. Run `python scripts/ta_bootstrap.py --curl /tmp/ta_session.curl --endpoint http://localhost:8000 --bearer <token>`.
**Expected:** Prints "Parsed: N cookies, query_ids={destinations: ..., attractions: ...}" then "Session injected — canary result: ready". GET /api/v1/tripadvisor/session/status returns `{present: true, expires_in: >0}`.
**Why human:** DevTools produces platform-specific cURL quoting. The `parse_curl()` regex handles common variants (single/double quotes, `$'...'` form) but real-world edge cases can only be confirmed with a live capture.

### 2. Full Session Injection + Canary Validation End-to-End

**Test:** From a machine with a residential IP (or with BRAVE_TA_PROXY_URL configured), inject a real DataDome session via `POST /api/v1/tripadvisor/session`. Observe the canary result.
**Expected:** With a valid live session: `{"status": "ready", "canary": "ready"}`. With an expired or invalid session: `{"detail": "invalid_session"}` (422) with key deleted. With a proxy/DNS fault: `{"detail": "canary_unverified"}` (503) with key preserved.
**Why human:** DataDome blocks automated requests from datacenter IPs. The canary makes a live httpx call to TripAdvisor — this cannot be validated in CI without a real session and (optionally) a residential proxy.

### 3. Sweep Operability: Real Collection + Session Expiry Mid-Sweep

**Test:** Inject a real session, set RUN_REAL_EXTERNALS=1, POST /api/v1/engine/start with source=tripadvisor and one UF. Monitor the EngineControl dashboard pill and pipeline counts.
**Expected:** Pill shows "Pronta" before sweep. If session expires mid-sweep, sweep stops cleanly and pill changes to "Precisa bootstrap" (needs_bootstrap marker set). At least one Nascente record is written per UF swept.
**Why human:** Real collection depends on DataDome cookie lifetime, proxy configuration, and TripAdvisor data availability — none of which are exercisable in the offline test environment.

---

## Gaps Summary

No gaps found. All 22 must-haves are verified. The deferred review items (WR-01 body-size middleware, WR-04 README shape note, WR-05 audit error log level, IN-02..IN-05 stale docstrings/dead config) are tracked in 12-REVIEW.md frontmatter and do not block phase goal achievement.

The three human verification items are operational readiness checks — they validate that the seam works in the real environment with a real DataDome session. The implementation is complete and correct; the lane is deliberately operator-gated (not 24/7 autonomous).

---

_Verified: 2026-06-24T16:00:00Z_
_Verifier: Claude (gsd-verifier)_
