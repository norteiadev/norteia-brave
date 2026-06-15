---
phase: 03-atrativos-lane-whatsapp-compliance
plan: "04"
subsystem: whatsapp-agent
tags:
  - langgraph
  - whatsapp
  - compliance
  - celery
  - push-attraction
dependency_graph:
  requires:
    - "03-01"  # schemas, null_whatsapp, WhatsAppConfig, RampConfig
    - "03-02"  # gather_signals_task, discover/find_contacts stubs
    - "03-03"  # send_path_gate, consent_log, quality_rating
  provides:
    - "brave.lanes.atrativos.whatsapp_agent.build_graph"
    - "brave.lanes.atrativos.whatsapp_agent.OPT_OUT_KEYWORDS"
    - "brave.clients.whatsapp.TwilioWhatsAppClient"
    - "brave.tasks.pipeline.outreach_task (full)"
    - "brave.tasks.pipeline.resume_conversation_task (full)"
    - "brave.tasks.pipeline.push_attraction_task"
  affects:
    - "brave/tasks/pipeline.py (outreach_task, resume_conversation_task replaced)"
    - "brave/clients/base.py (LLMClientProtocol.generate added)"
tech_stack:
  added:
    - "langgraph==1.2.5 (StateGraph, MemorySaver, AsyncPostgresSaver)"
    - "langgraph-checkpoint-postgres==3.1.0 (AsyncPostgresSaver for durable conversation)"
  patterns:
    - "asyncio.run(_run()) bridge for async LangGraph in sync Celery workers (Pitfall 5)"
    - "functools.partial for dependency injection into LangGraph async node functions"
    - "thread_id = f'atrativo:{rio_id}' — keyed by UUID, never phone (Pitfall 2)"
    - "Single _compliant_send() wrapper as sole send_template call site (D-11)"
key_files:
  created:
    - "brave/lanes/atrativos/whatsapp_agent.py"
    - "brave/clients/whatsapp.py"
    - "tests/unit/lanes/test_whatsapp_agent.py"
  modified:
    - "brave/tasks/pipeline.py (outreach_task, resume_conversation_task stubs replaced; push_attraction_task added)"
    - "brave/clients/base.py (LLMClientProtocol.generate() added)"
    - "tests/fakes/fake_llm.py (generate() added to FakeLLMClient)"
    - "pyproject.toml (langgraph + langgraph-checkpoint-postgres added)"
decisions:
  - "LangGraph async node functions (not sync): nodes run inside asyncio.run(_run()) event loop so async is required (sync nodes calling async via get_event_loop().run_until_complete() would deadlock)"
  - "message_text in ConversationState (not functools.partial): resume_conversation_task passes reply_text as a state update so LangGraph checkpoint resumes with the new value"
  - "score_config parameter name (not config): avoids LangGraph's special 'config' parameter conflict (UserWarning suppressed)"
  - "send_opening edges to END (not recv_reply): outreach_task and resume_conversation_task are separate Celery invocations — the graph waits at END for the next inbound webhook"
metrics:
  duration_minutes: 13
  completed_date: "2026-06-15"
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 4
---

# Phase 03 Plan 04: WhatsApp Conversation Agent Summary

**One-liner:** LangGraph WhatsAppAgent with AsyncPostgresSaver, Sonnet generation + DeepSeek extraction, TwilioWhatsAppClient, and push_attraction_task completing the full owner-validation loop.

## What Was Built

### Task 1: LangGraph WhatsAppAgent + TwilioWhatsAppClient

**`brave/lanes/atrativos/whatsapp_agent.py`**

The full LangGraph conversation graph implementing the D-08 WhatsApp owner-validation flow:

- `OPT_OUT_KEYWORDS = frozenset({"SAIR", "PARAR", "CANCELAR", "REMOVER", "STOP", "NÃO"})` — module-level constant
- `ConversationState` TypedDict with 11 fields including `message_text` for inbound reply routing
- Five async node functions (`_send_opening_node`, `_recv_reply_node`, `_extract_answers_node`, `_ask_followup_node`, `_finalize_node`)
- `_compliant_send()` — private async function that is the SOLE call site for `wa_client.send_template`; `send_path_gate` always called first (D-11, T-03-04-01)
- Two routing functions (`_after_recv_reply`, `_after_extract_answers`)
- `build_graph()` factory with injectable checkpointer (AsyncPostgresSaver in production, MemorySaver in tests)

Compliance gate enforcement: `send_path_gate` is called inside `_compliant_send` before every `send_template`. No other call to `send_template` exists in the file. grep-verifiable.

**`brave/clients/whatsapp.py`**

`TwilioWhatsAppClient` implementing `WhatsAppClientProtocol` (structural typing):
- Twilio 9.10.x SDK MessagingServiceSid path
- `run_real_externals` guard: raises `RuntimeError` if real sends attempted offline
- `tenacity` retry for 5xx errors
- `_check_protocol_compliance()` structural assertion at module bottom

**Protocol and Fake extensions:**
- `brave/clients/base.py`: `LLMClientProtocol.generate()` added (Sonnet PT-BR generation, D-08)
- `tests/fakes/fake_llm.py`: `FakeLLMClient.generate()` added with `generate_calls` tracking
- `pyproject.toml`: `langgraph==1.2.5` + `langgraph-checkpoint-postgres==3.1.0` declared

**`tests/unit/lanes/test_whatsapp_agent.py`** — 8 offline tests, all pass:

| Test | Behavior Verified |
|------|-------------------|
| `test_opt_out_keyword_routes_to_end` | "SAIR" → `opted_out=True`, `record_opt_out` called |
| `test_opt_out_keyword_detection_partial_match` | "Obrigado mas PARAR" → detected |
| `test_norteia_not_in_params_raises_compliance_error` | Gate condition 2 blocks, `sent_messages==[]` |
| `test_extraction_result_validated_by_pydantic` | Valid `ConversationExtractionResult`, `existe="sim"` |
| `test_extraction_quarantines_invalid_schema` | `ValidationError` → `extraction=None` (no propagation) |
| `test_extraction_quarantines_on_generic_exception` | `RuntimeError` → `extraction=None` |
| `test_build_graph_returns_compiled_graph` | `build_graph()` returns Pregel with `.ainvoke` |
| `test_opt_out_keywords_constant` | Exact 6-keyword frozenset verified |

### Task 2: outreach_task, resume_conversation_task, push_attraction_task

**`brave/tasks/pipeline.py`** — stubs replaced, new task added:

**`outreach_task` (brave.outreach)** — full implementation:
- `asyncio.run(_run())` bridge for async LangGraph in sync Celery context (Pitfall 5)
- `AsyncPostgresSaver.from_conn_string(pg_dsn)` + `await saver.setup()` per invocation
- `NullWhatsAppClient` (production offline) or `TwilioWhatsAppClient` (real); never FakeWhatsAppClient (T-03-04-07)
- `thread_id = f"atrativo:{rio_id}"` — keyed by UUID, not phone (Pitfall 2)
- Sub-state guard: returns immediately if `rio.sub_state != "whatsapp_in_progress"` (idempotent)
- Full `PermanentError / Exception / MaxRetriesExceededError` + `quarantine_poison` pattern

**`resume_conversation_task` (brave.resume_conversation)** — full implementation:
- Same `asyncio.run(_run())` + AsyncPostgresSaver pattern
- Passes `reply_text` as `{"message_text": reply_text}` state update to resume checkpoint
- LangGraph loads from checkpoint → `recv_reply_node` handles opt-out / extract / followup
- Sub-state guard: returns if `sub_state != "whatsapp_in_progress"`

**`push_attraction_task` (brave.push_attraction)** — new task mirroring `push_destination_task`:
- Identical `@shared_task` decorator shape (max_retries=3, time_limit=300)
- Always calls `api_client.push_attraction(payload)` (never `push_destination`)
- `promote_to_mar` (idempotent by source_ref, D-15) before push
- `routing == "mar"` guard for idempotency
- Full error handling pattern

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing] Added `generate()` to `LLMClientProtocol` and `FakeLLMClient`**
- **Found during:** Task 1 implementation
- **Issue:** The plan specified `llm_client.generate(messages, model)` for Sonnet PT-BR generation in `ask_followup_node`, but `LLMClientProtocol` only had `extract()`. Without `generate()`, the protocol contract was incomplete.
- **Fix:** Added `generate()` to `LLMClientProtocol` in `brave/clients/base.py` and `FakeLLMClient` in `tests/fakes/fake_llm.py`
- **Files modified:** `brave/clients/base.py`, `tests/fakes/fake_llm.py`
- **Commit:** 2b03b51

**2. [Rule 1 - Design] Used async node functions instead of sync**
- **Found during:** Task 1 implementation
- **Issue:** The plan specified "regular function" nodes, but nodes that call async operations (`wa_client.send_template`, `llm_client.extract`, `llm_client.generate`) need to be async to avoid event loop conflicts inside `asyncio.run(_run())`
- **Fix:** Made all 5 node functions `async def` (LangGraph 1.x supports both sync and async nodes equally)
- **Files modified:** `brave/lanes/atrativos/whatsapp_agent.py`
- **Commit:** 2b03b51

**3. [Rule 1 - Design] Used `message_text` in ConversationState instead of functools.partial**
- **Found during:** Task 1 implementation
- **Issue:** The plan suggested binding `message_text` via `functools.partial` on `recv_reply_node`, but this would fix the text at graph-build time, preventing resume_conversation_task from providing different reply texts per invocation
- **Fix:** Added `message_text` field to `ConversationState` TypedDict; `resume_conversation_task` passes it as a state update; `_recv_reply_node` reads `state.get("message_text", "")`
- **Files modified:** `brave/lanes/atrativos/whatsapp_agent.py`, `brave/tasks/pipeline.py`
- **Commit:** 2b03b51

**4. [Rule 1 - Bug] Renamed `config` parameter to `score_config` in `_finalize_node`**
- **Found during:** Task 1 tests (UserWarning from LangGraph)
- **Issue:** LangGraph reserves `config` as a special parameter name for `RunnableConfig`. Using it in `functools.partial` caused a `UserWarning` from LangGraph during node registration
- **Fix:** Renamed `config` → `score_config` in `_finalize_node` and its `functools.partial` binding
- **Files modified:** `brave/lanes/atrativos/whatsapp_agent.py`
- **Commit:** 2b03b51

## Success Criteria Verification

| Criterion | Status |
|-----------|--------|
| `pytest tests/unit/lanes/test_whatsapp_agent.py` exits 0 (8 tests) | PASS |
| `outreach_task.name == "brave.outreach"` | PASS |
| `resume_conversation_task.name == "brave.resume_conversation"` | PASS |
| `push_attraction_task.name == "brave.push_attraction"` | PASS |
| `grep "send_path_gate" whatsapp_agent.py` ≥ 1 match | PASS (4 matches) |
| `grep "FakeWhatsAppClient" pipeline.py` == 0 | PASS (0 matches) |
| `grep "thread_id.*atrativo" pipeline.py` ≥ 2 matches | PASS (4 matches) |
| `python -c "from brave.tasks.pipeline import outreach_task, resume_conversation_task, push_attraction_task"` exits 0 | PASS |
| No modifications to STATE.md or ROADMAP.md | PASS |

## Known Stubs

None — all stub bodies replaced; `push_attraction_task` is a complete implementation.

## Threat Surface Scan

No new network endpoints or trust boundaries introduced beyond those in the plan's `<threat_model>`:
- `_compliant_send` is the sole `send_template` call site (T-03-04-01 ✓)
- `thread_id = f"atrativo:{rio_id}"` (T-03-04-02 ✓)
- Phone number excluded from LLM prompts (T-03-04-03 ✓)
- `max_turns` guard in `_ask_followup_node` (T-03-04-04 ✓)
- `NullWhatsAppClient` used in production tasks when offline (T-03-04-07 ✓)

## Self-Check: PASSED

Files verified:
- `brave/lanes/atrativos/whatsapp_agent.py` ✓
- `brave/clients/whatsapp.py` ✓
- `tests/unit/lanes/test_whatsapp_agent.py` ✓
- `brave/tasks/pipeline.py` (push_attraction_task, outreach_task, resume_conversation_task) ✓

Commits verified:
- `2b03b51` (Task 1) ✓
- `3db8d29` (Task 2) ✓
