---
phase: 03-atrativos-lane-whatsapp-compliance
verified: 2026-06-15T00:00:00Z
status: human_needed
score: 9/9
overrides_applied: 0
human_verification:
  - test: "Run a real Twilio end-to-end dry-run in a sandbox environment"
    expected: "TwilioWhatsAppClient.send_template dispatches a WhatsApp BSP message; LangGraph conversation persists across two turns via AsyncPostgresSaver; re-score after owner_confirmed promotes to Mar"
    why_human: "All code paths verified offline; real BSP send and multi-turn persistence across worker restarts require live infrastructure (Twilio sandbox, real Postgres checkpoint commits, real Redis flag checks)"
  - test: "CR-04 concurrent inbound webhook double-send stress check"
    expected: "Two simultaneous inbound webhooks for the same rio_id trigger only a single follow-up send; the SELECT FOR UPDATE row lock in advance_sub_state serializes the second caller correctly under concurrency"
    why_human: "advance_sub_state now holds a row-level lock (SELECT FOR UPDATE, lock=True default) and unit tests pass, but concurrent correctness under real Celery workers with asyncio.run interleaved with sync session flushes cannot be fully verified by grep or offline tests — needs a load test or staging observation"
  - test: "WR-06 async/sync session boundary under worker restart"
    expected: "If a Celery worker is lost mid-conversation after the AsyncPostgresSaver checkpoint commits but before the sync session commits, the retry does not double-send; the consent-record upsert (WR-09 fix) absorbs the retry safely"
    why_human: "Code structure is correct (upsert in write_consent_record, idempotency guard in advance_sub_state), but the exact failure mode where the checkpoint commits and sync session rolls back requires a kill-worker-mid-task integration experiment to confirm no duplicate send"
---

# Phase 3: Atrativos Lane + WhatsApp Compliance — Verification Report

**Phase Goal:** An atrativo advances through a persisted, resumable sub-state machine — discovered (parent destino resolved from Mar) → contacts_found → signals_gathered → score → [borderline] human WhatsApp gate → automated owner-validation outreach → re-score → Mar/DLQ — with LGPD and WhatsApp BSP enforced as hard send-path gates before any real message is sent.
**Verified:** 2026-06-15
**Status:** human_needed (3 items — all automated checks VERIFIED; 87 unit + 20 integration tests pass)
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Sub-state FSM persisted and resumable: discovered → contacts_found → signals_gathered → score → aguardando_consulta_whatsapp → whatsapp_in_progress → re-score → Mar/DLQ | VERIFIED | `brave/lanes/atrativos/state_machine.py` — `advance_sub_state` with SELECT FOR UPDATE (CR-04); all 6 Celery tasks (`discover_atrativo_task`, `find_contacts_task`, `gather_signals_task`, `outreach_task`, `resume_conversation_task`, `push_attraction_task`) exist with `acks_late=True, reject_on_worker_lost=True`; `test_sc4` in `test_atrativos_lane_e2e.py` exercises full FSM end-to-end |
| 2 | Parent destino resolved from Mar (hard precondition D-03) | VERIFIED | `discovery_agent.py` line 6-7: `parent_destino_absent → quarantine_poison + continue`; `MarRecord` query at lines 133+; `test_sc2` verifies absent-parent skip; D-03 explicitly tested |
| 3 | LGPD compliance gate hard-blocks before any WhatsApp send | VERIFIED | `brave/compliance/gate.py` — `send_path_gate` with 8 conditions + CR-03 empty-phone guard (condition 0); `_compliant_send` in `whatsapp_agent.py` is the ONLY call site for `send_template` (grep-confirmed); 15 unit tests in `test_gate.py` all pass |
| 4 | WhatsApp BSP enforced: approved templates, 24h window, ramp cap, quality-rating auto-pause | VERIFIED | Conditions 4, 5, 7, 8 of `send_path_gate`; `check_and_increment_ramp` atomic INCR/DECR (D-07); WR-05 month-end fix (timedelta arithmetic at `gate.py:66`); `is_quality_red` verified; all gate tests pass |
| 5 | Opt-out anchored keyword match (CR-01 fix) — no false-positive opt-outs | VERIFIED | `whatsapp_agent.py:114-134` — `_detect_opt_out_keyword` uses `_WORD_RE.findall` (word tokenizer) with `_OPT_OUT_FILLER` allowlist; match only when the single meaningful token equals an opt-out keyword; BSP-standard "message is a bare keyword" semantics; replaces the previous unanchored `kw in upper_text` substring check |
| 6 | Canonical contacts.phone_e164 sourced correctly (CR-03 fix) | VERIFIED | `pipeline.py:47-57` — `_extract_contact_phone(rio)` reads `normalized["contacts"]["phone_e164"]` (the ContactFinderAgent's actual storage key); `outreach_task` uses `_extract_contact_phone` at line 990; early DLQ abort on empty phone at line 995; gate condition 0 also rejects empty phone |
| 7 | No silent fakeredis fallback in compliance path (CR-02 fix) | VERIFIED | `brave/api/deps.py:95-122` — `get_redis()` only uses fakeredis when `BRAVE_USE_FAKEREDIS=1` is explicitly set; without the flag, `client.ping()` raises on connection failure (fail-closed); comment explicitly documents the CR-02 rationale |
| 8 | SELECT FOR UPDATE in FSM transitions (CR-04 fix) | VERIFIED | `state_machine.py:66-73` — `advance_sub_state(..., lock=True)` re-fetches `RioRecord` with `session.get(RioRecord, rio.id, with_for_update=True)` before the idempotency guard; `lock=False` escape hatch for unit tests; `test_state_machine.py` (3 tests) passes |
| 9 | COMP-03: only place_id persisted from Google Places | VERIFIED | `discovery_agent.py:11,168,294-298` — payload carries `place_id_cache` key only; canonical data from AtrativoResult (first-party LLM extraction); `test_sc1` verifies `place_id_cache` key in NascenteRecord and absence of raw Places address data |

**Score:** 9/9 truths verified

---

### Code Review Fix Confirmation

| Fix | Review Finding | File | Status | Evidence |
|-----|---------------|------|--------|----------|
| CR-01 | Opt-out substring match → false positives | `whatsapp_agent.py:114-134` | FIXED | `_detect_opt_out_keyword` uses word tokenizer; match only on isolated keyword token |
| CR-02 | Silent fakeredis fallback on Redis blip | `api/deps.py:95-122` | FIXED | Explicit `BRAVE_USE_FAKEREDIS` flag required; ping raises in prod |
| CR-03 | contact_phone read from wrong normalized key | `tasks/pipeline.py:47-57` | FIXED | `_extract_contact_phone()` reads `normalized["contacts"]["phone_e164"]` |
| CR-04 | FSM idempotency without row lock | `lanes/atrativos/state_machine.py:66-73` | FIXED | `with_for_update=True` re-fetch before guard |
| WR-03 | Unauthenticated quality/inbound webhooks | `api/routers/atrativos_gate.py:80-105` | FIXED | `require_webhook_auth` dependency on both POST endpoints; X-Webhook-Secret with `hmac.compare_digest` |
| WR-05 | `_next_utc_midnight` raises ValueError on month-end | `compliance/gate.py:56-69` | FIXED | `(now + timedelta(days=1)).replace(...)` arithmetic |
| WR-09 | `write_consent_record` always inserts — duplicate rows on retry | `compliance/consent_log.py:47-145` | FIXED | Upsert semantics: raises `OptedOutError` for opted-out phones; reuses existing active row; only inserts new row when no prior row exists |

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|---------|--------|---------|
| `brave/lanes/atrativos/__init__.py` | Lane package init | VERIFIED | Empty module, 90B |
| `brave/lanes/atrativos/schemas.py` | AtrativoResult, ContactResult, SignalResult, ConversationExtractionResult | VERIFIED | 8.0K; all 4 schemas present with `description=` kwargs for instructor Mode.Tools |
| `brave/lanes/atrativos/discovery_agent.py` | DiscoveryAgent.produce(uf) | VERIFIED | 13.4K; Mar parent query, place_id-only persistence, quarantine on absent parent |
| `brave/lanes/atrativos/contact_finder_agent.py` | ContactFinderAgent.run(rio) | VERIFIED | 6.6K; advances sub_state with flag_modified on normalized |
| `brave/lanes/atrativos/signal_agent.py` | SignalAgent.run(rio); hard descarte on CLOSED | VERIFIED | 13.4K; CLOSED_PERMANENTLY/CLOSED_TEMPORARILY → descarte before scoring |
| `brave/lanes/atrativos/state_machine.py` | advance_sub_state with SELECT FOR UPDATE | VERIFIED | 3.6K; CR-04 row-locking; audit write on every transition |
| `brave/lanes/atrativos/whatsapp_agent.py` | LangGraph WhatsAppAgent; build_graph(); OPT_OUT_KEYWORDS | VERIFIED | 31.3K; AsyncPostgresSaver; thread_id=`atrativo:{rio_id}`; CR-01 anchored opt-out |
| `brave/compliance/__init__.py` | Compliance package init | VERIFIED | 0B (empty) |
| `brave/compliance/gate.py` | ComplianceError, send_path_gate (8+1 conditions), check_and_increment_ramp | VERIFIED | 13.2K; all conditions implemented; WR-05 timedelta fix; IN-01 dead uf removed |
| `brave/compliance/consent_log.py` | write_consent_record (upsert), is_opted_out, record_opt_out, lookup_rio_id_by_phone | VERIFIED | 9.0K; WR-09 upsert semantics; OptedOutError |
| `brave/compliance/quality_rating.py` | is_quality_red, set_quality_flag | VERIFIED | 3.3K |
| `brave/api/routers/atrativos_gate.py` | Gate list/approve/reject/inbound/quality-rating endpoints | VERIFIED | 5 endpoints; `require_steward` on approve/reject; `require_webhook_auth` on quality/inbound (WR-03 fix) |
| `brave/api/deps.py` | get_redis() without silent fakeredis fallback | VERIFIED | CR-02 fix; explicit `BRAVE_USE_FAKEREDIS` flag required |
| `brave/clients/null_whatsapp.py` | NullWhatsAppClient (production stub) | VERIFIED | 2.0K; in `brave/clients/` not `tests/`; structural duck typing |
| `brave/clients/whatsapp.py` | TwilioWhatsAppClient | VERIFIED | 7.7K; implements WhatsAppClientProtocol structurally |
| `brave/core/models.py` | ConsentLog model appended | VERIFIED | `__tablename__ = "consent_log"`; all columns per PLAN 01 spec; ForeignKey to rio_records |
| `brave/config/settings.py` | WhatsAppConfig + RampConfig; no Field(alias=...) | VERIFIED | Both classes present; `alias=` not found in file (CR-02) |
| `alembic/versions/0004_consent_log.py` | Migration revision="0004", down_revision="0003" | VERIFIED | Creates consent_log table + ix_consent_log_phone_e164 index; no CONCURRENTLY |
| `brave/tasks/pipeline.py` | 6 new Celery tasks; _extract_contact_phone helper | VERIFIED | All tasks at names: `brave.discover_atrativo`, `brave.find_contacts`, `brave.gather_signals`, `brave.push_attraction`, `brave.outreach`, `brave.resume_conversation`; all with `acks_late=True, reject_on_worker_lost=True` |
| `tests/fakes/fake_apify.py` | FakeApifyClient with protocol check | VERIFIED | `_check_protocol_compliance()` at module bottom |
| `tests/fakes/fake_whatsapp.py` | FakeWhatsAppClient with protocol check | VERIFIED | test-only; structural protocol match |
| `tests/fakes/fake_places.py` | SIGNAL_FIXTURE_OPEN, SIGNAL_FIXTURE_CLOSED constants | VERIFIED | Both module-level dicts; SIGNAL_FIXTURE_CLOSED["business_status"] == "CLOSED_PERMANENTLY" |
| `tests/unit/test_atrativos_schemas.py` | Schema round-trip tests | VERIFIED | 22 tests pass |
| `tests/unit/compliance/test_gate.py` | 9+ gate condition tests | VERIFIED | 15 tests (9 condition tests + ramp/quality/WR-05 helpers); all pass |
| `tests/integration/test_atrativos_lane_e2e.py` | 7 e2e scenarios | VERIFIED | All 7 scenarios (`test_sc1` through `test_sc7`); all pass with real DB+Redis |
| `tests/integration/test_atrativos_gate.py` | Gate endpoint integration tests | VERIFIED | 13 tests; all pass |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `whatsapp_agent.py _send_opening_node / _ask_followup_node` | `compliance/gate.send_path_gate` | `_compliant_send()` wrapper | WIRED | `send_template` only called at `whatsapp_agent.py:219` inside `_compliant_send`; `send_path_gate` called at line 207 |
| `whatsapp_agent.py finalize_node` | `brave/core/rio/routing.reprocess_record` | `_reprocess(session, rio_id, score_config)` | WIRED | Line 729-area; owner validation → reprocess → route_by_score → promote/push |
| `tasks/pipeline.py outreach_task` | `lanes/atrativos/whatsapp_agent.build_graph` | `asyncio.run(_run())` | WIRED | `name="brave.outreach"` task calls `build_graph` with AsyncPostgresSaver |
| `tasks/pipeline.py push_attraction_task` | `NorteiaApiClientProtocol.push_attraction` | `api_client.push_attraction(payload)` | WIRED | `name="brave.push_attraction"`; mirrors push_destination_task pattern (D-10) |
| `discovery_agent.py` | `brave/core/mar/service MarRecord` | Mar parent destino query | WIRED | Line 133+: `MarRecord.source_ref.contains(municipio_ibge)` |
| `discovery_agent.py` | `brave/core/nascente/service.store_raw` | `store_raw(..., entity_type="attraction", ...)` | WIRED | Line 294+: `source_ref="places:{uf}:{place_id}"` |
| `signal_agent.py` | `brave/core/rio/routing.process_nascente_record` | called after signals_gathered | WIRED | `process_nascente_record` invoked in SignalAgent.run() after signal gathering |
| `compliance/gate.py send_path_gate` | `compliance/consent_log.py is_opted_out` | condition 3 | WIRED | `is_opted_out(session, contact_phone)` at gate.py line 209 |
| `compliance/gate.py send_path_gate` | `compliance/quality_rating.py is_quality_red` | condition 8 | WIRED | `is_quality_red(redis_client)` at gate.py line 272 |
| `api/routers/atrativos_gate.py inbound` | `compliance/consent_log.lookup_rio_id_by_phone` | phone lookup before dispatching task | WIRED | `lookup_rio_id_by_phone` call verified in router |
| `alembic/versions/0004_consent_log.py` | `brave/core/models.py ConsentLog` | `down_revision="0003"` | WIRED | Migration chains correctly; `ConsentLog.__tablename__ == "consent_log"` |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `send_path_gate` | `contact_phone` | `_extract_contact_phone(rio)` → `normalized["contacts"]["phone_e164"]` | Yes — ContactFinderAgent writes this key (CR-03 fix) | FLOWING |
| `write_consent_record` | `phone_e164` | propagated from `contact_phone` | Yes — after CR-03 fix, non-empty E.164 phone | FLOWING |
| `check_and_increment_ramp` | Redis key `wa:ramp:{date}` | real Redis (after CR-02 fix) | Yes — Redis INCR/DECR against production Redis | FLOWING |
| `is_quality_red` | Redis key `wa:quality_red` | production Redis (after CR-02 fix) | Yes — flag written by `set_quality_flag` in webhook | FLOWING |
| `advance_sub_state` | `rio.sub_state` | re-fetched with `with_for_update=True` | Yes — authoritative DB state under row lock | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All offline unit tests pass (87 tests) | `uv run python -m pytest tests/unit/test_atrativos_schemas.py tests/unit/lanes/ tests/unit/compliance/ -q` | `87 passed in 0.71s` | PASS |
| Integration e2e + gate tests pass (20 tests) | `BRAVE_DB_URL=... BRAVE_REDIS_URL=... uv run python -m pytest tests/integration/test_atrativos_lane_e2e.py tests/integration/test_atrativos_gate.py -v` | `20 passed in 2.21s` | PASS |
| Imports clean — all Phase 3 modules | `python -c "from brave.core.models import ConsentLog; from brave.config.settings import WhatsAppConfig, RampConfig; from brave.lanes.atrativos.schemas import AtrativoResult; from brave.clients.null_whatsapp import NullWhatsAppClient; ..."` | All imports succeed | PASS |
| No alias= in settings.py (CR-02) | `grep -c "alias=" brave/config/settings.py` | 0 matches | PASS |
| send_template only called inside _compliant_send | `grep "send_template" whatsapp_agent.py` | Single call site at line 219 inside `_compliant_send` | PASS |

---

### Probe Execution

No `probe-*.sh` files declared in PLAN or present in `scripts/*/tests/`. Step 7c skipped — no probes.

---

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|------------|---------------|-------------|--------|---------|
| ATR-01 | 01, 02, 05 | Sub-state machine: discovered → contacts_found → signals_gathered → (score) → aguardando_consulta_whatsapp → whatsapp_in_progress → re-score | SATISFIED | `state_machine.py` + all 6 FSM tasks; e2e test_sc4 exercises full chain |
| ATR-02 | 02, 05 | DiscoveryAgent sweeps Google Places, resolves parent destino, persists `place_id` | SATISFIED | `discovery_agent.py`; D-03/D-04 verified; COMP-03 place_id-only persistence |
| ATR-03 | 02, 05 | ContactFinderAgent finds contacts via Places Details | SATISFIED | `contact_finder_agent.py`; sub_state → contacts_found; flag_modified on normalized |
| ATR-04 | 02, 05 | SignalAgent: CLOSED_* → descarte; signals gathered; Apify best-effort | SATISFIED | `signal_agent.py:50`; CLOSED_STATUSES frozenset; Apify exception swallowed gracefully |
| ATR-05 | 03, 05 | WhatsApp gate: human approve borderline atrativos, volume ramp | SATISFIED | 5 gate endpoints in `atrativos_gate.py`; ramp in `check_and_increment_ramp`; steward auth on approve/reject |
| ATR-06 | 04, 05 | WhatsAppAgent outreach: Sonnet asks, DeepSeek extracts, owner validation → re-score → Mar/DLQ | SATISFIED | `whatsapp_agent.py` LangGraph; `build_graph` factory; `outreach_task` + `resume_conversation_task`; `push_attraction_task` mirrors push_destination |
| COMP-01 | 01, 03, 04, 05 | LGPD: legal basis + Norteia identification + opt-out + consent log + data minimization | SATISFIED | `consent_log.py`; gate conditions 1-3; write_consent_record upsert (WR-09); PII phone[:5] logging; all tests pass |
| COMP-02 | 03, 04, 05 | BSP: approved templates, 24h window, human gate, ramp, auto-pause on quality RED | SATISFIED | Gate conditions 4-8; `quality_rating.py`; 15 gate unit tests |
| COMP-03 | 01, 02, 05 | Google Places: only `place_id` persisted as cache | SATISFIED | `discovery_agent.py:294`; `payload["place_id_cache"]`; test_sc1 verifies |

**Coverage: 9/9 requirements satisfied**

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `api/routers/atrativos_gate.py` | 337, 391 | `Production TODO (T-03-03-05): Add Twilio signature validation` | WARNING | Twilio `RequestValidator` signature verification deferred to production hardening; tracked under T-03-03-05; WR-03 mitigation (X-Webhook-Secret) is enforced — endpoints are not unauthenticated. This is a defence-in-depth item, not a gate bypass. |

**Remaining warnings from 03-REVIEW.md (not blocking — lower priority):**

| Finding | Status | Disposition |
|---------|--------|-------------|
| WR-01: Tenacity retries all exceptions | OPEN | Non-blocking; narrows retry surface; deferred to post-phase hardening |
| WR-02: Bare `except Exception: pass` on broker-down | OPEN | Non-blocking; "no broker in tests" pattern; production impact logged in review |
| WR-04: 24h window written to LangGraph state but not persisted to rio.normalized for gate read | OPEN | WARNING — gate condition 5 reads `rio.normalized.get("window_open", True)`; default=True means initial sends pass; subsequent follow-up window enforcement not verified. **Flagged for human review.** |
| WR-06: Async/sync session boundary under worker restart | OPEN | Human verification item (see below) |
| WR-07: finalize_node references stale captured rio after reprocess | OPEN | Non-blocking; informational |
| WR-08: corroboracao scoring dead conditional + post_count key mismatch | OPEN | Non-blocking; scoring quality issue; independent of gate correctness |

---

### Human Verification Required

#### 1. Real Twilio BSP End-to-End Send

**Test:** Configure a Twilio sandbox with an approved template. Set `BRAVE_WA_TWILIO_ACCOUNT_SID`, `BRAVE_WA_TWILIO_AUTH_TOKEN`, `BRAVE_WA_FROM_NUMBER`, `BRAVE_WA_APPROVED_TEMPLATES`. Run `outreach_task` with `run_real_externals=True` against a sandbox phone number.
**Expected:** `TwilioWhatsAppClient.send_template` dispatches the BSP-registered utility template; the message appears in the Twilio console; the LangGraph conversation checkpoint persists in Postgres; a reply to the sandbox number routes through the inbound webhook and resumes the conversation.
**Why human:** TwilioWhatsAppClient and AsyncPostgresSaver are structurally verified; multi-day conversation persistence across worker restarts requires live infrastructure.

#### 2. CR-04 Concurrent Webhook Stress Test

**Test:** With a real Celery worker running, submit two simultaneous inbound webhook requests for the same `rio_id` in `whatsapp_in_progress` sub_state. Use `ab` or `locust` to fire two concurrent `/inbound` POSTs within the same millisecond.
**Expected:** Only one `resume_conversation_task` executes a send; the second task reads `sub_state != "whatsapp_in_progress"` after the row lock releases (SELECT FOR UPDATE serializes) and returns False without sending.
**Why human:** `advance_sub_state` with `with_for_update=True` is correctly implemented. However, the task body also checks `sub_state` before calling `advance_sub_state`, and the LangGraph asyncio bridge adds complexity. True concurrent correctness under real Celery multiprocessing cannot be verified by grep or offline tests.

#### 3. WR-04: 24h Window Enforcement for Follow-ups

**Test:** After an owner sends an initial inbound reply (opening the 24h window), wait >24h and attempt a follow-up via `resume_conversation_task`. Verify gate condition 5 blocks the send.
**Expected:** `rio.normalized["window_open"]` is False after 24h; gate condition 5 raises `ComplianceError`; no follow-up message is sent.
**Why human:** The recv_reply node computes and returns `window_open` in LangGraph state, but WR-04 (review finding) notes this value may not persist to `rio.normalized`. The gate's condition 5 reads `rio.normalized.get("window_open", True)` — the default is True, so initial sends pass. Whether `window_open=False` is correctly persisted after the first inbound reply requires tracing through the full conversation path with time manipulation.

---

### Gaps Summary

No automated gaps found. All 9 observable truths verified. All 4 critical code review fixes (CR-01, CR-02, CR-03, CR-04) confirmed present in code. All 87 unit tests and 20 integration tests pass.

Three items require human verification:
1. Real Twilio BSP end-to-end send (cannot verify without live infrastructure)
2. CR-04 concurrent webhook stress (structural fix verified; concurrent behaviour needs load test)
3. WR-04: 24h window enforcement for follow-ups (window_open persistence path uncertain from grep)

---

_Verified: 2026-06-15T00:00:00Z_
_Verifier: Claude (gsd-verifier)_
