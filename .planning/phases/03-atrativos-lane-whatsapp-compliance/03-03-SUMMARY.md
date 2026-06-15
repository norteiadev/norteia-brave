---
phase: 03-atrativos-lane-whatsapp-compliance
plan: "03"
subsystem: compliance-gate
tags:
  - compliance
  - whatsapp
  - fastapi
  - lgpd
  - bsp
  - redis
  - tdd

dependency_graph:
  requires:
    - "03-01"  # ConsentLog model, WhatsAppConfig, RampConfig, atrativos schemas
  provides:
    - "brave.compliance.gate.send_path_gate"
    - "brave.compliance.gate.ComplianceError"
    - "brave.compliance.gate.check_and_increment_ramp"
    - "brave.compliance.consent_log.is_opted_out"
    - "brave.compliance.consent_log.lookup_rio_id_by_phone"
    - "brave.compliance.quality_rating.is_quality_red"
    - "brave.compliance.quality_rating.set_quality_flag"
    - "GET /api/v1/atrativos/gate"
    - "PATCH /api/v1/atrativos/gate/{rio_id}/approve"
    - "PATCH /api/v1/atrativos/gate/{rio_id}/reject"
    - "POST /api/v1/atrativos/whatsapp/quality-rating-webhook"
    - "POST /api/v1/atrativos/whatsapp/inbound"
  affects:
    - "03-04"  # WhatsAppAgent calls send_path_gate before send_template

tech_stack:
  added: []
  patterns:
    - "D-11 compliance gate: single enforcing function, 8 conditions, raises ComplianceError"
    - "CR-04 atomic reserve-before-call: Redis INCR + DECR undo on cap breach + EXPIREAT crash-safe TTL"
    - "T-03-03-01 steward auth carried forward from Phase 2 DLQ pattern (require_steward with hmac.compare_digest)"
    - "TDD RED/GREEN cycle: failing tests committed before implementation"
    - "fakeredis for Redis-condition unit tests (100% offline)"

key_files:
  created:
    - brave/compliance/__init__.py
    - brave/compliance/gate.py
    - brave/compliance/consent_log.py
    - brave/compliance/quality_rating.py
    - brave/api/routers/atrativos_gate.py
    - tests/unit/compliance/__init__.py
    - tests/unit/compliance/test_gate.py
    - tests/integration/test_atrativos_gate.py
  modified:
    - brave/api/main.py
    - brave/tasks/pipeline.py

decisions:
  - "D-11 gate checks in order (1–8): cheapest checks (in-memory) first, Redis checks last — minimizes Redis I/O on early failures"
  - "Ramp counter uses global key (wa:ramp:{date}) not per-UF, matching post-Oct-2025 portfolio-wide WhatsApp limits (RESEARCH.md Pitfall 4)"
  - "Gate condition 5 (24h window) treats all templates as window-required for launch safety; future refinement: per-category allowlist"
  - "outreach_task and resume_conversation_task added as stubs in pipeline.py so router dispatch does not ImportError; full impl in 03-04"
  - "Mock session for unit tests uses side_effect=[consent_row, opted_out_row_or_None] to distinguish legal-basis vs is_opted_out calls"

metrics:
  duration: "11m"
  completed_date: "2026-06-15"
  tasks_completed: 2
  files_created: 8
  files_modified: 2
  tests_added: 23
  tests_passing: 23
---

# Phase 03 Plan 03: D-11 Compliance Gate + WhatsApp Human Gate FastAPI Router Summary

**One-liner:** LGPD+BSP send-path gate (8 conditions, 12 offline unit tests) + 5-endpoint WhatsApp gate FastAPI router with steward auth, Redis quality-rating flag, and consent-log-based inbound routing.

## What Was Built

### Task 1: brave/compliance/ package + 9 unit tests (TDD RED→GREEN)

**brave/compliance/gate.py**
- `ComplianceError(Exception)`: raised by any failed gate condition (always blocking)
- `check_and_increment_ramp(redis_client, cap, uf)`: CR-04 atomic INCR+DECR undo reserve, EXPIREAT crash-safe TTL to UTC midnight
- `send_path_gate(session, redis_client, rio, contact_phone, template_name, params, settings)`: 8 D-11 conditions in order:
  1. legal_basis_recorded — consent_log has a row for contact_phone
  2. norteia_identified — "Norteia" in params["body"]
  3. opt_out_honored — is_opted_out() returns False
  4. approved_template_used — template_name in settings.approved_templates
  5. 24h_window_respected — window_open=True in rio.normalized
  6. human_gate_approved — rio.sub_state == "whatsapp_in_progress"
  7. ramp_not_exceeded — check_and_increment_ramp (atomic, undo on breach)
  8. quality_not_red — is_quality_red() returns False

**brave/compliance/consent_log.py**
- `write_consent_record`: create ConsentLog row, PII guard (phone[:5] in logs only)
- `is_opted_out`: SELECT WHERE opted_out IS True
- `record_opt_out`: set opted_out=True + opted_out_at + keyword; write_audit
- `lookup_rio_id_by_phone`: return rio_id for inbound webhook routing

**brave/compliance/quality_rating.py**
- `QUALITY_RED_KEY = "wa:quality_red"`
- `is_quality_red`: Redis EXISTS check
- `set_quality_flag`: RED→set, GREEN/YELLOW→delete; no TTL (persists until GREEN/YELLOW clears)

**tests/unit/compliance/test_gate.py** (12 tests, all pass offline)
- 8 blocking tests: one per D-11 condition independently verified
- 1 happy-path test: all conditions pass, ramp counter == 1 after call
- 3 standalone tests: is_quality_red, set_quality_flag, check_and_increment_ramp DECR

### Task 2: FastAPI atrativos gate router + main.py (TDD RED→GREEN)

**brave/api/routers/atrativos_gate.py** (5 endpoints)
- `GET /api/v1/atrativos/gate`: list entity_type=attraction + sub_state=aguardando_consulta_whatsapp; optional uf/limit filters
- `PATCH /approve`: require_steward (hmac.compare_digest, T-03-03-01), 409 if already processed, flip sub_state→whatsapp_in_progress, dispatch outreach_task, write_audit
- `PATCH /reject`: require_steward, set routing=dlq + sub_state=None + dlq_reason=steward_rejected_gate, write_audit
- `POST /quality-rating-webhook`: set_quality_flag(get_redis(), rating), write_audit
- `POST /inbound`: lookup_rio_id_by_phone → dispatch resume_conversation_task; returns ignored if no active conversation

**brave/api/main.py**: registered `atrativos_gate_router` (Phase 3 addition)

**brave/tasks/pipeline.py**: added `outreach_task` + `resume_conversation_task` stubs (full impl in 03-04)

**tests/integration/test_atrativos_gate.py** (11 tests, all pass)
- 2 auth tests (no DB needed): 401 without X-Steward-Secret
- 9 DB-backed tests: list queue filtering, approve/reject state transitions, quality-rating webhook, inbound routing

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Mock session side_effect for two scalar() calls**
- **Found during:** Task 1 GREEN phase (test_gate_blocks_when_template_not_approved failed)
- **Issue:** `_make_session_with_consent(opted_out=False)` used `return_value` which returns the same row for both the legal-basis check AND the is_opted_out check, causing is_opted_out to return True even for non-opted-out contacts.
- **Fix:** Changed to `side_effect=[consent_row, opted_out_row_or_None]` so the two sequential scalar() calls return different values matching the two distinct queries.
- **Files modified:** tests/unit/compliance/test_gate.py
- **Commit:** c0f5915

**2. [Rule 2 - Missing Critical] Auth tests must not require DB fixture**
- **Found during:** Task 2 test design
- **Issue:** Auth tests that verify 401 were initially using `db_session` fixture, which skips if no BRAVE_DB_URL is set. Auth gates fire before DB work.
- **Fix:** Removed `db_session` parameter from `test_approve_gate_requires_steward_secret` and `test_reject_gate_requires_steward_secret`. Used random UUID in URL — 401 fires before any DB lookup.
- **Files modified:** tests/integration/test_atrativos_gate.py
- **Commit:** f51805e

**3. [Rule 2 - Missing Functionality] outreach_task and resume_conversation_task stubs**
- **Found during:** Task 2 implementation — router's approve and inbound endpoints import these tasks
- **Issue:** Router tries to import from brave.tasks.pipeline; tasks didn't exist → ImportError in tests
- **Fix:** Added stub tasks with proper @shared_task decorator, docstrings, and TODO markers for 03-04. No functional code — stubs only let the import succeed and the try/except dispatch pattern work.
- **Files modified:** brave/tasks/pipeline.py
- **Commit:** f51805e

**4. [Rule 3 - Blocking] Alembic migration needed before integration tests**
- **Found during:** Task 2 GREEN phase (test_inbound_reply tests got ProgrammingError: relation "consent_log" does not exist)
- **Issue:** Migration 0004_consent_log.py existed but hadn't been applied to the test DB
- **Fix:** Applied `alembic upgrade head` to bring test DB to current schema
- **Action:** Migration run (not a code change); no commit needed

## Security Verification (STRIDE Threat Register)

| Threat ID | Mitigation | Verified |
|-----------|------------|----------|
| T-03-03-01 | require_steward + hmac.compare_digest on /approve and /reject | test_approve/reject_requires_steward_secret passes (401) |
| T-03-03-02 | is_opted_out() at gate condition 3 | test_gate_blocks_when_opted_out passes |
| T-03-03-03 | template_name in approved_templates at gate condition 4 | test_gate_blocks_when_template_not_approved passes |
| T-03-03-04 | Atomic INCR + DECR undo reserve (CR-04) | test_gate_blocks_when_ramp_exceeded: counter == cap after breach |
| T-03-03-05 | Twilio signature TODO in docstring | documented |
| T-03-03-06 | phone_e164[:5] prefix only in logs | consent_log.py write_consent_record + record_opt_out |
| T-03-03-07 | No DELETE endpoint, opted_out never unset | no delete route exists; record_opt_out only sets to True |
| T-03-03-08 | Only (rio_id, message_text) forwarded, not phone | inbound endpoint: phone looked up, not passed to task |

## Known Stubs

| Stub | File | Reason |
|------|------|--------|
| `outreach_task` body: `pass` | brave/tasks/pipeline.py | Full LangGraph conversation implementation in 03-04 |
| `resume_conversation_task` body: `pass` | brave/tasks/pipeline.py | Full conversation resumption in 03-04 |
| Twilio signature validation on webhooks | brave/api/routers/atrativos_gate.py | Production hardening item (T-03-03-05); documented in docstrings |

These stubs do not prevent the plan's goal: the gate is complete and proven offline. The stubs are explicitly scoped to 03-04.

## Self-Check

### Created files exist
- /brave/compliance/__init__.py: FOUND
- /brave/compliance/gate.py: FOUND
- /brave/compliance/consent_log.py: FOUND
- /brave/compliance/quality_rating.py: FOUND
- /brave/api/routers/atrativos_gate.py: FOUND
- /tests/unit/compliance/test_gate.py: FOUND
- /tests/integration/test_atrativos_gate.py: FOUND

### Commits exist
- 1c78200: test(03-03): add failing tests for D-11 compliance gate (RED phase)
- c0f5915: feat(03-03): implement D-11 compliance gate package (GREEN phase)
- 8ef12e9: test(03-03): add failing integration tests for atrativos gate router (RED phase)
- f51805e: feat(03-03): implement atrativos gate FastAPI router + register in main.py (GREEN phase)

### Tests
- 12 unit tests passing (100% offline)
- 11 integration tests passing (9 DB-backed + 2 auth-only)
- Total: 23 tests added and passing

## Self-Check: PASSED
