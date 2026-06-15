---
status: partial
phase: 03-atrativos-lane-whatsapp-compliance
source: [03-VERIFICATION.md]
started: 2026-06-15T00:00:00Z
updated: 2026-06-15T00:00:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Real Twilio BSP end-to-end send
expected: With live Twilio credentials + an approved utility template, TwilioWhatsAppClient.send_template dispatches a real WhatsApp BSP message inside the 24h window, the LangGraph conversation persists across two turns via AsyncPostgresSaver (thread_id=atrativo:{rio_id}), and an owner-confirmed reply re-scores the atrativo into Mar. (Cannot be automated — needs live BSP infra; default suite is 100% offline.)
result: [pending]

### 2. CR-04 concurrent-webhook stress test
expected: Under real Celery multiprocessing, two concurrent inbound webhooks for the same rio_id do NOT double-send. The SELECT ... FOR UPDATE row lock in advance_sub_state / outreach_task / resume_conversation_task serializes them so only one send occurs. (Unit tests cover the guard logically; real-concurrency confirmation is human.)
result: [pending]

### 3. WR-04 24h-window enforcement for follow-up sends
expected: A time-based trace through the full conversation path confirms recv_reply persists window_open / last_inbound_at into rio.normalized so gate condition 5 (24h window) actually enforces on follow-up sends (not default-True). Fix 74a4dee added the persistence; confirm it holds end-to-end across a real >24h gap.
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
