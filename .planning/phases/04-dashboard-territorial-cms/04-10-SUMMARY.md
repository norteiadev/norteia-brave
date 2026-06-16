---
phase: 04-dashboard-territorial-cms
plan: 10
subsystem: gap-closure-dash-03
tags: [dash-03, gap-closure, whatsapp-gate, ramp-context, quality-rating, d-01, read-only, bearer, fakeredis, msw, vitest]

# Dependency graph
requires:
  - phase: 04-dashboard-territorial-cms
    plan: 06
    provides: "RampContext.tsx panel + gate-api.ts fetchRampContext (wired to GET /api/v1/atrativos/whatsapp/ramp-context) + MSW ramp handlers"
  - phase: 03-atrativos-lane-whatsapp-compliance
    plan: 03
    provides: "compliance gate ramp counter (wa:ramp:{date} keys) + wa:quality_red flag"
provides:
  - "GET /api/v1/atrativos/whatsapp/ramp-context — read-only Bearer-guarded ramp + quality context endpoint (closes the DASH-03 hollow panel)"
  - "brave.compliance.gate.ramp_key — shared ramp-key helper (single source of truth for read + write paths)"
affects:
  - "dashboard RampContext panel now renders real data on the happy path instead of the 'indisponível' fallback"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Read-only D-01 context endpoint: Redis-only (no DB), Bearer-guarded, returns aggregate counters; 401 before any Redis read"
    - "Shared ramp_key() helper so the read path (context endpoint) and write path (check_and_increment_ramp INCR) can never drift onto divergent key formats"
    - "Response returns BOTH the requirement contract field names (daily_cap/used/remaining/quality) AND the frontend RampQualityContext aliases (ramp_cap/ramp_used/ramp_remaining/quality_rating/paused) so the existing panel renders without a frontend path/type change"

key-files:
  created:
    - dashboard/components/gate/__tests__/RampContext.test.tsx
  modified:
    - brave/compliance/gate.py
    - brave/api/routers/atrativos_gate.py
    - tests/integration/test_dashboard_endpoints.py
    - .planning/STATE.md

decisions:
  - "Endpoint lives in atrativos_gate.py (the natural /atrativos/whatsapp/* namespace home) rather than dashboard.py — Bearer-guarded with require_bearer (the dashboard read gate), not steward"
  - "Extracted a shared ramp_key() helper into gate.py and re-pointed check_and_increment_ramp at it, so the read-only context endpoint mirrors the EXACT write-path key format instead of reimplementing it"
  - "READ-ONLY: redis.get only — never INCR/DECR. The ramp is enforced server-side in the Phase 3 send path; this view is advisory display only (T-04-20, no UI bypass). A dedicated test asserts the counter is never mutated by the endpoint"
  - "Response is a superset: requirement field names + the frontend's already-expected RampQualityContext aliases, so the frontend fetcher/path/type are unchanged (the gap was purely a missing backend endpoint)"

requirements-completed: [DASH-03]

# Metrics
duration: ~20min
completed: 2026-06-16
---

# Phase 4 Plan 10: DASH-03 Gap Closure (Ramp/Quality Context Endpoint) Summary

**Closes the DASH-03 verification gap: the dashboard `RampContext` panel was wired-but-hollow — `gate-api.ts:fetchRampContext` fetched `GET /api/v1/atrativos/whatsapp/ramp-context`, an endpoint the backend never exposed, so the panel always rendered the degraded "indisponível" fallback. This adds the missing read-only, Bearer-guarded D-01 endpoint that reads today's `wa:ramp:{date-UTC}` counter (via a new shared `ramp_key` helper) + the `wa:quality_red` flag and returns `daily_cap/used/remaining/quality` (plus the frontend's expected `RampQualityContext` aliases), so the panel now renders real GREEN/RED + cap data. Proven offline: 6 backend pytest (401-before-work, happy-path shape, RED flag, absent-key→used=0, read-only never-mutates, per-UF) + 3 frontend Vitest+MSW (real data on happy path, RED destructive state, fallback only on fetch error). `bunx tsc --noEmit` clean.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 1 gap-closure unit (backend endpoint + shared helper + frontend test)
- **Files:** 1 created (RampContext.test.tsx), 3 modified (gate.py, atrativos_gate.py, test_dashboard_endpoints.py)

## Accomplishments

### Backend — shared ramp-key helper (`brave/compliance/gate.py`)
- Added `ramp_key(uf=None)` — the single source of truth for the `wa:ramp:{YYYY-MM-DD}` (global) / `wa:ramp:{UF}:{YYYY-MM-DD}` (per-UF) UTC-day key format.
- Re-pointed `check_and_increment_ramp` (the write path / condition 7 INCR) at `ramp_key(uf)` so the read path (the new context endpoint) and the write path can never diverge.

### Backend — read-only context endpoint (`brave/api/routers/atrativos_gate.py`)
- `GET /api/v1/atrativos/whatsapp/ramp-context` — Bearer-guarded (`require_bearer`), Redis-only (no DB).
  - `redis.get(ramp_key(None))` → `used` (0 if key absent), `remaining = max(0, daily_cap - used)`.
  - `is_quality_red(redis)` → `quality` `"RED"|"GREEN"`.
  - Cap from `RampConfig.daily_cap` (`config.ramp.daily_cap`, env `BRAVE_WA_RAMP_DAILY_CAP`, default 50).
  - Optional `?uf=` → `per_uf: {uf, used, remaining}` from the per-state key.
  - Returns BOTH the requirement field names (`daily_cap/used/remaining/quality`) AND the frontend `RampQualityContext` aliases (`ramp_cap/ramp_used/ramp_remaining/quality_rating/paused`) — so the existing panel renders unchanged.
  - **Read-only:** `redis.get` only, never INCR/DECR — the ramp counter is never mutated.

### Frontend — RampContext renders real data (`dashboard/components/gate/RampContext.tsx`)
- No source change required: the panel and `gate-api.ts` fetcher already consumed `RampQualityContext` (`ramp_remaining/ramp_used/ramp_cap/quality_rating/paused`) and the MSW happy/RED/error handlers already existed (from plan 06). The gap was purely the missing backend endpoint; the response intentionally returns those exact aliases so the panel now flows real data.
- Added `dashboard/components/gate/__tests__/RampContext.test.tsx` proving: happy path renders the real GREEN badge + cap numbers (NOT the "indisponível" fallback); RED applies the destructive badge background + section border + auto-pause copy; the fallback appears ONLY when the fetch errors.

## Verification

- `BRAVE_USE_FAKEREDIS=1 .venv/bin/python -m pytest tests/integration/test_dashboard_endpoints.py -k ramp_context` → **6 passed** (401-before-work, happy-path shape, absent-key→used=0, RED flag, read-only never-mutates, per-UF).
- `.venv/bin/python -m pytest tests/integration/test_dashboard_endpoints.py -m "not integration"` → **30 passed** (offline dashboard suite, no regression).
- `.venv/bin/python -m pytest tests/ -k "gate or compliance or ramp"` → **51 passed** (gate/compliance/ramp, no regression from the shared `ramp_key` refactor).
- `cd dashboard && bunx vitest run` → **14 files, 82 tests passed** (was 79; +3 RampContext).
- `cd dashboard && bunx tsc --noEmit` → **clean (exit 0)**.
- `ruff check` on the three modified Python files → no new errors introduced (import block auto-sorted; the 2 remaining warnings — `gate.py` inline `ConsentLog` import location and a pre-existing unused `rio` in an unrelated funnels test — are pre-existing and out of scope).

## Deviations from Plan

### Auto-fixed / minor

**1. [Rule 3 — Blocking] Extracted a shared `ramp_key()` helper instead of duplicating the key format**
- **Found during:** implementing the read endpoint.
- **Issue:** The requirement says to "reuse the SAME date-key format as `check_and_increment_ramp` … import/share the key helper if one exists, else mirror it exactly." No helper existed — the format was inline in `check_and_increment_ramp`.
- **Fix:** Added `ramp_key(uf)` to `gate.py` and re-pointed `check_and_increment_ramp` at it, so read and write share one definition (no divergence risk). Existing ramp tests still pass.
- **Files:** `brave/compliance/gate.py`.

All else executed as written; the frontend needed no source change (the gap was purely the missing backend endpoint).

## Threat Model Compliance

- **Read-only ramp (T-04-20 — Tampering / ramp bypass):** mitigated/honored — the endpoint performs `redis.get` only; it NEVER INCRs or DECRs the ramp counter. The ramp is enforced exclusively in the Phase 3 send path (`send_path_gate` condition 7). A dedicated test (`test_ramp_context_is_read_only_never_mutates_counter`) calls the endpoint 3× and asserts the counter is unchanged — there is no UI path to consume or inflate ramp budget.
- **Bearer at the edge (T-04-01/02):** the endpoint is `require_bearer`-guarded; the no-Bearer 401 fires before any Redis read (`test_ramp_context_no_bearer_returns_401`). Same fail-closed, constant-time `hmac.compare_digest` discipline as the rest of the dashboard read surface.
- **No PII / no secrets:** the response is aggregate counters + a GREEN/RED string only — no record-level data, no phone numbers, no tokens. No logging of secrets.

## Threat Flags

None — no new security surface beyond a read-only, Bearer-guarded aggregate-counter GET (it replaces the previously-assumed `new-endpoint-assumed` flag from plan 06 with a real, guarded implementation).

## Known Stubs

None — `RampContext` is now fully data-backed end-to-end: the panel → `fetchRampContext` → BFF → the real `GET /api/v1/atrativos/whatsapp/ramp-context` reading live Redis ramp + quality state. The "indisponível" fallback now appears only on a genuine fetch failure (advisory graceful degradation), not unconditionally.

## Self-Check: PASSED

- `dashboard/components/gate/__tests__/RampContext.test.tsx` exists (FOUND).
- `brave/compliance/gate.py` `ramp_key` helper + `brave/api/routers/atrativos_gate.py` `get_ramp_context` endpoint present (FOUND).
- 6 backend ramp-context tests + 3 frontend RampContext tests green; full frontend suite 82 passed; tsc clean.

---
*Phase: 04-dashboard-territorial-cms*
*Completed: 2026-06-16*
