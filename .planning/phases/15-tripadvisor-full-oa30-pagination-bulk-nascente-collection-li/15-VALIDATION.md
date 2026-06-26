---
phase: 15
slug: tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
status: planned
nyquist_compliant: true
wave_0_complete: false
created: 2026-06-26
---

# Phase 15 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x (backend, 100% offline default) · Vitest 4.x + MSW (dashboard) |
| **Config file** | `pyproject.toml` / `pytest.ini` (backend); `dashboard/vitest.config.*` (dashboard) |
| **Quick run command** | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor -q` |
| **Full suite command** | `.venv/bin/python -m pytest -q` then `cd dashboard && bun run test` |
| **Estimated runtime** | ~30–90 seconds (unit); dashboard ~20s |

**Backend tests run with `RUN_REAL_EXTERNALS` UNSET; integration tests need `BRAVE_DB_URL`. Dashboard via `cd dashboard && bun run test`.**

---

## Sampling Rate

- **After every task commit:** Run the quick command for the touched area.
- **After every plan wave:** Run the full suite (backend + dashboard).
- **Before `/gsd:verify-work`:** Full suite must be green with `RUN_REAL_EXTERNALS` UNSET.
- **Max feedback latency:** ~90 seconds.

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 15-01-T1 | 15-01 | 1 | TA-12 | T-15-01 | Fixture scrubbed of datadome/cookies/PII | grep gate | `test -s tests/fixtures/tripadvisor/attractions_oa30.html && grep -q WebPresentation_SingleFlexCardSection ... && ! grep -Eiq 'datadome=\|set-cookie\|__secure-\|sessionid=' ...` | ❌ W0 (operator capture) | ⬜ pending |
| 15-02-T1 | 15-02 | 1 | TA-12 | T-15-02-01 | Null client yields nothing, no network | unit | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_client.py tests/unit -k "protocol or compliance or fake_tripadvisor" -q` | ✅ extend | ⬜ pending |
| 15-02-T2 | 15-02 | 1 | TA-12 | T-15-02-02 | geocode_national 4-key LGPD shape | unit | `.venv/bin/python -m pytest tests/unit -k "geocod or nominatim or compliance" -q` | ✅ extend | ⬜ pending |
| 15-03-T1 | 15-03 | 1 | TA-12 | T-15-03-02 | Progress hash carries no secrets | unit (fakeredis) | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_sweep_progress.py -x -q` | ❌ W0 | ⬜ pending |
| 15-03-T2 | 15-03 | 1 | TA-12 | T-15-03-01 | Endpoint bearer/steward-guarded; 401 fail-closed | unit | `.venv/bin/python -m pytest tests/unit/api/test_tripadvisor_session.py -k "sweep_progress or progress or auth" -x -q` | ✅ extend | ⬜ pending |
| 15-04-T1 | 15-04 | 2 | TA-12 | T-15-04-03 | Extractor reuses parser (LGPD aggregate-only); no DOM parser dep | unit (fixture) | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_pagination.py -k extract -x -q` | ❌ W0 (needs fixture) | ⬜ pending |
| 15-04-T2 | 15-04 | 2 | TA-12 | T-15-04-01/02/04 | No cookie/proxy logging; int-only URL (non-int geoId rejected); 334-page/oa9990 loop clamp; throttle + 403 fail-fast | unit (respx) | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_pagination.py tests/unit/lanes/tripadvisor/test_client.py -k "paginated or max_pages or throttle or contract or cap or geoid" -x -q` | ❌ W0 | ⬜ pending |
| 15-05-T1 | 15-05 | 2 | TA-12 | T-15-05-01/02 | National geocode 4-key shape; rate-limit + cache | unit (respx+fakeredis) | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_geo_national.py -k geocode_national -x -q` | ❌ W0 | ⬜ pending |
| 15-05-T2 | 15-05 | 2 | TA-12 | T-15-05-01 | National resolver pure math; uf derived from match | unit | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_geo_national.py -k resolve_national -x -q` | ❌ W0 | ⬜ pending |
| 15-08-T1 | 15-08 | 2 | TA-12 | T-15-08-01 | BFF double-prefix; read via authenticated BFF | unit (vitest/MSW) | `cd dashboard && bun run test TASweepProgress` | ❌ W0 | ⬜ pending |
| 15-08-T2 | 15-08 | 2 | TA-12 | T-15-08-02 | 10s poll cadence; 401-safe render | unit (vitest/MSW) | `cd dashboard && bun run test TASweepProgress` | ❌ W0 | ⬜ pending |
| 15-06-T1 | 15-06 | 3 | TA-12 | T-15-06-01/04 | LGPD aggregate-only; bypass parent gate, §7.6+DLQ still gate | unit (fakes+savepoint) | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_atrativos_bulk.py -k ingest_bulk -x -q` | ❌ W0 | ⬜ pending |
| 15-06-T2 | 15-06 | 3 | TA-12 | T-15-06-02/03 | Per-page commit before progress; record_error wired (error_count>0 on failure); no-secret logging grep gate | unit (fakes+fakeredis) | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_atrativos_bulk.py -k produce_paginated -x -q` | ❌ W0 | ⬜ pending |
| 15-07-T1 | 15-07 | 4 | TA-12 | T-15-07-01/03/04 | Error-class-only logs; depth gating, no auto-Mar/WhatsApp; rc=None guard (no per-UF UnboundLocalError) | unit + CLI | `.venv/bin/python -m pytest tests/unit/tasks/test_sweep_tripadvisor.py -x -q && .venv/bin/python scripts/ta_bulk_sweep.py --help >/dev/null` | ✅ extend | ⬜ pending |
| 15-07-T2 | 15-07 | 4 | TA-12 | T-15-07-02/04 | Per-page durability + resume; no retry storm; per-UF guarded-except regression | unit (fakes+fakeredis+savepoint) | `.venv/bin/python -m pytest tests/unit/tasks/test_sweep_tripadvisor.py -x -q` | ✅ extend | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/fixtures/tripadvisor/attractions_oa30.html` — one real captured AttractionsFusion HTML page, **scrubbed of PII/cookies/keys** (15-01). BLOCKS the extractor (15-04).
- [ ] `tests/unit/lanes/tripadvisor/test_pagination.py` — extractor + paginated fetch (15-04).
- [ ] `tests/unit/lanes/tripadvisor/test_sweep_progress.py` — progress + resume (15-03).
- [ ] `tests/unit/lanes/tripadvisor/test_geo_national.py` — national geocode + national resolver (15-05).
- [ ] `tests/unit/lanes/tripadvisor/test_atrativos_bulk.py` — bulk ingest + paginated producer (15-06).
- [ ] Dashboard: `dashboard/mocks/handlers/ta-sweep.ts` + `TASweepProgress.test.tsx` panel coverage (15-08).
- [x] Open Question 1 (national UF / parent gate) RESOLVED: bulk lane bypasses the parent-destino gate and derives uf+município via `geocode_national` + `resolve_municipio_national` (15-05 / 15-06).

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real slice (~5–10 pages) reaches Nascente against live TripAdvisor | TA-12 | Hits live TA + DataDome; cannot run in CI (RUN_REAL_EXTERNALS opt-in, needs operator session + BRAVE_DB_URL) | Inject session, run `scripts/ta_bulk_sweep.py --start-page 1 --max-pages 5` with RUN_REAL_EXTERNALS=1, confirm Nascente attraction rows > 0 and the dashboard /processo panel shows live progress |
| DataDome endurance over sequential page requests | TA-12 | Anti-bot behavior only observable live | Watch the slice run for 403/429; confirm fail-fast records resume offset + panel shows stopped_needs_bootstrap |
| HTML DataDome surface vs GraphQL canary (Pitfall 2 / A3) | TA-12 | Only observable live | If page 1 of the slice 403s despite a green session pill, update `data/tripadvisor/README` capture instructions |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (HTML fixture is the critical one)
- [x] No watch-mode flags
- [x] Feedback latency < 90s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** ready for execution
