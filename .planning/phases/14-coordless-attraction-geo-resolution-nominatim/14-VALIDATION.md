---
phase: 14
slug: coordless-attraction-geo-resolution-nominatim
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-25
---

# Phase 14 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from 14-RESEARCH.md "## Validation Architecture".

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.x (`asyncio_mode = "auto"`, `respx`, `fakeredis`) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]`; shared fixtures `tests/conftest.py` |
| **Quick run command** | `env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest tests/unit/lanes/tripadvisor/test_atrativos.py tests/unit/clients/test_nominatim.py -x -p no:warnings` |
| **Full suite command** | `env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest -p no:warnings` |
| **Estimated runtime** | ~8 seconds (full offline suite) |

> MEMORY: unset `RUN_REAL_EXTERNALS` (`env -u`) — sourcing `.env` can flip it on and make offline tests hit real APIs.

---

## Sampling Rate

- **After every task commit:** quick run command (atrativos + nominatim client tests)
- **After every plan wave:** full suite (`RUN_REAL_EXTERNALS` unset) — must be green, zero real network (respx enforces)
- **Before `/gsd:verify-work`:** full suite green
- **Max feedback latency:** ~8 seconds
- **Level-3 (operator, real, gated):** real MG sweep (`RUN_REAL_EXTERNALS=1`, session injected, mirrors Phase-13 runbook) → Nascente `entity_type='attraction'` count > 0 with municípios resolved (not mass-quarantined)

---

## Per-Requirement Verification Map

> Task IDs assigned by the planner; rows are requirement-level until plans exist. All ❌ W0 files are created in Wave 0.

| Req | Behavior | Test Type | Automated Command | File |
|-----|----------|-----------|-------------------|------|
| TA-14 | Real client raises `RuntimeError` when `run_real_externals=False` | unit | `pytest tests/unit/clients/test_nominatim.py::test_guard_raises -x` | ❌ W0 |
| TA-14 | `geocode` sends UA + `addressdetails=1` + `countrycodes=br` (respx captures) | unit | `pytest tests/unit/clients/test_nominatim.py::test_request_params -x` | ❌ W0 |
| TA-14 | Address precedence municipality→city→town→village→county parsed | unit | `pytest tests/unit/clients/test_nominatim.py::test_address_precedence -x` | ❌ W0 |
| TA-14 | Redis cache hit on 2nd call → no 2nd httpx request (respx count==1) | unit | `pytest tests/unit/clients/test_nominatim.py::test_cache_by_location_id -x` | ❌ W0 |
| TA-14 | Rate limit ≥1 req/s enforced (mock clock / sleep asserted) | unit | `pytest tests/unit/clients/test_nominatim.py::test_rate_limit -x` | ❌ W0 |
| TA-14 | `NullGeocoderClient.geocode` returns None, no network | unit | `pytest tests/unit/clients/test_nominatim.py::test_null_returns_none -x` | ❌ W0 |
| TA-14 | LGPD: result has only lat/lon/osm_id/municipio_name (no street/PII) | unit | `pytest tests/unit/clients/test_nominatim.py::test_lgpd_no_pii -x` | ❌ W0 |
| TA-15 | **Regression:** coordless card that previously quarantined now resolves | unit | `pytest tests/unit/lanes/tripadvisor/test_atrativos.py::test_coordless_resolves_via_geo -x` | ❌ W0 |
| TA-15 | `ibge_unmatched` fires only after BOTH name-match AND geo-enrichment fail | unit | `pytest tests/unit/lanes/tripadvisor/test_atrativos.py::test_quarantine_after_both_fail -x` | ❌ W0 |
| TA-15 | `geocoder=None` → existing behavior unchanged (no regression) | unit | `pytest tests/unit/lanes/tripadvisor/test_atrativos.py::test_no_geocoder_unchanged -x` | ⚠️ extend |
| TA-15 | Relaxed 50 km radius at call site; default 15 km unchanged for destinos | unit | `pytest tests/unit/lanes/tripadvisor/test_ibge.py -x` | ⚠️ extend |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/clients/test_nominatim.py` — TA-14 (guard, params, precedence, cache, rate-limit, null, LGPD)
- [ ] `tests/fakes/fake_nominatim.py` — `FakeGeocoderClient` fixture + call recording (needed by TA-15 regression)
- [ ] Extend `tests/unit/lanes/tripadvisor/test_atrativos.py` — regression + both-fail + no-geocoder-unchanged
- [ ] (Optional) `real_nominatim` marker in `conftest.py` gated by `RUN_REAL_EXTERNALS` for the opt-in live geocode test
- [ ] No framework install needed — pytest/respx/fakeredis already present

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real MG sweep yields Nascente attraction count > 0 with municípios resolved | TA-15 | Needs a live operator-captured TripAdvisor session + real Nominatim calls (DataDome-walled, rate-limited) | Inject session (Phase-13 RUNBOOK), `RUN_REAL_EXTERNALS=1` sweep MG/atrativos, then `select entity_type, count(*) from nascente where source='tripadvisor' group by entity_type;` → attraction > 0; spot-check quarantine table is NOT dominated by `ibge_unmatched` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
