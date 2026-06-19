---
phase: 08-ops-cms-destinos-atrativos-crud-process-observability-cores
plan: "01"
subsystem: collector-api
tags: [cms, crud, destinos, atrativos, fastapi, bearer-auth, pii-masking]
dependency_graph:
  requires:
    - brave/api/deps.py (require_bearer, require_steward_or_bearer, get_db)
    - brave/core/models.py (RioRecord, MarRecord, NascenteRecord, AuditLog, mask_phone)
    - brave/core/dlq/service.py (validate_and_promote_rio)
    - brave/lanes/atrativos/state_machine.py (advance_sub_state)
    - brave/observability/audit.py (write_audit)
    - brave/core/rio/routing.py (reprocess_record)
    - brave/tasks/pipeline.py (reprocess_record_task, push_destination_task)
  provides:
    - brave/api/routers/cms.py (CMS router — 6 destinos + 5 atrativos endpoints)
  affects:
    - plan 08-04 (registers cms.router in main.py — deferred, wave isolation)
tech_stack:
  added: []
  patterns:
    - FastAPI Bearer-guarded read endpoints (require_bearer)
    - FastAPI steward_or_bearer mutation endpoints (require_steward_or_bearer)
    - SQLAlchemy LEFT JOIN with outerjoin + paginated count subquery
    - JSON subscript as_string() filter for JSONB parent_mar_id
    - flag_modified on JSONB field edits (Pitfall 3)
    - _safe_normalized PII mask on all atrativo response paths
    - Lazy Celery task import with offline sync fallback
key_files:
  created:
    - brave/api/routers/cms.py
  modified: []
decisions:
  - "cms.py not registered in main.py — plan 08-04 handles registration (wave isolation)"
  - "advance_sub_state called with lock=True (SELECT FOR UPDATE) for concurrent safety (T-08-03)"
  - "phone_e164 excluded from /edit body merge — sanitized_fields filter (T-08-05)"
  - "_safe_normalized inlined (not imported from atrativos_gate.py) to keep module boundary clean"
  - "Lazy imports for validate_and_promote_rio and advance_sub_state inside handlers (avoids circular at module load)"
metrics:
  duration: "~10 min"
  completed: "2026-06-18"
  tasks: 2
  files: 1
requirements: [D-03, D-04]
---

# Phase 08 Plan 01: CMS CRUD Router — Destinos and Atrativos Summary

CMS CRUD router with 11 endpoints covering destinos (all routings) and atrativos (all FSM sub_states), wiring to existing pipeline building blocks with Bearer/steward auth and full PII masking.

## What Was Built

`brave/api/routers/cms.py` — a single FastAPI router (not yet registered in `main.py`; plan 08-04 handles that) providing 11 CMS endpoints:

**Destinos (6 endpoints — D-03):**
- `GET /api/v1/destinos` — paginated list with LEFT JOIN MarRecord; filters: uf/routing/score_band/q; Bearer-guarded
- `GET /api/v1/destinos/{rio_id}` — full detail: score_breakdown, normalized, AuditLog journey, child_atrativos count by sub_state; Bearer-guarded
- `PATCH /api/v1/destinos/{rio_id}/promote` — validates+promotes via `validate_and_promote_rio`, dispatches `push_destination_task`; returns 202; steward_or_bearer
- `PATCH /api/v1/destinos/{rio_id}/descarte` — sets routing=descarte, dlq_reason=steward_rejected; steward_or_bearer
- `PATCH /api/v1/destinos/{rio_id}/reprocess` — dispatches `reprocess_record_task` (Celery fallback to sync); returns 202; steward_or_bearer
- `PATCH /api/v1/destinos/{rio_id}/edit` — merges body.fields into normalized with `flag_modified`; steward_or_bearer

**Atrativos (5 endpoints — D-04):**
- `GET /api/v1/atrativos` — paginated list with JSON subscript `as_string()` for `parent_mar_id` filter; `_safe_normalized` on every row; Bearer-guarded
- `GET /api/v1/atrativos/{rio_id}` — full detail: FSM audit trail, score_breakdown, `_safe_normalized`, parent destino link; Bearer-guarded
- `PATCH /api/v1/atrativos/{rio_id}/advance` — calls `advance_sub_state(lock=True)`; returns 409 on expected_state mismatch (T-08-03); steward_or_bearer
- `PATCH /api/v1/atrativos/{rio_id}/descarte` — sets routing=dlq, dlq_reason=steward_rejected_gate, sub_state=None; steward_or_bearer
- `PATCH /api/v1/atrativos/{rio_id}/edit` — merges body.fields excluding phone_e164 (T-08-05) with `flag_modified`; steward_or_bearer

## Threat Model Mitigations Applied

| Threat ID | Status |
|-----------|--------|
| T-08-01: Spoofing reads | Mitigated — `require_bearer` on all 4 GET endpoints |
| T-08-02: Tampering via PATCH | Mitigated — `require_steward_or_bearer` on all 7 PATCH endpoints |
| T-08-03: advance expected_state bypass | Mitigated — `advance_sub_state` idempotency guard + 409 on mismatch |
| T-08-04: phone_e164 disclosure | Mitigated — `_safe_normalized` on every atrativo response path |
| T-08-05: /edit re-injects phone_e164 | Mitigated — `sanitized_fields` filter excludes phone_e164 before merge |

## Deviations from Plan

None — plan executed exactly as written.

All pitfalls documented in 08-PATTERNS.md were applied:
- Pitfall 2: JSON subscript `as_string()` not JSONB `@>` operator for parent_mar_id filter
- Pitfall 3: `flag_modified` after normalized dict reassignment in both /edit handlers
- Pitfall 4: `lock=True` in advance_sub_state (offline tests use `lock=False`)

## Known Stubs

None — all endpoints wire to real building blocks. No placeholder data.

## Threat Flags

None — no new network endpoints or trust boundaries introduced beyond what the plan's threat model covers.

## Self-Check: PASSED

- `brave/api/routers/cms.py` exists: YES (635 lines)
- Commit `1a1fbfe` exists: YES
- 11 routes present: YES (`/api/v1/destinos`, `/api/v1/destinos/{rio_id}`, `/api/v1/destinos/{rio_id}/promote`, `/api/v1/destinos/{rio_id}/descarte`, `/api/v1/destinos/{rio_id}/reprocess`, `/api/v1/destinos/{rio_id}/edit`, `/api/v1/atrativos`, `/api/v1/atrativos/{rio_id}`, `/api/v1/atrativos/{rio_id}/advance`, `/api/v1/atrativos/{rio_id}/descarte`, `/api/v1/atrativos/{rio_id}/edit`)
- `phone_e164` in response paths: NO (only in masking guard + comment + exclusion filter)
- `_safe_normalized` in atrativo serialization: YES (list contacts_summary + detail normalized)
- `require_bearer` on reads: YES (4 GET endpoints)
- `require_steward_or_bearer` on mutations: YES (7 PATCH endpoints)
- `flag_modified` in both /edit handlers: YES (lines ~370, ~623)
