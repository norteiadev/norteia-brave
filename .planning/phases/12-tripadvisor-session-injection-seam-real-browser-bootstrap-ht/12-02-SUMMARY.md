---
phase: 12-tripadvisor-session-injection-seam-real-browser-bootstrap-ht
plan: "02"
subsystem: tripadvisor-session-api
tags: [tripadvisor, session-injection, canary-gate, fastapi, redis, tdd]
dependency_graph:
  requires:
    - brave/lanes/tripadvisor/client.py (BRAVE_TA_SESSION_KEY, SessionExpiredError, TripAdvisorClient)
    - brave/api/deps.py (get_redis, require_steward_or_bearer, get_db)
    - brave/config/settings.py (TripAdvisorConfig.session_ttl)
    - brave/observability/audit.py (write_audit)
  provides:
    - POST /api/v1/tripadvisor/session (session injection + canary gate)
    - GET /api/v1/tripadvisor/session/status (session read + reason discriminator)
  affects:
    - brave/api/main.py (router registration added)
tech_stack:
  added: []
  patterns:
    - TDD (RED→GREEN): failing tests committed first, then implementation
    - monkeypatchable _run_canary at module level (no respx needed for canary-path tests)
    - dependency_overrides pattern (fakeredis + auth bypass) matching test_engine_source.py
    - Pydantic v2 model_validator(mode="after") for non-empty dict validation
    - extra="forbid" on SessionInjectBody (Pydantic)
    - content-length guard before Pydantic parse (T-12-02-03)
    - audit-only metadata pattern: cookie_count + query_ids keys; never cookie values (T-12-02-01)
key_files:
  created:
    - brave/api/routers/tripadvisor_session.py
    - tests/unit/api/test_tripadvisor_session.py
  modified:
    - brave/api/main.py
decisions:
  - "_run_canary is a module-level async function rather than a method — allows monkeypatching in tests without respx; canary always runs against real TripAdvisor using injected cookies"
  - "Audit logging skipped gracefully when get_db() returns None (test override) — non-blocking pattern preserves inject success path even without DB"
  - "64 KB size limit enforced via content-length header check before Pydantic parse (T-12-02-03)"
  - "SessionInjectBody uses model_validator(mode='after') to reject empty cookies/query_ids dicts after Pydantic field assignment"
metrics:
  duration: "~12 min"
  completed: "2026-06-24"
  tasks_completed: 1
  files_created: 2
  files_modified: 1
---

# Phase 12 Plan 02: TripAdvisor Session Injection Endpoint Summary

**One-liner:** POST /api/v1/tripadvisor/session with synchronous canary gate and GET /session/status with reason discriminator (needs_bootstrap/null), backed by fakeredis offline tests.

## What Was Built

### brave/api/routers/tripadvisor_session.py

New router with two endpoints:

**POST /api/v1/tripadvisor/session** (`require_steward_or_bearer`, 200):
- `SessionInjectBody` (Pydantic, `extra="forbid"`): `cookies` (non-empty), `query_ids` (≥1 key), `user_agent`, `acquired_at` required; `client_hints`, `locale`, `acquisition_ip` optional
- 64 KB content-length guard before Pydantic parse (T-12-02-03 DoS mitigation)
- Writes `BRAVE_TA_SESSION_KEY` to Redis with `TripAdvisorConfig.session_ttl` TTL
- Calls `_run_canary(session, ta_config, redis)` — module-level async function, monkeypatchable in tests
- `_run_canary` guards: `SessionExpiredError`, `asyncio.TimeoutError`, general exception, AND empty-result list — all delete Redis key and raise `HTTPException(422, detail="invalid_session")`
- Audit log: `{cookie_count, query_ids: keys, acquired_at, canary_result}` — never cookie values (T-12-02-01)

**GET /api/v1/tripadvisor/session/status** (`require_steward_or_bearer`, 200):
- `TASessionStatusResponse`: `present`, `expires_in`, `query_ids`, `reason`
- Session present: `{present: True, expires_in: ttl, query_ids: [...], reason: null}`
- Session absent + `brave:ta:needs_bootstrap` set: `{present: False, reason: "needs_bootstrap"}`
- Session absent + no marker: `{present: False, reason: null}`

### tests/unit/api/test_tripadvisor_session.py

13 offline tests using fakeredis + dependency_overrides:
1. `test_inject_valid_session_returns_ready` — canary no-op → 200 + Redis key set
2. `test_inject_malformed_body_422` — missing cookies → 422, no Redis write
3. `test_inject_extra_field_forbidden_422` — unknown_field → 422 (extra=forbid)
4. `test_inject_empty_cookies_422` — empty cookies → 422
5. `test_inject_empty_query_ids_422` — empty query_ids → 422
6. `test_inject_body_size_limit` — 70 KB body → 422/413
7. `test_canary_fail_deletes_key_returns_422` — canary raises → key deleted + 422 invalid_session
8. `test_canary_empty_result_returns_422` — empty-result canary → key deleted + 422 invalid_session
9. `test_status_present` — key in Redis → `{present: True, expires_in: int, query_ids, reason: null}`
10. `test_status_needs_bootstrap` — marker set → `{present: False, reason: "needs_bootstrap"}`
11. `test_status_absent` — no key, no marker → `{present: False, reason: null}`
12. `test_inject_unauthenticated_gets_401` — no auth → 401
13. `test_status_unauthenticated_gets_401` — no auth → 401

### brave/api/main.py

Added `from brave.api.routers import tripadvisor_session` and `app.include_router(tripadvisor_session.router)` (Phase 12 comment block).

## TDD Gate Compliance

- RED commit: `880c47f` — `test(12-02): add failing tests for TA session injection endpoint` (all 13 fail, module not found)
- GREEN commit: `fc4ff9d` — `feat(12-02): implement TA session injection + canary gate + status endpoint` (all 13 pass)
- REFACTOR: not needed — code clean as written

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

### Deliberate Adjustments

**Audit logging made non-blocking when `get_db()` is overridden to `None`**
- The plan specified `write_audit(session=db, ...)` but tests override `get_db` to return `None` to avoid needing a real database.
- The implementation catches the `None` case and logs a warning rather than failing the inject.
- This is the correct behavior for a side-effect operation — audit failure must not block a successful inject.

**Body size check uses `content-length` header only (not full body read)**
- The plan offered two approaches; the `content-length` header check was chosen as it avoids buffering the oversized body, which is more DoS-resistant.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes were introduced beyond what is in the plan's threat model (T-12-02-01 through T-12-02-05 all addressed).

| Threat | Status |
|--------|--------|
| T-12-02-01 Cookie values logged | Mitigated — only `cookie_count` + `query_ids` keys in all log calls |
| T-12-02-02 Unauthenticated inject | Mitigated — `require_steward_or_bearer` on both endpoints; test 12/13 assert 401 |
| T-12-02-03 Oversized POST body | Mitigated — `content-length` guard at 64 KB before Pydantic parse |
| T-12-02-04 Canary MITM | Accepted — server-side httpx only; out of scope |
| T-12-02-05 Concurrent inject race | Accepted — last-write-wins; both callers authenticated |

## Known Stubs

None.

## Self-Check: PASSED

Files created/modified:
- [FOUND] brave/api/routers/tripadvisor_session.py
- [FOUND] tests/unit/api/test_tripadvisor_session.py
- [FOUND] brave/api/main.py (modified)

Commits:
- [FOUND] 880c47f — test(12-02): add failing tests for TA session injection endpoint
- [FOUND] fc4ff9d — feat(12-02): implement TA session injection + canary gate + status endpoint

All 13 tests confirmed passing in the GREEN phase run.
Router registration confirmed: `grep "tripadvisor_session" brave/api/main.py` → 2 matches.
