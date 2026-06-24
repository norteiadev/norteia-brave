---
phase: 12-tripadvisor-session-injection-seam-real-browser-bootstrap-ht
reviewed: 2026-06-24T00:00:00Z
depth: standard
files_reviewed: 16
files_reviewed_list:
  - brave/api/main.py
  - brave/api/routers/tripadvisor_session.py
  - brave/lanes/tripadvisor/client.py
  - brave/tasks/pipeline.py
  - dashboard/components/engine/EngineControl.tsx
  - dashboard/components/engine/__tests__/EngineControl.test.tsx
  - dashboard/lib/engine-api.ts
  - dashboard/mocks/handlers/engine.ts
  - data/tripadvisor/README
  - pyproject.toml
  - scripts/ta_bootstrap
  - scripts/ta_bootstrap.py
  - tests/unit/api/test_tripadvisor_session.py
  - tests/unit/lanes/tripadvisor/test_client.py
  - tests/unit/tasks/test_sweep_tripadvisor.py
findings:
  critical: 2
  warning: 7
  info: 5
  total: 14
status: issues_found
---

# Phase 12: Code Review Report

**Reviewed:** 2026-06-24
**Depth:** standard
**Files Reviewed:** 16
**Status:** issues_found

## Summary

This phase implements the TripAdvisor session-injection seam: an operator-captured browser session is POSTed to a steward/bearer-guarded endpoint, canary-validated through the production httpx path, written to Redis, and consumed by a Playwright-free client. The auth guard is solid (constant-time compare, fail-closed), the canary delete-on-failure gate is correct, the `extensions.preRegisteredQueryId` payload shape matches the persisted-query contract, and the fail-fast (no-retry) path on session errors in `sweep_tripadvisor` is implemented and tested.

However, the review found **two BLOCKERs** that defeat stated phase goals:

1. **Cookie/credential leak into logs** — the generic-exception path in `_run_canary` logs `reason=str(exc)`, which the phase's own redaction note (T-12-04-01 in `pipeline.py`) explicitly warns can contain cookie fragments. The redaction discipline is applied in the sweep but violated in the canary.
2. **The residential proxy is never used** — `BRAVE_TA_PROXY_URL` / `config.proxy_url` is documented as the DataDome-bypass mechanism in the README and config, but the refactored client constructs `httpx.AsyncClient` with no `proxy=` argument, so the proxy is silently ignored. Every real request goes out from the server's datacenter IP — the exact IP class the README says is DataDome-walled.

Additional warnings cover a 64 KB guard that does not actually block oversized bodies, a frontend/backend `expires_in` contract mismatch, an `expires_in` `0`-vs-`null` semantic gap, a frontend `reason`-type optional mismatch, and a canary that misclassifies infrastructure errors (e.g. unknown-UF `ValueError`, Redis down) as `invalid_session`.

## Critical Issues

### CR-01: Cookie values can leak into logs via `str(exc)` in the canary generic-exception path

**File:** `brave/api/routers/tripadvisor_session.py:139-147`
**Issue:** The phase's redaction requirement (mirrored at `brave/tasks/pipeline.py:1031-1033`, which deliberately logs only `type(exc).__name__` "because exc str may contain cookie fragments from error context") is violated here. The generic-`Exception` branch of `_run_canary` logs `reason=str(exc)`:

```python
except Exception as exc:
    redis.delete(BRAVE_TA_SESSION_KEY)
    logger.warning(
        "ta_session_canary_error",
        reason=str(exc),   # ← leaks full exception text
        ...
    )
```

`fetch_destinations` builds an `httpx.AsyncClient(cookies=cookies, ...)` and calls `resp.raise_for_status()`. An `httpx.HTTPStatusError` (or a transport/redirect error) can carry the request URL, headers, and cookie material in its string form, and any unexpected library exception is uncontrolled. This is the same channel the sweep code explicitly refuses to log. Because cookies = a live DataDome credential, this is a credential-disclosure defect, not a style nit.

**Fix:** Log only the exception class name, matching the sweep discipline:

```python
except Exception as exc:
    redis.delete(BRAVE_TA_SESSION_KEY)
    logger.warning(
        "ta_session_canary_error",
        reason=type(exc).__name__,  # never str(exc) — may carry cookie fragments
        cookie_count=len(session.get("cookies", {})),
        query_ids_keys=list(session.get("query_ids", {}).keys()),
    )
    raise HTTPException(status_code=422, detail="invalid_session") from exc
```

### CR-02: The configured residential proxy is never applied — real requests bypass it entirely

**File:** `brave/lanes/tripadvisor/client.py:158, 219`
**Issue:** `TripAdvisorConfig.proxy_url` (`BRAVE_TA_PROXY_URL`) exists, the README step 11 documents configuring `BRAVE_TA_PROXY_URL=socks5://...`, and the client docstring claims `config.proxy_url` is a constructor concern (T-11-01-01). But neither `fetch_destinations` nor `fetch_attractions` passes the proxy to httpx:

```python
async with httpx.AsyncClient(cookies=cookies, follow_redirects=True) as hc:
    resp = await hc.post(_TA_GRAPHQL_URL, json=payload, headers=headers)
```

`self._config.proxy_url` is read nowhere in the file (the only `_config` use is in `resolve_geo_id`). The proxy is silently dropped. Per the README's own MITIGATIONS (b) and LEGAL RISK sections, datacenter/home IPs are DataDome-walled — so in production every real sweep request, and the canary itself, egresses from the server's datacenter IP, which is precisely the IP class the design intends the proxy to avoid. The canary will then fail (403 → `SessionExpiredError` → key deleted) even when the operator captured a perfectly valid session, making the seam unusable from a server without an inherited residential egress. This is a correctness/operability blocker for the stated real-collection goal.

**Fix:** Thread the configured proxy into both httpx clients (and assert it in a unit test):

```python
proxy = self._config.proxy_url or None
async with httpx.AsyncClient(
    cookies=cookies, follow_redirects=True, proxy=proxy
) as hc:
    resp = await hc.post(_TA_GRAPHQL_URL, json=payload, headers=headers)
```

(Note: httpx 0.28 uses the singular `proxy=` argument — `proxies=` was removed.) If the intentional decision is "no proxy in this phase," remove the README/config claims so operators are not misled into expecting protection that does not exist.

## Warnings

### WR-01: The 64 KB body guard does not block oversized bodies — it relies on a spoofable header and silently passes when absent

**File:** `brave/api/routers/tripadvisor_session.py:187-205`
**Issue:** The size check (T-12-02-03) reads `request.headers.get("content-length")` and only enforces the limit when the header is present and parses as an int. Three gaps:
1. A client sending chunked transfer-encoding (no `Content-Length`) bypasses the check entirely — `body: SessionInjectBody` is parsed before the guard ever sees the bytes (FastAPI resolves the body model before the function executes), so by the time line 187 runs, the full payload was already read into memory and validated.
2. The order is wrong: the `body: SessionInjectBody` parameter is materialized at call entry, so the "size-check before parse" intent in the docstring (step 1 → step 2) is not what actually happens. The guard runs *after* Pydantic parsing.
3. A malicious/incorrect `Content-Length` smaller than the real body still passes.

The accompanying test (`test_inject_body_size_limit`) passes only because the 70 KB cookie value also triggers Pydantic/transport limits — it does not prove the guard works.

**Fix:** Either rely on a server/ASGI-level body-size limit (uvicorn `--limit-max-requests` is not this; use a middleware that enforces max body size before parsing), or read the raw body with `await request.body()` in the guard *before* declaring `body` as a parsed param. At minimum, do not advertise a guarantee the code does not provide.

### WR-02: Canary misclassifies infrastructure errors as `invalid_session` and deletes a possibly-valid key

**File:** `brave/api/routers/tripadvisor_session.py:139-157`
**Issue:** `_run_canary` runs `client.fetch_destinations("RJ")`, which first calls `resolve_geo_id("RJ", ...)`. That can raise `ValueError` (unknown UF / missing seed JSON), and the httpx client can raise `httpx.ConnectError`/timeout for reasons unrelated to the session (DNS, proxy down, Redis unreachable inside `resolve_geo_id`). All of these land in the generic `except Exception` branch, which (a) deletes the freshly-written session key and (b) returns `422 invalid_session`. An operator who just injected a valid session will be told the session is invalid when the real fault is server-side infrastructure, and the valid session is destroyed — forcing a needless re-capture of a scarce, manually-obtained credential.

**Fix:** Distinguish "session is provably bad" (403/429/empty result) from "we could not complete the canary" (connectivity/resolution). For the latter, do NOT delete the key; return a 503/`canary_unverified` so the operator can retry without re-capturing:

```python
except (SessionExpiredError, asyncio.TimeoutError) as exc:
    redis.delete(BRAVE_TA_SESSION_KEY)
    ...
    raise HTTPException(422, detail="invalid_session") from exc
except Exception as exc:
    # Infra error — do NOT destroy a possibly-valid session
    logger.warning("ta_session_canary_unverified", reason=type(exc).__name__, ...)
    raise HTTPException(503, detail="canary_unverified") from exc
```

### WR-03: Frontend/backend `expires_in` contract drift — backend can return `0`, frontend renders "0 min" as if healthy

**File:** `brave/api/routers/tripadvisor_session.py:305` and `dashboard/components/engine/EngineControl.tsx:315-319`
**Issue:** The backend returns `expires_in=max(ttl_seconds, 0)`. `redis.ttl()` returns `-1` (key exists, no expiry) or `-2` (key absent) in edge cases; `max(..., 0)` collapses both to `0`. The frontend then renders `({Math.round(sessionStatus.expires_in / 60)} min)` → "(0 min)" beside a green "Pronta" pill, telling the operator the session is ready with 0 minutes left. A `-1` (no-TTL key, which can happen if a session was written without `setex`) becomes a misleading "0 min" rather than "no expiry". The pill colour (`sessionColor`) keys only on `present`, so a 0-second session still shows emerald/healthy.

**Fix:** In the backend, treat `ttl_seconds <= 0` as "expired/unknown" and either omit `expires_in` or surface it distinctly. In the frontend, guard `expires_in > 0` before rendering the minutes badge, and consider an amber pill when `expires_in` is low.

### WR-04: `reason` is required (non-optional) in the frontend type but the backend response model defaults it — and `query_ids` shape disagrees

**File:** `dashboard/lib/engine-api.ts:112-117` vs `brave/api/routers/tripadvisor_session.py:95-101`
**Issue:** Two contract mismatches:
1. The TS interface declares `reason: "needs_bootstrap" | null` (required), but the Pydantic model defaults `reason=None` and `present` responses always set it to `None`. This happens to align, but the TS type also omits `reason` being potentially absent — fine today, fragile if the backend later drops the field. Minor.
2. More concrete: backend `query_ids: list[str] | None` returns the **keys** of the query-id map (`["destinations", "attractions"]`), while the field name and the `engine-api.ts` comment ("query_ids: {...}" in README step 9) imply a dict. The README at `data/tripadvisor/README:165` documents the status response as `"query_ids": {...}` (a dict), but the implementation returns a list. Operators/consumers following the README will mis-parse.

**Fix:** Pick one shape and make the README, the Pydantic model, and the TS type agree. The list-of-keys is the safer (no-value-leak) choice — update the README example to `"query_ids": ["destinations", "attractions"]`.

### WR-05: Audit-skip swallows all exceptions and can mask a real audit-trail failure

**File:** `brave/api/routers/tripadvisor_session.py:225-260`
**Issue:** The audit block is wrapped in nested `try/except Exception` with the outer one logging `ta_session_audit_skip` and continuing. The injection of a production-write credential is a security-relevant action; silently dropping its audit record (e.g., because the audit table is missing or the DB is down) on a *successful* inject means there is no record that a session was installed. For a credential-injection endpoint this weakens the compliance/audit posture (LGPD/§15.7 audit-log requirement referenced in CLAUDE.md). The double-nested structure also makes the control flow hard to follow and the inner `db_gen.close()` is duplicated in both branches.

**Fix:** Keep the inject succeeding, but emit a structured `logger.error` (not `warning`) on audit failure so the gap is alertable, and simplify to a single `try/finally` with one `db_gen.close()`. Consider a metric/counter for audit-write failures on this endpoint.

### WR-06: Canary uses `asyncio.wait_for` timeout but the underlying httpx call is not cancellation-clean

**File:** `brave/api/routers/tripadvisor_session.py:125-128`
**Issue:** `asyncio.wait_for(client.fetch_destinations("RJ"), timeout=15.0)` will raise `TimeoutError` and abandon the in-flight `fetch_destinations` coroutine, but `fetch_destinations` paginates up to `_MAX_PAGES=50` with a fresh `httpx.AsyncClient` per page inside `async with`. On cancellation mid-page the context manager unwinds correctly, but a slow first request that exceeds 15s leaves no partial state — that's fine — yet a 200-but-slow TripAdvisor (paginating many pages) could legitimately exceed 15s and be killed, deleting a *valid* session (same root cause as WR-02). For a destinations canary, 50 pages × network latency can plausibly exceed 15s for a large UF.

**Fix:** Bound the canary to a single page (the canary only needs to prove the session returns *any* data). Add a `max_pages`/`limit` parameter to `fetch_destinations` or run a dedicated single-request probe rather than the full paginating fetch.

### WR-07: `ta_bootstrap.py` prints validation-error response bodies to stderr — may echo cookie/credential context

**File:** `scripts/ta_bootstrap.py:190-194`
**Issue:** On a 422 the script prints `Response: {resp_body}` to stderr. The server's `invalid_session` detail is currently safe, but if the server ever returns Pydantic validation errors (e.g., the 422 from `extra="forbid"` or malformed-body cases), FastAPI's default 422 body echoes the offending input values — which for this endpoint includes the submitted **cookies** and **query_ids**. The operator's terminal/CI log would then capture live credential material. The README explicitly treats the cURL as a short-lived credential not to be persisted; echoing the rejected body undercuts that.

**Fix:** Do not print the raw response body on validation failure. Print a fixed message ("Validation error — check cookies/query_ids; see server logs") and rely on the server's redacted logs for detail.

## Info

### IN-01: f-string with no placeholders

**File:** `scripts/ta_bootstrap.py:191`
**Issue:** `print(f"Validation error — check cookies/query_ids")` uses an f-string prefix with no interpolation. Ruff (`F541`) flags this.
**Fix:** Drop the `f` prefix: `print("Validation error — check cookies/query_ids")`.

### IN-02: Stale docstring references a removed Playwright bootstrap

**File:** `brave/config/settings.py:235`
**Issue:** `TripAdvisorConfig`'s docstring still says it "Controls the Playwright DataDome session bootstrap" — but Phase 12 removed Playwright entirely (asserted by `test_no_playwright_at_module_level`). The `query_id_override` description (line 267) likewise references "the live-capture bootstrap." Misleading for the next maintainer.
**Fix:** Update the docstrings to describe the operator-injection model. (Not in the listed file set but directly contradicted by this phase; flag for follow-up.)

### IN-03: `query_id_override` config field is dead — never read by the refactored client

**File:** `brave/lanes/tripadvisor/client.py:142, 206`
**Issue:** The client reads the query id from `session.get("query_ids", {})` (Redis-injected) only; `config.query_id_override` (settings.py:263) is never consulted, so the documented override path no longer works. Either wire it as a fallback/override over the injected value or remove the config field to avoid implying behaviour that does not exist.
**Fix:** Decide override precedence and either apply `self._config.query_id_override` or delete the field + its README/docstring references.

### IN-04: `_run_canary` instantiates a second `TripAdvisorClient` against the same Redis, duplicating the sweep's construction

**File:** `brave/api/routers/tripadvisor_session.py:121-123`
**Issue:** The canary builds a `TripAdvisorClient(config=ta_config, redis=redis)` while `sweep_tripadvisor` builds its own with a freshly `from_url`'d Redis. Minor duplication; acceptable, but note the canary uses the request-scoped `get_redis()` client whereas the sweep uses an env-derived one — if they ever diverge (different DB index), the canary would validate a key the worker cannot see. Worth a comment asserting they target the same Redis.
**Fix:** Add a comment documenting the shared-Redis assumption, or centralize client construction.

### IN-05: `lane` field in `startEngine` body is never sent by `EngineControl`

**File:** `dashboard/lib/engine-api.ts:84-97` and `dashboard/components/engine/EngineControl.tsx:118-128`
**Issue:** `startEngine` accepts an optional `lane?: "destinos" | "atrativos" | "both"`, but `EngineControl` only ever sends `depth`, `source`, and (for TripAdvisor) `ufs`. The `lane` field is dead surface in the typed fetcher.
**Fix:** Remove `lane` from the `startEngine` signature if the backend defaults it, or document why it is retained.

---

_Reviewed: 2026-06-24_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
