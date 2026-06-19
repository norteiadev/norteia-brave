---
phase: 08-ops-cms-destinos-atrativos-crud-process-observability-cores
verified: 2026-06-19T10:10:00Z
status: passed
score: 13/13 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 12/13
  gaps_closed:
    - "D-06: /processo page includes JourneyStepper in compact mode (compact=true) for a sample atrativo summary showing pipeline state at a glance"
  gaps_remaining: []
  regressions: []
  gap_closure_commit: "4c8d383"
---

# Phase 8: Ops CMS + Process Observability Verification Report

**Phase Goal:** Give operators a visual, browsable CMS over all pipeline records (destinos + atrativos across every stage) and 24/7 process observability (workers, failures, human-pending queue, journey to Mar) — backed by new FastAPI endpoints that compose existing service functions, reskinned with Norteia brand tokens, and exposed through a Next.js dashboard with StageBadge + JourneyStepper primitives.
**Verified:** 2026-06-19T10:10:00Z
**Status:** passed
**Re-verification:** Yes — after gap closure (commit 4c8d383)

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | D-03: GET /api/v1/destinos returns paginated items with routing/score/name/validation_pending fields and requires Bearer (401 without it) | VERIFIED | cms.py route confirmed: `list_destinos` with `Depends(require_bearer)`; returns `{items, total, offset, limit}`; 22 backend tests pass including `test_list_destinos_bearer_required` (401) and `test_list_destinos_with_bearer` (shape) |
| 2 | D-03: GET /api/v1/destinos/{id} returns score_breakdown, normalized, audit_log journey rows, and child_atrativos_count | VERIFIED | `get_destino_detail` at line 170 returns `score_breakdown`, `audit_log`, `normalized`, `child_atrativos`; `test_get_destino_detail` asserts all keys present |
| 3 | D-03: PATCH promote/descarte/reprocess all guarded with steward_or_bearer | VERIFIED | 7 PATCH endpoints all use `Depends(require_steward_or_bearer)`; auth tests cover 401 path; WR-01 fix applied — `db.commit()` before `push_destination_task.delay()` (cms.py line 293) |
| 4 | D-04: GET /api/v1/atrativos returns items filtered by uf/sub_state/parent_mar_id/routing with pagination; JSON subscript as_string() filter used | VERIFIED | `list_atrativos` uses `RioRecord.normalized["parent_mar_id"].as_string() == str(parent_mar_id)` for JSONB filter; test passes |
| 5 | D-04: Phone PII never returned raw — _safe_normalized applied to all atrativo response paths; email/ig_handle also dropped (CR-01 fix) | VERIFIED | `_safe_contacts` (cms.py line 58-82) builds an allow-list: website + phone_masked only; email and ig_handle explicitly dropped. `test_list_atrativos_pii_masked`, `test_get_atrativo_detail_contacts_masked`, and CR-01 email test all pass; grep confirms `email` only appears in masking/comment lines |
| 6 | D-04: PATCH advance returns 409 on expected_state mismatch; PATCH descarte sets routing=dlq + dlq_reason=steward_rejected_gate | VERIFIED | `advance_atrativo_state` calls `advance_sub_state(lock=True)` and maps `False` return to 409; `test_advance_atrativo_conflict` passes; descarte sets `routing="dlq"`, `dlq_reason="steward_rejected_gate"`, `sub_state=None` |
| 7 | D-05: GET /api/v1/workers handles broker-absent gracefully; GET /api/v1/failures returns {total, by_task, items} with payload never exposed | VERIFIED | workers.py verified: `inspect(timeout=1.0)` in try/except; `ping or {}` coercion; Redis LLEN in separate try/except; WR-02/WR-03 fixes applied (true total via COUNT query, by_task via full grouped query); WR-04 fix applied (`len(BRAVE_BEAT_SCHEDULE)` not hardcoded 54); `test_workers_broker_down` (200 + broker_reachable=False) and `test_failures_payload_not_exposed` pass |
| 8 | D-05: Both observability endpoints Bearer-guarded | VERIFIED | `require_bearer` on `GET /api/v1/workers` (line 33) and `GET /api/v1/failures` (line 88); `test_workers_bearer_required` and `test_failures_bearer_required` pass |
| 9 | D-01: globals.css :root sets --primary to oklch(0.23 0.10 253) (navy), --accent to oklch(0.48 0.12 30) (terracota), --background to oklch(0.98 0.01 90) (off-white); .dark updated; @theme inline unchanged | VERIFIED | Direct file read: line 57 `--primary: oklch(0.23 0.10 253)`, line 63 `--accent: oklch(0.48 0.12 30)`, line 51 `--background: oklch(0.98 0.01 90)`, line 92 `.dark --primary: oklch(0.28 0.10 253)`, line 98 `.dark --accent: oklch(0.52 0.12 30)`; `@theme inline` block at line 11 preserved |
| 10 | D-01: --status-in-progress, --status-success, --status-warning tokens added | VERIFIED | globals.css line 72: `--status-in-progress: var(--primary)`, line 73: `--status-success: var(--status-mar)`, line 74: `--status-warning: var(--status-dlq)` |
| 11 | D-02: StageBadge exported with routing (4 states) + sub_state (5 FSM states) + score band + source + validationPending using only CSS var tokens | VERIFIED | StageBadge.tsx: `ROUTING_CLASS` with mar/dlq/descarte/in_progress; `SUB_STATE_CLASS` with discovered/contacts_found/signals_gathered/aguardando_consulta_whatsapp/whatsapp_in_progress; `scoreClass` function; SOURCE_LABEL; all using `var(--status-*)` or `var(--color-primary)` — no hardcoded hex |
| 12 | D-06: JourneyStepper.tsx renders destino 4-step and atrativo 7-step journeys from auditLog + routing/subState props; compact prop available | VERIFIED | JourneyStepper.tsx (305 lines): `compact` prop on interface (line 31), compact horizontal render branch at line 221; destino steps (4) and atrativo steps (7) defined; imported by DetailPanel.tsx |
| 13 | D-06: /processo page includes JourneyStepper in compact mode (compact=true) for a sample atrativo summary showing pipeline state at a glance | VERIFIED | `dashboard/app/processo/page.tsx` line 13: `import { JourneyStepper } from "@/components/cms/JourneyStepper"`; line 129: `<JourneyStepper compact entityType="attraction" routing={sampleAtrativo.routing} subState={sampleAtrativo.sub_state} auditLog={[]} />` inside `<section data-testid="processo-journey">`; empty-state fallback "Nenhum atrativo na fila" on line 138. Two new tests in `dashboard/app/processo/__tests__/processo.test.tsx` assert compact stepper renders (aria-label "Pipeline journey") and empty state. Dashboard suite: 101 passed (17 test files). |
| 14 | D-07: Full suite 100% offline — backend 438 passed/1 skipped; dashboard 101 passed | VERIFIED | Directly executed: `unset RUN_REAL_EXTERNALS && .venv/bin/python -m pytest` → 438 passed, 1 skipped (unchanged); `cd dashboard && bun run test` → 101 passed, 17 test files (up from 99 — 2 new D-06 tests added) |

**Score:** 13/13 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `brave/api/routers/cms.py` | CMS CRUD router: 6 destinos + 5 atrativos endpoints | VERIFIED | 706 lines; all 11 routes confirmed by live Python import: `[r.path for r in router.routes]` |
| `brave/api/routers/workers.py` | Workers + failures observability router | VERIFIED | 140 lines; 2 routes confirmed |
| `brave/api/main.py` | Registers cms_router + workers_router | VERIFIED | Lines 48-52: import + include_router for both; all 19 new routes visible in app.routes |
| `dashboard/app/globals.css` | Norteia brand tokens in :root/.dark | VERIFIED | Navy primary, terracota accent, off-white background in oklch; 3 new status alias tokens |
| `dashboard/components/cms/StageBadge.tsx` | StageBadge component | VERIFIED | 142 lines; exports `StageBadge`; 4 routing states + 5 sub_states + scoreClass |
| `dashboard/components/cms/JourneyStepper.tsx` | JourneyStepper component with compact prop | VERIFIED | 305 lines; exports `JourneyStepper`; compact prop implemented at line 212 |
| `dashboard/app/page.tsx` | Nav entries for /destinos, /atrativos, /processo | VERIFIED | Lines 22-24: all 3 nav entries in SURFACES array |
| `dashboard/lib/destinos-api.ts` | Type-safe API client for destinos | VERIFIED | 108 lines; exports destinoKeys, fetchDestinoList, fetchDestinoDetail, promoteDestino, descarteDestino, reprocessDestino |
| `dashboard/components/cms/DestinoList.tsx` | TanStack Table with StageBadge | VERIFIED | 262 lines; imports StageBadge at line 12 |
| `dashboard/components/cms/DetailPanel.tsx` | Generic detail panel with JourneyStepper + ScoreBreakdownPanel | VERIFIED | 180 lines; imports JourneyStepper (line 6) and ScoreBreakdownPanel (line 8) |
| `dashboard/app/destinos/page.tsx` | Master-detail /destinos page | VERIFIED | 5.9K file exists |
| `dashboard/app/destinos/[id]/page.tsx` | Full-detail /destinos/[id] page | VERIFIED | Exists in directory |
| `dashboard/mocks/handlers/destinos.ts` | MSW handlers with double-prefix | VERIFIED | BASE = `http://localhost:3000/api/api/v1/destinos` confirmed |
| `dashboard/lib/atrativos-api.ts` | Typed API client for atrativos with phone_masked | VERIFIED | 120 lines; interface declares `phone_masked` not `phone_e164`; comment on line 34: "NEVER phone_e164" |
| `dashboard/components/cms/AtrativoList.tsx` | TanStack Table with sub_state StageBadge | VERIFIED | 271 lines |
| `dashboard/app/atrativos/page.tsx` | Master-detail /atrativos page | VERIFIED | 5.1K file exists |
| `dashboard/mocks/handlers/atrativos.ts` | MSW handlers with double-prefix; phone_masked only | VERIFIED | BASE = `http://localhost:3000/api/api/v1/atrativos`; grep for phone_e164 in handlers/atrativos.ts returns only PII contract comment lines, no raw values |
| `dashboard/lib/workers-api.ts` | Typed client + polling constant | VERIFIED | 82 lines; exports workersKeys, fetchWorkers, fetchFailures, WORKERS_REFETCH_INTERVAL_MS |
| `dashboard/components/processo/WorkerBoard.tsx` | Live-polled worker tiles with broker-down state | VERIFIED | 157 lines; broker-down banner at line 74 with role="alert" |
| `dashboard/components/processo/FailuresPanel.tsx` | PoisonQuarantine failures panel | VERIFIED | 131 lines |
| `dashboard/app/processo/page.tsx` | /processo page with WorkerBoard + FailuresPanel + human-pending + funnel + compact JourneyStepper | VERIFIED | WorkerBoard, FailuresPanel, DLQ+gate tiles, BarChart funnel all present; JourneyStepper compact wired at line 129 (commit 4c8d383) |
| `dashboard/app/processo/__tests__/processo.test.tsx` | Tests for D-06 compact JourneyStepper + empty state | VERIFIED | Two new tests added: "D-06: renders a compact JourneyStepper for a sample gate atrativo" and "D-06: journey section shows empty state when gate queue is empty" — both pass in 101-test suite |
| `dashboard/mocks/handlers/workers.ts` | MSW handlers with broker-down variant | VERIFIED | Double-prefix BASE_WORKERS + BASE_FAILURES; workersSuccess, workersBrokerDown, failuresSuccess |
| `tests/test_cms_endpoints.py` | pytest offline suite for CMS endpoints | VERIFIED | 547 lines (exceeded 80-line minimum); 22 tests all pass |
| `tests/test_workers_endpoints.py` | pytest offline suite for workers endpoints | VERIFIED | 369 lines; 10 tests all pass |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `brave/api/routers/cms.py` | `brave.core.dlq.service.validate_and_promote_rio` | lazy import inside `promote_destino` | VERIFIED | cms.py line 273: `from brave.core.dlq.service import validate_and_promote_rio` inside handler body |
| `brave/api/routers/cms.py` | `brave.lanes.atrativos.state_machine.advance_sub_state` | lazy import inside `advance_atrativo_state` | VERIFIED | cms.py line 606: `from brave.lanes.atrativos.state_machine import advance_sub_state` inside handler body |
| `brave/api/routers/cms.py` | `brave.api.deps.require_bearer / require_steward_or_bearer` | Depends() on all endpoints | VERIFIED | 11 endpoints: 4 GET with require_bearer, 7 PATCH with require_steward_or_bearer |
| `brave/api/routers/workers.py` | `brave.tasks.celery_app.app.control.inspect` | lazy import inside `get_workers` handler | VERIFIED | workers.py line 44: `from brave.tasks.celery_app import app as celery_app` inside handler |
| `brave/api/routers/workers.py` | `brave.api.deps.get_redis` | Depends(get_redis) on get_workers | VERIFIED | workers.py line 34: `redis: Redis = Depends(get_redis)` |
| `dashboard/components/cms/DestinoList.tsx` | `dashboard/components/cms/StageBadge.tsx` | import at line 12 | VERIFIED | `import { StageBadge } from "@/components/cms/StageBadge"` |
| `dashboard/components/cms/DetailPanel.tsx` | `dashboard/components/cms/JourneyStepper.tsx` | import at line 6 | VERIFIED | `import { JourneyStepper } from "@/components/cms/JourneyStepper"` |
| `dashboard/app/destinos/page.tsx` | `dashboard/lib/destinos-api.ts` | fetchDestinoList + destinoKeys | VERIFIED | Confirmed by bun test passing DestinoList tests |
| `dashboard/app/processo/page.tsx` | `dashboard/components/cms/JourneyStepper.tsx` | JourneyStepper import + compact usage (D-06) | VERIFIED | Line 13: `import { JourneyStepper } from "@/components/cms/JourneyStepper"`; line 129: `<JourneyStepper compact entityType="attraction" ...>` inside data-testid="processo-journey" section (commit 4c8d383) |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `brave/api/routers/cms.py list_destinos` | `rows` from `db.execute(stmt)` | SQLAlchemy query on RioRecord + MarRecord | Yes — LEFT JOIN with real JSONB + columns | FLOWING |
| `brave/api/routers/cms.py list_atrativos` | `rows` from `db.execute(stmt)` | SQLAlchemy query on RioRecord with JSON subscript filter | Yes — real DB query | FLOWING |
| `brave/api/routers/workers.py get_workers` | `ping/active/reserved` | Celery inspect (lazy import); Redis llen | Yes — live inspect or graceful None coercion; tests confirm both paths | FLOWING |
| `brave/api/routers/workers.py get_failures` | `rows` + `total` + `by_task` | PoisonQuarantine DB queries (3 separate queries) | Yes — WR-02/WR-03 fixes use real COUNT and GROUP BY | FLOWING |
| `dashboard/components/cms/DestinoList.tsx` | `query.data?.items` | `fetchDestinoList` via `apiFetch` BFF | Yes — fetcher calls `/api/v1/destinos`; MSW mocks for tests | FLOWING |
| `dashboard/components/processo/WorkerBoard.tsx` | `data` (WorkersData) | `fetchWorkers` via `apiFetch`; refetchInterval=10s | Yes — polling confirmed at line 30 | FLOWING |
| `dashboard/app/processo/page.tsx JourneyStepper` | `sampleAtrativo` from `gateItems?.[0]` | `fetchGateQueue` via `apiFetch`; same query already fetched for gate-count tile | Yes — derives from real gate endpoint query; empty-state fallback when queue is empty | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| cms.py imports cleanly and exports 11 routes | `.venv/bin/python -c "from brave.api.routers.cms import router; print([r.path for r in router.routes])"` | All 11 paths returned without ImportError | PASS |
| workers.py imports cleanly and exports 2 routes | `.venv/bin/python -c "from brave.api.routers.workers import router; print([r.path for r in router.routes])"` | `['/api/v1/workers', '/api/v1/failures']` | PASS |
| main.py registers both routers | `.venv/bin/python -c "from brave.api.main import app; [r.path for r in app.routes]"` | /api/v1/destinos, /api/v1/atrativos, /api/v1/workers, /api/v1/failures all present | PASS |
| Phase 8 backend tests pass | `unset RUN_REAL_EXTERNALS && .venv/bin/python -m pytest tests/test_cms_endpoints.py tests/test_workers_endpoints.py` | 32 passed | PASS |
| Full backend suite passes (no regressions) | `unset RUN_REAL_EXTERNALS && .venv/bin/python -m pytest` | 438 passed, 1 skipped | PASS |
| Dashboard suite passes (with D-06 tests) | `cd dashboard && bun run test` | 101 passed, 17 test files | PASS |
| phone_e164 never in cms.py atrativo response serialization | `grep "phone_e164" brave/api/routers/cms.py` | Only appears in masking guard, comment lines, and edit exclusion filter — not in response serialization | PASS |
| JourneyStepper compact used in /processo | `grep -n "compact\|JourneyStepper" dashboard/app/processo/page.tsx` | Line 13: import; line 129: `<JourneyStepper compact ...>` | PASS |

---

### Probe Execution

Step 7c: SKIPPED — no `probe-*.sh` files declared in any PLAN or discoverable via find for this phase.

---

### Requirements Coverage

| Requirement ID | Source Plan | Description | Status | Evidence |
|----------------|------------|-------------|--------|----------|
| D-01 | 08-03 | Norteia brand token swap in globals.css | SATISFIED | oklch(0.23 0.10 253) navy, oklch(0.48 0.12 30) terracota, 3 new status alias tokens, @theme inline preserved |
| D-02 | 08-03 | StageBadge component with routing/sub_state/score/source/validationPending | SATISFIED | StageBadge.tsx 142 lines, 4 routing + 5 sub_state + scoreClass + SOURCE_LABEL, CSS vars only |
| D-03 | 08-01, 08-04 | Destinos CRUD endpoints + frontend | SATISFIED | 6 endpoints in cms.py + destinos-api.ts + DestinoList + DetailPanel + pages + MSW + tests |
| D-04 | 08-01, 08-05 | Atrativos CRUD endpoints + frontend with PII masking | SATISFIED | 5 endpoints, _safe_contacts allow-list (CR-01 fixed), atrativos-api.ts phone_masked types, AtrativoList, pages, tests |
| D-05 | 08-02, 08-06 | Process observability: workers + failures + /processo page | SATISFIED | workers.py 2 endpoints; WorkerBoard + FailuresPanel + human-pending tiles + BarChart funnel; WR-02/WR-03/WR-04 applied |
| D-06 | 08-03, 08-06 | JourneyStepper + compact mode on /processo | SATISFIED | JourneyStepper.tsx with compact prop: VERIFIED. Compact mode on /processo page: VERIFIED (commit 4c8d383) — `<JourneyStepper compact entityType="attraction" ...>` wired to first gate-queue item with empty-state fallback |
| D-07 | 08-07 | 100% offline tests for all phase 8 backend endpoints + dashboard | SATISFIED | 32 backend tests pass (22 cms + 10 workers); dashboard 101 passed (17 files) — 2 new D-06 tests included |

**Orphaned requirements:** None — all D-01 through D-07 IDs are covered by at least one plan.

Note: D-01 through D-07 are phase-internal requirement IDs; they do not appear in the global REQUIREMENTS.md traceability table (which covers CORE/SCORE/OBS/CNTR/DEST/ATR/DASH/COMP/TEST/ORCH series through Phase 5). No REQUIREMENTS.md entries are mapped to Phase 8.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `dashboard/app/processo/page.tsx` | 42-44 | `HUMAN_PENDING_LIMIT = 500` + `dlqItems?.length` count — WR-06 acknowledged but not fully resolved | Warning (known) | Count is still derived from list length, not a real total. The "500+" guard is a partial mitigation. Not a blocker given this is an internal ops tool with bounded queue sizes. |
| `tests/test_workers_endpoints.py` | Around 250 | `beat_schedule.entries == 54` hardcoded assertion against dynamic schedule | Warning | If UF_LIST changes, test will fail. WR-04 fixed the endpoint to use `len(BRAVE_BEAT_SCHEDULE)`, but the test still asserts the literal `54`. |

**Debt markers:** None — no TBD/FIXME/XXX found in any phase 8 file.

---

### Human Verification Required

None — all behavioral claims are programmatically verifiable and were verified above.

---

### Gaps Summary

No gaps. All 13 must-have truths are verified.

The single gap from the initial verification (D-06 JourneyStepper compact on /processo) was closed in commit 4c8d383:

- `dashboard/app/processo/page.tsx` now imports `JourneyStepper` (line 13) and renders `<JourneyStepper compact entityType="attraction" routing={sampleAtrativo.routing} subState={sampleAtrativo.sub_state} auditLog={[]} />` inside `<section data-testid="processo-journey">` (line 129), using `gateItems?.[0]` as the sample record with an "Nenhum atrativo na fila" empty-state fallback when the queue is empty.
- Two new tests in `dashboard/app/processo/__tests__/processo.test.tsx` assert compact stepper renders (finds `[aria-label="Pipeline journey"]` inside the `processo-journey` section) and empty state text appears when the gate list is empty.
- Dashboard test suite increased from 99 to 101 passed; all 17 test files pass.

---

_Verified: 2026-06-19T10:10:00Z_
_Verifier: Claude (gsd-verifier)_
_Re-verification after gap closure: commit 4c8d383_
