---
phase: 03
slug: atrativos-lane-whatsapp-compliance
status: verified
threats_open: 0
asvs_level: 1
created: 2026-06-16
---

# Phase 03 — Atrativos Lane (WhatsApp + LGPD/BSP Compliance) — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
> Audited against the 5 plan-time `<threat_model>` blocks (22 STRIDE rows, register
> authored at plan time) plus the 13 code-review findings in `03-REVIEW.md`
> (4 BLOCKER + 9 WARNING). ASVS L1, block_on=high. Every declared mitigation was
> verified by reading the implemented code — documentation/intent was not accepted
> as evidence.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Steward → WhatsApp gate mutating endpoints | `PATCH /api/v1/atrativos/gate/{id}/approve\|reject` advance `sub_state`, dispatch outreach, route to DLQ | Steward intent; authorizes outreach to a real owner phone |
| Twilio/n8n → webhook endpoints | `POST /quality-rating-webhook` (sets/clears global RED send-pause flag), `POST /inbound` (relays owner reply into the conversation/promotion path) | Quality rating + inbound message body; both drive the compliance gate and Mar promotion |
| `send_path_gate` → WhatsAppClientProtocol | The single allowed `send_template` call site; the 8-condition D-11 gate runs first | E.164 phone + template params (must contain "Norteia") |
| LangGraph nodes → DeepSeek (OpenRouter) | Structured extraction from conversation text | Message body only — phone/PII excluded; `provider_data_collection="deny"` |
| Lane → ConsentLog (LGPD) | `phone_e164` persisted for suppression lookup; opt-out is append-only/permanent | PII (E.164); logs emit phone[:5] prefix only |
| AsyncPostgresSaver checkpoint | Conversation state persisted across days; keyed `atrativo:{rio_id}` (UUID, never phone) | Conversation turns; thread_id is a UUID, not PII |
| Config → env | `BRAVE_WA_*` / `BRAVE_LLM_*` secrets; no `Field(alias=...)` (no env shadowing) | Twilio/Anthropic/OpenRouter secrets; `Field(default="")`, never logged |

---

## Threat Register

22 plan-time STRIDE threats verified against the implementation; 13 code-review
findings adjudicated. All mitigations were grep- and read-confirmed in the cited
files (line evidence below). All `*-SC` supply-chain rows are CLOSED (the one new
package, `langgraph-checkpoint-postgres`, passed the blocking human verification
checkpoint — publisher `langchain-ai`, v3.1.0, confirmed in `03-01-SUMMARY.md`).

| Threat ID | Category | Component | Disposition | Mitigation (evidence) | Status |
|-----------|----------|-----------|-------------|------------------------|--------|
| T-03-01-01 | Tampering | ConsentLog FK rio_id | mitigate | `ForeignKey("rio_records.id")` in `brave/core/models.py:368` + `ForeignKeyConstraint(["rio_id"],["rio_records.id"])` in `alembic/versions/0004_consent_log.py:65` | closed |
| T-03-01-02 | Info Disclosure | WhatsApp secrets in env | mitigate | No `Field(alias=...)` anywhere in `brave/config/settings.py`; `WhatsAppConfig` secrets `Field(default="")`, never logged (CR-02 carried fwd) | closed |
| T-03-01-03 | Tampering | Supply chain (langgraph-checkpoint-postgres) | mitigate | Blocking human checkpoint verified publisher=langchain-ai, v3.1.0 (`03-01-SUMMARY.md` Task 1) | closed |
| T-03-01-SC | Tampering | pip install gate | mitigate | Only one new pkg; verified via blocking checkpoint before any import | closed |
| T-03-02-01 | Tampering | sub_state FSM race | mitigate | `advance_sub_state` re-fetches `session.get(RioRecord, rio.id, with_for_update=True)` BEFORE the guard (`state_machine.py:71`); tasks lock row too (CR-04) | closed |
| T-03-02-02 | Info Disclosure | Places content beyond place_id | mitigate | DiscoveryAgent canonical = AtrativoResult (first-party); only `place_id` persisted as cache (`discovery_agent.py:294-298`, COMP-03/D-04) | closed |
| T-03-02-03 | Tampering | Celery replay stale sub_state | mitigate | `acks_late=True` + `reject_on_worker_lost=True` + early-return sub_state guard in each task | closed |
| T-03-02-04 | Spoofing | Apify Meta-ToS | accept | Read-only `scrape_ig`, best-effort try/except → `{}` (`signal_agent.py:226-236`), no automated DM; CLAUDE.md constraint. See AR-03-04 | closed |
| T-03-02-05 | Info Disclosure | PII in LLM prompt | mitigate | DiscoveryAgent sends structured business data; `provider_data_collection="deny"` (`settings.py:75`) | closed |
| T-03-03-01 | EoP | /approve /reject auth | mitigate | `require_steward` (X-Steward-Secret, `hmac.compare_digest`, fail-closed) on both routes (`atrativos_gate.py:51-74, 174, 271`) | closed |
| T-03-03-02 | Tampering | Opt-out bypass | mitigate | `is_opted_out` at gate condition 3 (`gate.py:209`); CR-01 whole-token opt-out detection (`whatsapp_agent.py:114-134`) avoids both false-neg bypass and false-pos suppression | closed |
| T-03-03-03 | Tampering | Unregistered template | mitigate | Gate condition 4: `template_name in approved_templates` (`gate.py:219-225`) | closed |
| T-03-03-04 | Tampering | Ramp counter race | mitigate | Atomic INCR + DECR-on-breach reserve-before-call (`gate.py:107-119`, CR-04) | closed |
| T-03-03-05 | Spoofing | Quality-rating webhook spoof | mitigate | **WR-03 fix verified** — `require_webhook` (X-Webhook-Secret, `hmac.compare_digest`, fail-closed) enforced via `dependencies=[Depends(require_webhook)]` on quality-rating-webhook (`atrativos_gate.py:322-325, 77-105`). No longer a deferred TODO | closed |
| T-03-03-06 | Info Disclosure | Phone PII in consent_log | mitigate | Logs emit `phone_e164[:5]` only (`consent_log.py:91,137,225`) | closed |
| T-03-03-07 | Tampering | consent_log DELETE / un-opt-out | mitigate | No DELETE route; `record_opt_out` only sets True; WR-09 `write_consent_record` refuses to resurrect an opted-out phone (`consent_log.py:90-99`) | closed |
| T-03-03-08 | Info Disclosure | PII in inbound webhook payload | mitigate | Inbound forwards only `(rio_id, message_text)`; phone looked up locally (`atrativos_gate.py:402-408`) | closed |
| T-03-04-01 | Tampering | Gate bypass via direct send_template | mitigate | `_compliant_send` is the sole `send_template` call site; `send_path_gate` invoked first (`whatsapp_agent.py:204-219`) | closed |
| T-03-04-02 | Tampering | thread_id collision | mitigate | `thread_id = f"atrativo:{rio_id}"` (UUID) in both tasks (`pipeline.py:987,1164`) | closed |
| T-03-04-03 | Info Disclosure | PII to DeepSeek | mitigate | Extraction prompt built from message content only; phone never included (`whatsapp_agent.py:434-444`); data_collection=deny | closed |
| T-03-04-04 | DoS | Runaway conversation | mitigate | `max_turns` guard in `_after_extract_answers` (`whatsapp_agent.py:710-725`), default 3 | closed |
| T-03-04-05 | Info Disclosure | Checkpoint rows PII | mitigate | Checkpoints keyed by `atrativo:{rio_id}` (UUID, not phone); retention as documented v2 debt | closed |
| T-03-04-06 | Tampering | push_attraction non-canonical | mitigate | `push_attraction_task` returns early unless `routing=="mar"` (`pipeline.py:827`); idempotent by source_ref via `promote_to_mar` | closed |
| T-03-04-07 | Spoofing | FakeWhatsAppClient in prod | mitigate | Tasks select `TwilioWhatsAppClient`/`NullWhatsAppClient`; `grep FakeWhatsAppClient pipeline.py` == 0 (`pipeline.py:918,949,1128`) | closed |
| T-03-05-01 | Info Disclosure | Test fixtures real PII | mitigate | All fixture phones synthetic (`+5573999990001`, `+5511999990001`) — `test_atrativos_lane_e2e.py:194,716,796` | closed |
| T-03-05-02 | Tampering | Integration test missing gate-bypass coverage | mitigate | SC6 invokes `send_path_gate` opted-out path → ComplianceError; gate proven non-bypassable in e2e | closed |

### Code-review findings (03-REVIEW.md) — fix verification

The REVIEW reported 4 BLOCKER + 9 WARNING. Each fix was verified present in code
(dedicated `fix(03): ...` commits `547c098`..`2c40db5`); none were trusted on the
commit message alone.

| Finding | Maps to | Fix verified in code | Status |
|---------|---------|----------------------|--------|
| CR-01 substring opt-out → false positives | T-03-03-02 | `_detect_opt_out_keyword` whole-token/anchored match (`whatsapp_agent.py:114-134`) | closed |
| CR-02 fakeredis fallback drops RED pause | T-03-03-05 | `get_redis` never silent-falls-back (`deps.py:95-122`); `is_quality_red` fail-closed on RedisError (`quality_rating.py:50-55`) | closed |
| CR-03 contact_phone from wrong key → empty send | T-03-04-01/03-03-02 | `_extract_contact_phone` reads `normalized["contacts"]["phone_e164"]`; empty → DLQ (`pipeline.py:47-57,990-1001`); gate condition 0 rejects empty phone (`gate.py:170-175`) | closed |
| CR-04 concurrent inbound double-send | T-03-02-01 | `with_for_update=True` before guard in approve/outreach/resume + `advance_sub_state` (`atrativos_gate.py:208`, `pipeline.py:927,1106`, `state_machine.py:71`) | closed |
| WR-01 retry-everything predicate | (hardening) | narrowed retry predicates (commit `c3f609b`) | closed |
| WR-02 silent dispatch/push swallow | (hardening) | prod path logs error + raises 503 (`atrativos_gate.py:229-249,415-431`; `pipeline.py:861-864`) | closed |
| WR-03 unauthenticated webhooks | T-03-03-05 | `require_webhook` X-Webhook-Secret on both POST webhooks (`atrativos_gate.py:77-105,324,373`) | closed |
| WR-04 24h-window dead-code | (hardening) | `_persist_window_state` writes the key the gate reads (`whatsapp_agent.py:317-337`) | closed |
| WR-05 month-end ValueError | T-03-03-04 | `_next_utc_midnight` uses `timedelta` (`gate.py:56-69`) | closed |
| WR-06 sync session across async | (hardening, deferred) | documented limitation + CR-04 row-lock + WR-09 mitigations (`whatsapp_agent.py:38-54`). See AR-03-06 | open (non-blocking) |
| WR-07 stale captured rio in finalize | (hardening) | `_finalize_node` re-fetches record (`whatsapp_agent.py:623-668`) | closed |
| WR-08 corroboração dead conditional | (hardening) | catch-all removed; `posts_count` key fixed (`signal_agent.py:327-337`) | closed |
| WR-09 duplicate/contradictory consent rows | T-03-03-07 | `write_consent_record` is opt-out-safe upsert (raises `OptedOutError`) (`consent_log.py:47-145`) | closed |

*Status: open · closed* · *Disposition: mitigate · accept · transfer*

---

## Unregistered Flags

None. The `## Threat Flags` section of all five SUMMARYs (03-01..03-05) reports
"None" — no new attack surface appeared during implementation beyond the plan-time
register. The two new POST webhook endpoints map to existing threat T-03-03-05 (and
were hardened by WR-03), so they are not unregistered surface.

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-03-04 | T-03-02-04 | Apify IG/X scraping is read-only best-effort signal, never an automated DM. Meta-ToS exposure is documented per CLAUDE.md; degrades to `{}` on any error and never blocks a record. | Leandro Freire | 2026-06-16 |
| AR-03-06 | WR-06 | Sync SQLAlchemy session mutated across the `asyncio.run` boundary while AsyncPostgresSaver commits checkpoints on a separate connection. Not transactionally coupled. Bounded by the CR-04 per-row lock (serializes resumes), WR-09 consent upsert, and CR-04 ramp reserve-before-call. Below block_on=high; full fix (async session) tracked as follow-up. | Leandro Freire | 2026-06-16 |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-06-16 | 22 (+13 review findings) | 22 / 12 of 13 | 1 (WR-06, non-blocking) | gsd-security-auditor |

All 22 plan-time threats verified CLOSED in implemented code. All 4 code-review
BLOCKERs (CR-01..CR-04) verified fixed. 8 of 9 WARNINGs verified fixed; WR-06
remains an accepted non-blocking risk (below block_on=high). The brief-flagged
T-03-03-05 quality-rating webhook spoof is CLOSED: WR-03 added X-Webhook-Secret
constant-time auth, confirmed active via the FastAPI dependency. `threats_open: 0`
at block_on=high.

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Each declared mitigation verified present in implemented code (not docs/intent)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed (no open threat at or above block_on=high)
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-06-16
