# Quick Task 260629-p2v: TripAdvisor session auto-refresh — Context

**Gathered:** 2026-06-29
**Status:** Ready for planning

<domain>
## Task Boundary

Make the operator paste the TripAdvisor cURL **only once**. Today the session in Redis
(`brave:ta:session`) goes stale well before the operator's actual TA login expires, forcing
frequent re-paste. Root cause + fix validated empirically below.

Two building blocks (both in scope):
- **A. Cookie write-back** — after each TA request, merge the response `Set-Cookie` jar back
  into `brave:ta:session` and slide its TTL.
- **B. Keep-alive Celery beat** — periodic light HTML GET to re-mint `datadome` during idle so
  the session never lapses between sweeps.

Out of scope: solving a hard DataDome JS challenge (httpx can't); changing the acquisition
(cURL paste) UI; proxy/IP strategy.
</domain>

<decisions>
## Implementation Decisions (LOCKED — do not revisit)

### Scope
- Implement **A + B** (write-back + keep-alive beat). Confirmed by operator.

### Fallback on refresh failure (403 / DataDome hard block)
- **Keep the existing gate unchanged**: on `SessionExpiredError`/`SessionMissingError` mid-flow,
  reuse the current `needs_bootstrap` marker + engine auto-OFF + re-paste toast path
  (`brave/tasks/pipeline.py:1116-1143`, `tripadvisor_session.py` canary). Do NOT add a
  "try keep-alive before giving up" retry. The only change is that this path is now hit *rarely*.

### Cookie write-back mechanics
- Persist the **full rotated jar** back, not just a subset. Empirically the cookies that rotate
  are `datadome`, `__vt`, `TASID` (→ `session_id`), `TAUnique`; `TASSK`/`TART` may rotate too.
  Merge response cookies over the stored ones (new wins), keep untouched long-lived ones
  (`TAAUTHEAT`, device IDs).
- Re-derive `session_id` from the refreshed `TASID` when present.
- **Slide TTL**: on each successful write-back, reset Redis key TTL to `session_ttl` (sliding
  window) so an actively-used session never expires.
- Write-back must be **best-effort / non-fatal**: a Redis or parse error during write-back must
  NOT break the actual data fetch. Log + continue.
- Must not log secret cookie *values* (follow existing redaction discipline:
  `tripadvisor_session.py` audit writes counts only; `ta_bulk_sweep.py:26`).

### Keep-alive beat
- New Celery beat task (redbeat) that, **only when a session exists** (TTL>0) and the engine is
  idle/enabled, issues ONE light `GET` of the listing HTML URL with the stored cookies to
  re-mint `datadome`+`__vt`, then runs cookie write-back. Skips entirely when no session.
- Interval configurable via settings (env `BRAVE_TA_*`). Default proposal: every ~10 min
  (session_ttl default = 1800s/30min, so 10min keeps a comfortable margin). Planner to pick a
  sane default < session_ttl/2.
- Respect `run_real_externals` gate (no real HTTP in tests; use `NullTripAdvisorClient` pattern
  already in `pipeline.py:1000`).
- Keep-alive failure (403) → same fallback as above (needs_bootstrap + engine off). Do not crash
  the beat.

### Where the write-back lives
- The client (`brave/lanes/tripadvisor/client.py`) currently builds a fresh
  `httpx.AsyncClient(cookies=...)` **per request and discards the response jar**
  (`fetch_destinos` ~:357, `fetch_attractions` ~:419, `fetch_attractions_paginated` ~:583).
  Add capture of `response.cookies` / `Set-Cookie` after each successful call and route it to a
  single shared persist helper that updates `brave:ta:session`.
- Centralize the merge+persist+TTL-slide in ONE function (e.g. in `client.py` or a small
  `session.py`) so all three transports + the keep-alive beat reuse it. Avoid duplicating
  Redis-write logic.
</decisions>

<specifics>
## Empirical validation (2026-06-29, operator's fresh cURL, same machine = same residential IP)

Spike replayed the operator cookie jar via httpx (h1.1) against tripadvisor.com.br:

| Request | Status | Cookies rotated via `Set-Cookie` |
|---------|--------|----------------------------------|
| POST `/data/graphql/ids` | 200 (30 B `[{"data":{"isSaved":[false]}}]`) | `__vt` (every call), `TAUnique` |
| POST again (reused jar) | 200 | `__vt` again |
| GET HTML listing page | 200 (~1.5 MB) | **`datadome`** + `__vt` + `TASID` + `TAUnique` |

Conclusions:
1. httpx replay from the collector host works (200) when IP matches where cookies were earned.
2. TA hands back rotated cookies on **every** response; current code discards them → staleness.
3. `datadome` (the anti-bot cookie that expires first) is re-minted specifically on the **HTML
   GET**, which is why the keep-alive uses an HTML GET, not the tiny graphql POST.
4. Long-lived `TAAUTHEAT` (login) + device IDs persist; only short-lived cookies need renewal.

Key URLs/payload used:
- GraphQL ids: `https://www.tripadvisor.com.br/data/graphql/ids`
- HTML listing: `https://www.tripadvisor.com.br/Attractions-g294280-Activities-a_allAttractions.true-Brazil.html`
- Payload: `[{"variables":{"request":{"id":"1493739","type":"location"}},"extensions":{"preRegisteredQueryId":"25f9ddb1ce629144"}}]`
- Browser-like Accept/sec-fetch headers required on the HTML GET or DataDome 403s
  (already handled at `client.py:558-568`).

## Existing code map (from exploration)
- Inject endpoint: `POST /api/v1/tripadvisor/session` → `tripadvisor_session.py:218-346`;
  writes `redis.setex(BRAVE_TA_SESSION_KEY, ta_config.session_ttl, json)` at `:270`; synchronous
  canary via `TripAdvisorClient.fetch_attractions`.
- Session read/normalize: `client.py:_get_session()` :115-140 (Phase-11 list-form cookies
  normalized to flat dict).
- Engine start TA gate (TTL>0): `brave/api/routers/engine.py:170-180`.
- Sweep worker builds client / NullTripAdvisorClient: `brave/tasks/pipeline.py:989-1001`;
  mid-sweep session-failure auto-OFF: `:1116-1143`.
- Config `TripAdvisorConfig` (env `BRAVE_TA_`): `brave/config/settings.py` — `session_ttl`
  (default 1800), `proxy_url`, `page_throttle_seconds`, `query_id_override`.
- Redis keys: `brave:ta:session`, `brave:ta:needs_bootstrap`, `brave:ta:sweep:progress`.
- Existing beats live in `brave/tasks/` (redbeat schedule); prior `ta_resume_watch` 60s beat was
  added then removed in quick task e69 — follow the current beat-registration pattern.
</specifics>

<canonical_refs>
## Canonical References
- Requirements TA-01…TA-13: `.planning/REQUIREMENTS.md:200-216` (DataDome, cookies incl
  `datadome`, `preRegisteredQueryId`, canary gate, `SessionMissingError`).
- Prior TA quick tasks: `260628-m1n` (bulk auto-resume, later removed), `260629-e69` (motor
  token-validity gate — current behavior to preserve).
- Test rules: no real externals by default; `RUN_REAL_EXTERNALS` opt-in; dashboard `bun run test`,
  backend `.venv/bin/python -m pytest`.
</canonical_refs>
