---
phase: 15
slug: tripadvisor-full-oa30-pagination-bulk-nascente-collection-li
status: draft
nyquist_compliant: false
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
| TBD (planner fills) | — | 0 | TA-12 | T-15-* | datadome/session cookies never logged | unit | `.venv/bin/python -m pytest tests/unit/lanes/tripadvisor -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*
*Planner: complete this map — one row per task; every task gets an automated verify or a Wave 0 dependency.*

---

## Wave 0 Requirements

- [ ] `tests/fixtures/tripadvisor/attractions_oa30.html` — one real captured AttractionsFusion HTML page, **scrubbed of PII/cookies/keys**, so the embedded-JSON extractor is unit-testable offline (research Wave-0 blocker).
- [ ] `tests/unit/lanes/tripadvisor/` — stubs for the paginated fetch + HTML `sections[]` extraction + bulk ingest path + progress-state writes (TA-12).
- [ ] Dashboard: MSW handler for the new progress status endpoint + Vitest stub for the panel.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real slice (~5–10 pages) reaches Nascente against live TripAdvisor | TA-12 | Hits live TA + DataDome; cannot run in CI (RUN_REAL_EXTERNALS opt-in, needs operator session + BRAVE_DB_URL) | Inject session, run the bulk sweep with a small page-range cap, confirm Nascente rows > 0 and the dashboard panel shows live progress |
| DataDome endurance over sequential page requests | TA-12 | Anti-bot behavior only observable live | Watch the slice run for 403/429; confirm fail-fast records resume offset |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (HTML fixture is the critical one)
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
