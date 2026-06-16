---
status: partial
phase: 03-atrativos-lane-whatsapp-compliance
source: [03-01-SUMMARY.md, 03-02-SUMMARY.md, 03-03-SUMMARY.md, 03-04-SUMMARY.md, 03-05-SUMMARY.md]
started: 2026-06-16T16:52:04Z
updated: 2026-06-16T17:10:00Z
---

## Current Test

[testing complete — 4/4 runnable passed; 3 blocked on live infra]

## Tests

### 1. Cold Start Smoke Test
expected: Kill any running worker/API. docker compose up -d (Postgres+Redis), alembic upgrade head applies 0004_consent_log, FastAPI boots clean, GET /health returns 200. consent_log table exists in DB.
result: pass
note: Postgres+Redis healthy; alembic at head; consent_log table present (to_regclass confirms); FastAPI app boots; GET /api/v1/health → 200. (health route is /api/v1/health, not /health.)

### 2. Offline test suite green
expected: `BRAVE_DB_URL=... BRAVE_REDIS_URL=... uv run pytest` → all pass, no real external network.
result: pass
note: 304 passed, 1 warning, exit 0 (incl. 87 atrativos unit + 20 atrativos integration).

### 3. WhatsApp gate endpoint + steward auth
expected: mutating gate ops require X-Steward-Secret; read-only queue is open by design (D-06).
result: pass
note: PATCH /approve → 401, PATCH /reject → 401, POST quality-rating-webhook → 401, POST inbound → 401 (all no-auth, fail-closed). GET /api/v1/atrativos/gate → 200 (read-only by design; auth on mutating ops per D-06/WR-03). Original test expectation (GET requires auth) was incorrect.

### 4. Compliance gate blocks before send (offline)
expected: send_path_gate raises ComplianceError for opted-out/empty-phone/unapproved-template/ramp-exceeded/quality-RED/24h-window — proven by gate unit tests + e2e SC5/SC6. No message leaves without passing all conditions.
result: pass
note: tests/unit/compliance/test_gate.py + test_atrativos_lane_e2e.py → 24 passed.

### 5. [LIVE INFRA] Real Twilio BSP end-to-end send
expected: With live Twilio creds + approved utility template, TwilioWhatsAppClient.send_template dispatches a real WhatsApp message in the 24h window; LangGraph conversation persists across two turns via AsyncPostgresSaver (thread_id=atrativo:{rio_id}); owner-confirmed reply re-scores into Mar.
result: [pending]
blocked_by: third-party

### 6. [LIVE INFRA] CR-04 concurrent-webhook stress test
expected: Under real Celery multiprocessing, two concurrent inbound webhooks for the same rio_id do NOT double-send (SELECT FOR UPDATE row lock serializes them).
result: [pending]
blocked_by: server

### 7. [LIVE INFRA] WR-04 24h-window enforcement time-trace
expected: A real >24h-gap conversation trace confirms recv_reply persists window_open/last_inbound_at to rio.normalized so gate condition 5 enforces on follow-up sends (not default-True).
result: [pending]
blocked_by: third-party

## Summary

total: 7
passed: 4
issues: 0
pending: 0
skipped: 0
blocked: 3

## Gaps

[none — 4/4 runnable tests passed; 3 remaining blocked on live infra (Twilio BSP, real Celery multiprocessing), tracked in 03-HUMAN-UAT.md]
