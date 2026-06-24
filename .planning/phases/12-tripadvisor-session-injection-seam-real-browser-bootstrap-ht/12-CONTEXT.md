# Phase 12 — TripAdvisor session-injection seam · CONTEXT

Requirements: TA-09, TA-10, TA-11, TA-12, TA-13
Source: office-hours design doc `~/.gstack/projects/norteia-brave/leandro-main-design-20260624-121942.md` (stress-tested 9/10, spike-validated 2026-06-24).

## Goal

Make the TripAdvisor lane actually collect data by splitting **session acquisition**
(hard, anti-bot, operator-gated) from **bulk fetch** (cheap, deterministic httpx). Phase 11
shipped the lane but its headless-Playwright bootstrap is DataDome-walled from a
datacenter/home IP (real ES test: 403, 0 records).

## Why (core tension, grounded in code + spike)

Spike 2026-06-24 measured the real `POST https://www.tripadvisor.com/data/graphql/ids`:
- A `datadome` cookie + TA session cookies captured from the operator's logged-in Chrome
  (DevTools Copy-as-cURL) **survive replay through `httpx`** — HTTP 200 + real GraphQL data,
  same machine/IP, different TLS/JA3, repeatable. → acquisition and fetch CAN be decoupled
  (refutes the cookie/fingerprint-binding worry).
- Automated browsers are walled: raw httpx, gstack `/browse` headless, and `/browse`
  headed+stealth all got 403. Only the genuine logged-in human browser passes.
- **The real persisted-query format is `{"variables": {...}, "extensions": {"preRegisteredQueryId": "<16-hex>"}}`** sent as a BATCH ARRAY — NOT the `{"query": queryId, "variables": {...}}`
  shape `brave/lanes/tripadvisor/client.py` currently builds. Phase 11's fetch payload is wrong.

## LOCKED decisions

### Approach (TA-09..TA-13)
- Approach A (session-injection seam). Spike confirmed cookie portability → do NOT escalate
  to browser-side fetch (Approach B was the fallback if the spike failed).
- Session acquired by a REAL human browser, not automated. Operator-gated, low volume,
  NOT on the autonomous beat.

### Acquisition runbook (TA-09)
- Capture cookies (incl. `datadome`) + `preRegisteredQueryId`s from a real logged-in browser
  via DevTools "Copy as cURL" of a `graphql/ids` POST (or a `/browse` handoff).
- Ships as repo runbook: `data/tripadvisor/README` operator-gate section + a `scripts/ta_bootstrap`
  helper that turns a captured cURL into a `POST /tripadvisor/session` call.

### Injection endpoint (TA-10)
- `POST /api/v1/tripadvisor/session`, `require_steward_or_bearer` (same gate as `/engine/start`).
- Pydantic body, `extra="forbid"`: `cookies` (non-empty), `query_ids` (≥1), `user_agent`,
  `acquired_at` required; `client_hints`, `locale`, `acquisition_ip` optional. 64 KB size limit;
  malformed → 422.
- Writes Redis `BRAVE_TA_SESSION_KEY` with TTL `BRAVE_TA_SESSION_TTL` (config, default 1800 s —
  OUR cache TTL, not DataDome's real token lifetime). NEVER log cookie values; audit-log only
  `{cookie_count, query_ids: keys, acquired_at, canary_result}`.

### Canary gate (TA-11)
- On inject, synchronously run ONE real `graphql/ids` request through the SAME production
  `httpx` path the worker uses, 15 s hard timeout.
- 200 + non-empty data → `ready`; 403 / captcha / empty-or-generic payload / timeout →
  `invalid_session` (delete the Redis key, return 422). Empty payload counts as failure
  (catches a stale `queryId` returning 200-but-empty).
- `GET /api/v1/tripadvisor/session/status` → `{present, expires_in, query_ids}` for dashboard.

### Client refactor (TA-12)
- `_get_session()` reads Redis only; miss/expiry → `SessionMissingError` (new exception).
- **Fix the persisted-query payload to `extensions.preRegisteredQueryId` (batch-array shape).**
- Remove the Playwright `_bootstrap_session` + the ThreadPoolExecutor offload + the `scraper`
  optional dependency (`pyproject.toml`) + the `real_browser` test/marker (the bootstrap path
  no longer exists).
- Verify real attraction `geoId`s — seed ES `303516` redirected to MG `303380`; the seed map
  may be wrong.

### Sweep fail-fast + visibility (TA-13)
- `sweep_tripadvisor` catches `SessionMissingError` and the FIRST mid-sweep
  403/captcha/empty-payload/stale-`queryId` → stop, mark `needs_bootstrap`, NO retry-storm.
- Session state machine (derived from the single Redis key): `needs_bootstrap` (absent) /
  `ready` (present + canary passed) / `invalid` (canary failed → key deleted) / `expired`
  (TTL elapsed → absent). Mid-sweep expiry: partial ingest acceptable (records independently
  scored), sweep stops cleanly.
- Surface session state to the operator dashboard (engine status / a session-health indicator)
  instead of silent 0-records. Sweeps capped by record count + wall-clock budget under TTL.

## Defaults (reversible)
- `BRAVE_TA_SESSION_TTL` default 1800 s. Canary timeout 15 s. Endpoint size limit 64 KB.
- Dashboard session-health indicator on EngineControl is in scope but minimal (can be a
  read of `GET /session/status`).

## Out of scope (future)
- Residential-proxy automation; sweep-level checkpointing (deferred — sweeps sized under TTL);
  autonomous 24/7 TA (would need a paid/licensed source or managed browser+proxy stack);
  the "5-attraction cap" test (handled separately later).
- Follow-up (not blocking): characterize the real DataDome token lifetime empirically.

## Conventions to mirror
- Endpoint: mirror `brave/api/routers/engine.py` auth + validation-before-mutation pattern.
- Redis access: reuse `BRAVE_DB_REDIS_URL` + the existing client `_get_session` read path.
- Tests: 100% offline by default (fakeredis for the endpoint; FakeTripAdvisorClient for the
  client). NO test hits real TripAdvisor — acquisition is operator-run, out of CI.

## Tests (100% offline default)
- Endpoint (fakeredis): valid body → `ready` + Redis key set with TTL; malformed → 422;
  canary-fail path → `invalid_session` + key deleted; `GET /session/status` reflects presence/expiry.
- Client: `_get_session` raises `SessionMissingError` on miss; reads an injected session; the
  payload now uses `extensions.preRegisteredQueryId` (assert the request body shape).
- Sweep: `SessionMissingError` → fail-fast, no retry, state surfaced.
