---
phase: 04-dashboard-territorial-cms
plan: 08
subsystem: dashboard-funnels-conversations
tags: [dash-05, conversation-message, funnels, transcript, lgpd, append-only, r2-option-b, alembic-0005, bearer, tdd, d-01]

# Dependency graph
requires:
  - phase: 04-dashboard-territorial-cms
    plan: 02
    provides: "read-only Bearer-guarded dashboard.py router (require_bearer); DI shapes"
  - phase: 04-dashboard-territorial-cms
    plan: 07
    provides: "dashboard.py group_by aggregation idiom (cost) mirrored by funnels"
  - phase: 03-atrativos-lane-whatsapp-compliance
    plan: "*"
    provides: "outreach_task + resume_conversation_task LangGraph WhatsApp flow (the two pinned write-points); ConsentLog posture analog; _extract_contact_phone (CR-03)"
  - phase: 01-brave-core
    plan: "*"
    provides: "NascenteRecord/RioRecord/MarRecord medallion models (source/uf/entity_type/routing) for funnel stage counts; Alembic chain (head 0004)"
provides:
  - "ConversationMessage append-only model (rio_id FK, masked phone, direction, role, content, extracted JSON, created_at) + mask_phone helper"
  - "Alembic migration 0005 (chained to 0004, no CONCURRENTLY, rio_id index)"
  - "ConversationMessage appends at BOTH pipeline write-points: outreach (outbound ask) + resume (inbound reply + follow-up/extraction), on the task's own committed session, alongside AsyncPostgresSaver"
  - "GET /api/v1/funnels — ingested(by source/uf/entity_type) -> routing(by routing/uf) -> published counts, optional entity_type/uf/source filters, Bearer-guarded"
  - "GET /api/v1/conversations list + GET /api/v1/conversations/{rio_id} transcript — trivial SELECT over conversation_message, masked phone, 404 on unknown rio_id"
affects:
  - "04-09 frontend (funnels + conversations views) consumes /api/v1/funnels and /api/v1/conversations[/{rio_id}]"
  - "the WhatsApp conversation history is now readable outside LangGraph checkpoints (R2 Option B)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Append-only conversation log written by the existing Celery tasks at every message boundary (R2 Option B) — read endpoint is a trivial SELECT decoupled from LangGraph serialization"
    - "_log_conversation_messages syncs the graph's FINAL-state messages[] tail into conversation_message idempotently (count-based prefix skip) on the task's OWN session before its single commit — never a separate/uncommitted session, alongside (not replacing) AsyncPostgresSaver"
    - "LGPD phone minimization at write time AND read time: mask_phone(prefix*****suffix); the raw E.164 is never persisted or emitted; grep gate asserts no phone_e164 token in dashboard.py"
    - "Funnels = pure GROUP BY over the three medallion layers, honoring the dlq.py optional-filter idiom (if uf: query = query.where(...))"
    - "Migration roundtrip tested offline via Operations.context(MigrationContext.configure(conn)) binding alembic.op to a live connection; loaded by file path (alembic/versions is not a package)"

key-files:
  created:
    - alembic/versions/0005_conversation_message.py
  modified:
    - brave/core/models.py
    - brave/tasks/pipeline.py
    - brave/api/routers/dashboard.py
    - tests/integration/test_dashboard_endpoints.py

decisions:
  - "R2 resolved as Option B: append-only conversation_message log (not coupling the read endpoint to LangGraph checkpoint blob serialization) — offline-testable, LGPD-minimizable, mirrors the ConsentLog our-own-table posture"
  - "Append is idempotent on retry: count rows already logged for the rio_id, append only the new tail of the final-state messages — a retried task does not duplicate prior turns"
  - "The extraction snapshot is attached to the final outbound (follow-up) row in a resume so the structured result rides alongside its message boundary"

metrics:
  duration: ~45m
  completed: 2026-06-16
  tasks: 2
  files_changed: 5
  commits: 2
---

# Phase 4 Plan 08: Funnels + Conversations Backend (DASH-05) Summary

Append-only `conversation_message` transcript log (R2 Option B) written at both WhatsApp pipeline write-points, plus Bearer-guarded `GET /api/v1/funnels` and `GET /api/v1/conversations[/{rio_id}]` read endpoints with LGPD-masked PII.

## What Was Built

**Task 1 — ConversationMessage log + appends at both write-points (commit `018b0a3`)**
- `ConversationMessage(Base)` in `brave/core/models.py`: append-only (`id`, `rio_id` FK→`rio_records` indexed, `phone_masked`, `direction`, `role`, `content`, `extracted` JSON, `created_at`), mirroring the `ConsentLog` posture but storing ONLY a masked phone. Added a `mask_phone()` helper (`prefix*****suffix`, `***` sentinel for empty).
- `alembic/versions/0005_conversation_message.py`: `create_table` + `ix_conversation_message_rio_id`, chained to `0004`, no CONCURRENTLY, matching `downgrade`. Applies cleanly (`alembic upgrade head`).
- `brave/tasks/pipeline.py`: `_log_conversation_messages()` helper appends rows from the graph's FINAL state on the task's own committed session, alongside the `AsyncPostgresSaver` (not replacing it). Wired into BOTH tasks:
  - `outreach_task`: `_run()` now returns `(final_state, contact_phone)`; the produced OUTBOUND ask message(s) (read from the final state, NOT the empty `message_text=""` literal) are appended before the existing `session.commit()`. Tolerant of the no-contact-phone early return.
  - `resume_conversation_task`: appends the INBOUND `reply_text` AND any follow-up OUTBOUND message + `extraction` snapshot read from the final state, before the existing `session.commit()`.

**Task 2 — funnels + conversations endpoints (commit `835e48f`)**
- `GET /api/v1/funnels`: `NascenteRecord` ingested (group by source/uf/entity_type) → `RioRecord` routing (group by routing/uf) → `MarRecord` published count; optional `entity_type`/`uf`/`source` filters via the dlq.py `if uf: query = query.where(...)` idiom; `Depends(require_bearer)`.
- `GET /api/v1/conversations`: per-`rio_id` list (masked phone, message count, last message).
- `GET /api/v1/conversations/{rio_id}`: trivial `SELECT ... ORDER BY created_at` transcript, masked phone, 404 on unknown rio_id.
- All routes Bearer-guarded; `phone_e164` token absent from `dashboard.py` (grep gate).

## Verification

- `pytest tests/integration/test_dashboard_endpoints.py -k "funnels or conversations or conversation_message"` → 8 passed (and the full file: 50 passed).
- Two-write-point acceptance: `test_outreach_task_appends_outbound_message` (outbound row, content from final state, not `""`) + `test_resume_task_appends_inbound_and_followup` (inbound reply + follow-up outbound + extraction).
- Masked-phone: `test_conversation_message_no_raw_phone_persisted`, `test_mask_phone_minimizes_pii`, and endpoint payload scans assert the raw E.164 never appears.
- Own-session commit: `test_outreach_append_committed_on_task_own_session` (visible from a fresh session).
- Migration: `test_migration_0005_chains_to_0004` + `test_migration_0005_upgrade_downgrade_roundtrip`; `alembic upgrade head` applies cleanly.
- `grep phone_e164 dashboard.py` → 0 matches. `grep ConversationMessage pipeline.py` → matches in both tasks. No regressions in `test_whatsapp_agent.py` / `test_atrativos_gate.py` (35 passed).

## Deviations from Plan

None — plan executed as written. (The plan's two `tdd="true"` tasks were executed RED-then-GREEN within each task commit; the offline tests for the append write-points drive the real Celery task functions with the LangGraph build_graph + AsyncPostgresSaver patched to deterministic offline stand-ins, since the saver otherwise needs live checkpoint tables.)

## Threat Surface

Honored the plan's `<threat_model>`: T-04-24 (masked phone only, no `phone_e164` token in dashboard.py), T-04-25 (Bearer on every route, 401 before DB), T-04-26 (append-only, FK to rio_records, own committed session), T-04-27/T-04-SC accepted (aggregate-only funnels; no new deps). The pipeline change is additive — scoring/routing/Mar/Pact contract untouched. No new threat surface introduced beyond the documented register.

## Self-Check: PASSED

All created/modified files exist on disk; both task commits (018b0a3, 835e48f) are in git history.
