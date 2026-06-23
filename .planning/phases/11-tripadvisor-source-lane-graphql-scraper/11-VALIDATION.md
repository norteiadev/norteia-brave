---
phase: 11
slug: tripadvisor-source-lane-graphql-scraper
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-23
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution. Derived from 11-RESEARCH.md "## Validation Architecture". 100% offline by default; live scrape only via opt-in `@pytest.mark.real_browser`.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework (backend)** | pytest 9.x (`.venv/bin/python -m pytest`) |
| **Framework (dashboard)** | Vitest 4.x + MSW 2.x (`cd dashboard && bun run test`) |
| **Config file** | `pyproject.toml` (pytest) · `dashboard/vitest.config.ts` |
| **Quick run command** | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor/ -q` |
| **Full suite command** | `.venv/bin/python -m pytest -q && cd dashboard && bun run test` |
| **Estimated runtime** | ~30–60 s backend unit · ~20 s dashboard |
| **Offline guard** | `RUN_REAL_EXTERNALS` unset; `pytest-socket` blocks network; Playwright not installed in CI (`scraper` optional dep) |

---

## Sampling Rate

- **After every task commit:** quick command for the touched layer.
- **After every plan wave:** full suite (backend + dashboard).
- **Before `/gsd:verify-work`:** full suite green; `alembic upgrade head && alembic downgrade -1` round-trips 0006.
- **Max feedback latency:** ~60 s.

---

## Per-Task Verification Map (by requirement/wave — planner emits exact task IDs)

| Plan | Wave | Requirement | Secure/Correct Behavior | Test Type | Automated Command | Status |
|------|------|-------------|-------------------------|-----------|-------------------|--------|
| 11-01 | 1 | TA-01 | Geo cache hit/miss/seed-fallback; httpx GraphQL parse via respx; `403→re-bootstrap→200` session-expiry (Playwright mocked); `NullTripAdvisorClient` no-op + no Playwright import | unit | `pytest tests/unit/lanes/tripadvisor/test_client.py tests/unit/lanes/tripadvisor/test_geo.py -q` | ⬜ pending |
| 11-02 | 2 | TA-02/03/04 | Producers write Nascente (Fake client + JSON fixtures); IBGE resolver (accents, São/Sao, haversine fallback, no-match→quarantine); parent-via-RioRecord + `parent_destino_absent`; LGPD schema has no author/text fields | unit | `pytest tests/unit/lanes/tripadvisor/test_producers.py tests/unit/lanes/tripadvisor/test_ibge.py -q` | ⬜ pending |
| 11-02 | 2 | TA-04/05 | **Scoring proofs**: typical 200rev/4.5★/~5mo → 67.06 → `dlq`; sparse → 27.5 → `descarte`; val=100 → ~82 < 85 (never auto-Mar); `mar_ready` set ONLY when attraction + `tripadvisor:` + atualidade≥70 + corrob≥bar; False for every other source | unit | `pytest tests/unit/lanes/tripadvisor/test_scoring.py tests/unit/core/test_route_mar_ready.py -q` | ⬜ pending |
| 11-03 | 3 | TA-05/06 | Migration 0006 up/down round-trip; `engine_sweep_run(source="tripadvisor")` dispatches `sweep_tripadvisor` per UF; `/engine/start` source 202 + echo / invalid 422; promote single → MarRecord + `promotion_reason` provenance + `push_attraction_task` dispatched; non-`mar_ready` → 409; promote-batch promotes N per UF; audit rows written | unit/integration | `pytest tests/unit/api/test_engine_source.py tests/unit/api/test_promote_override.py tests/unit/core/test_promote_service.py -q` | ⬜ pending |
| 11-04 | 4 | TA-06/07 | EngineControl source radiogroup renders + sends `source`+`ufs` + active-source read-back; `/mar-ready` lists `mar_ready`; single + bulk multi-select promote optimistic with rollback on error | component | `cd dashboard && bun run test` | ⬜ pending |
| 11-05 | 5 | TA-08 | `data/tripadvisor/README` + lane docstring + root `SOURCES.md` exist with legal-risk note | source-assertion | `test -f data/tripadvisor/README && test -f SOURCES.md && grep -q ToS data/tripadvisor/README` | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/lanes/tripadvisor/conftest.py` — shared fixtures (Fake client, captured TA GraphQL JSON fixtures under `tests/fixtures/tripadvisor/`).
- [ ] `tests/fakes/fake_tripadvisor.py` — `FakeTripAdvisorClient(fixtures=…)` recording `.calls` (built in 11-01).
- [ ] `rapidfuzz` core dep + `scraper` optional group in `pyproject.toml` (11-01) — needed before 11-02 IBGE tests.

*Existing pytest + Vitest+MSW infrastructure otherwise covers all phase requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live DataDome bootstrap + queryId capture + real scrape of one UF | TA-01 | Hits real TripAdvisor; anti-bot/proxy reality unverifiable offline (RESEARCH §1 open questions) | `RUN_REAL_EXTERNALS=1 pytest -m real_browser tests/unit/lanes/tripadvisor/test_live_scrape.py` — confirm cookies+queryId captured, JSON parsed, records route to DLQ |
| End-to-end operator flow | TA-06/07 | UI + engine + DB across services | `make pipeline/up` + migrate + worker + uvicorn → `/processo` source=TripAdvisor + 1 UF + depth `nascente_rio` → start → nascente>0, rio dlq>0, mar=0; `/mar-ready` → promote one → MarRecord appears |

---

## Validation Sign-Off

- [ ] All tasks have automated verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
