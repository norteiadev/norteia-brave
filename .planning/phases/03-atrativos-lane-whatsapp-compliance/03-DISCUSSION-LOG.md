# Phase 3: Atrativos Lane (WhatsApp + Compliance) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-12
**Phase:** 3-Atrativos Lane (WhatsApp + Compliance)
**Areas discussed:** FSM orchestration & durability, WhatsApp BSP provider, Compliance gate enforcement, Human gate + volume ramp, Conversation logic split, Discovery parent-resolution & place_id, SignalAgent score mapping, Owner-validation re-score
**Mode:** `--auto` — all gray areas auto-selected; each question resolved with the recommended (research-backed) option.

---

## FSM orchestration & durability

| Option | Description | Selected |
|--------|-------------|----------|
| Celery + Redis durable FSM on `sub_state` | Idempotent tasks keyed off the existing `RioRecord.sub_state` column; human wait held as queue state; defer Temporal | ✓ |
| Adopt Temporal now | Durable timers + human-in-loop signals + replay UI, but adds a server/cluster + rewrite | |

**User's choice:** Celery + Redis durable FSM (D-01/D-02). Phase 3 was the agreed Temporal re-evaluation point; outcome = defer.
**Notes:** Re-open Temporal only on proven restart/timer pain. Orchestration stays behind the existing interface so a future swap is contained (Phase 1 D-06).

---

## WhatsApp BSP provider

| Option | Description | Selected |
|--------|-------------|----------|
| Twilio at launch | Ships faster; behind `WhatsAppClientProtocol.send_template`; faked in suite | ✓ |
| Meta Cloud API direct | Cheaper at scale but own webhook infra + rate-tier management | |

**User's choice:** Twilio launch path (D-09), behind the existing protocol.
**Notes:** Meta Cloud is the cost-optimized end-state, migrated behind the same interface later (deferred). Re-verify Twilio-vs-Meta pricing/policy at build time per the Phase 3 research flag.

---

## Compliance gate enforcement (LGPD + BSP)

| Option | Description | Selected |
|--------|-------------|----------|
| Single hard send-path gate before every send | Code-enforced + offline-tested; asserts legal basis/identification/opt-out/template/24h window/gate+ramp/minimization; blocks on failure | ✓ |
| Documentation + manual checklist | Compliance as process, not code | |

**User's choice:** Hard send-path gate function (D-11) — blocks the first real send if any condition fails.
**Notes:** PROJECT.md Key Decision (compliance as hard send-path gates, not a late checkbox). Auto-pause on degraded WhatsApp quality rating. Consent/opt-out log records legal basis per contact.

---

## Human WhatsApp gate + volume ramp

| Option | Description | Selected |
|--------|-------------|----------|
| FastAPI endpoint mirroring DLQ steward + Redis ramp counter | Queue list + approve/reject over `aguardando_consulta_whatsapp`; ramp = atomic Redis counter reusing cost-guard pattern | ✓ |
| Bespoke gate service | New abstraction outside the proven steward pattern | |

**User's choice:** FastAPI gate mirroring Phase 2 DLQ steward (D-06); Redis ramp counter (D-07).
**Notes:** No outreach dispatched without a human approve. Apply the CR-04 reserve-before-call atomicity lesson to the ramp counter. Ramp caps in config, not code.

---

## Conversation logic split & transport

| Option | Description | Selected |
|--------|-------------|----------|
| All logic in LangGraph; n8n thin transport | Sonnet (native SDK) PT-BR ask + DeepSeek (instructor) extract; state persisted; 100% offline-testable | ✓ |
| n8n holds conversation logic | Ready WhatsApp nodes but un-unit-testable | |

**User's choice:** All logic in LangGraph code (D-08); n8n transport only.
**Notes:** CLAUDE.md hard constraint — n8n holds zero logic. DeepSeek extraction passes the mandatory 2nd-layer validator (Phase 1 D-11). Graph state persists for multi-day conversations.

---

## Discovery parent-resolution & place_id caching

| Option | Description | Selected |
|--------|-------------|----------|
| Hard precondition: parent destino in Mar; persist only place_id | Resolve parent by territorial key against Mar; skip+audit if absent; place_id is cache only | ✓ |
| Ingest atrativo even without a Mar parent | Risks unresolvable atrativo→destino references | |

**User's choice:** Hard precondition (D-03/D-04). Unresolved parent ⇒ skip + audit (`parent_destino_absent`), retry on later sweep.
**Notes:** COMP-03 / Phase 1 D-17 — canonical data is first-party; place_id rides in the Nascente payload as a cache key only.

---

## SignalAgent score mapping

| Option | Description | Selected |
|--------|-------------|----------|
| Deterministic Places→§7.6 mapping; Apify non-blocking | CLOSED_* = hard pre-score descarte; publishTime≤30d ⇒ atualidade; weekday_text ⇒ completude; Apify/OTA best-effort | ✓ |
| Treat all signals as soft score inputs | No hard descarte; dead places still scored | |

**User's choice:** Deterministic mapping with hard CLOSED_* descarte (D-05).
**Notes:** Apify IG/X and OTA price-check are best-effort, non-blocking (Meta-ToS read-only posture); failures degrade corroboração but never fail a record.

---

## Owner-validation re-score

| Option | Description | Selected |
|--------|-------------|----------|
| Owner confirm → existing re-score path | Raise a validation criterion → `reprocess_record` → promote/`push_attraction`; mirror Phase 2 D-07; no new branch | ✓ |
| New owner-specific scoring branch | Special-case code path parallel to the score engine | |

**User's choice:** Reuse the existing re-score path (D-10), owner validation substituting for steward validation.
**Notes:** `push_attraction` mirrors Phase 2 `push_destination_task` (idempotent by `source_ref`, frozen Pact shape). Negative/no-answer leaves the record in DLQ.

---

## Claude's Discretion

- `AtrativoResult` / conversation-state Pydantic schemas, FSM task topology & Celery queue/task names, consent-log DDL vs audit-log reuse, ramp window (per-UF vs global) + default cap, FastAPI request/response models, LangGraph node layout + prompt text, test-fixture structure.
- The Phase 3 research flag MUST be honored: re-verify Twilio-vs-Meta-Cloud pricing/policy/template categorization/rate caps at build time; design the durable-FSM + multi-day LangGraph conversation.

## Deferred Ideas

- Dashboard WhatsApp-gate / conversations / funnels UI — Phase 4.
- Meta Cloud API direct BSP migration — end-state, behind the same protocol.
- Temporal durable-workflow engine — re-open only on proven Celery FSM pain.
- Active freshness-decay / re-score cron (§7.8, FRESH-01) — v2.
- OTA price cross-check integration (OTA-01) — v2.
- Auto-tuning of §7.6 weights (TUNE-01) — v2.
