# Phase 3: Atrativos Lane (WhatsApp + Compliance) - Research

**Researched:** 2026-06-12
**Domain:** Durable sub-state FSM (Celery+Postgres), LangGraph conversation graph (Sonnet/DeepSeek), WhatsApp BSP (Twilio), LGPD compliance gate, Google Places (New) + Apify signals
**Confidence:** HIGH (stack verified against PyPI; BSP policy verified against current Meta/Twilio docs; LangGraph checkpointer verified; compliance design from authoritative sources)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01:** Stay on Celery + Redis with celery-redbeat. FSM modeled as idempotent tasks keyed off RioRecord.sub_state. Day-scale human-gate wait is held as queue state (record sits in aguardando_consulta_whatsapp), not a blocked worker.

**D-02:** sub_state is the single source of truth for FSM position, advanced by supersession-safe writes; transitions write an audit row (actor = agent name / steward). Canonical FSM values: discovered → contacts_found → signals_gathered → aguardando_consulta_whatsapp → whatsapp_in_progress.

**D-03:** Parent destino resolution from Mar is a hard precondition. If no parent destino is in Mar, the atrativo is NOT ingested — log+audit the skip (parent_destino_absent) and retry on a later sweep.

**D-04:** Persist only Google place_id as cache (COMP-03 / Phase 1 D-17). Canonical data is first-party validated. place_id in Nascente payload as cache key.

**D-05:** SignalAgent: business_status CLOSED_* → hard pre-score descarte; reviews[].publishTime ≤ 30 days → atualidade funcionando; weekday_text → completude hours. Apify IG/X best-effort non-blocking. OTA optional corroboration for ticketed only.

**D-06:** Gate is a FastAPI endpoint mirroring Phase 2 DLQ steward pattern — queue list + approve (flip to whatsapp_in_progress + enqueue outreach task) + reject (route dlq/descarte). No automated outreach without human approve.

**D-07:** Volume ramp = Redis counter reusing Phase 1 cost-guard counter pattern (atomic INCR + ceiling check, daily/UTC key, crash-safe TTL — apply CR-04 reserve-before-call hardening). Ramp limits in config (pydantic-settings), not code constants.

**D-08:** All conversation logic in LangGraph code; n8n is thin transport only. WhatsAppAgent: Sonnet 4.5 via native Anthropic SDK generates PT-BR turns (identifies Norteia + opt-out); DeepSeek (instructor + Mode.Tools) extracts structured answers; graph state persists so multi-day conversation survives restarts.

**D-09:** WhatsApp transport is Twilio at launch, behind WhatsAppClientProtocol.send_template. Faked in default suite. Meta Cloud API is cost-optimized end-state migrated behind the same interface later.

**D-10:** Owner-validation feeds existing reprocess_record → promote_to_mar → push_attraction task (mirror Phase 2 push_destination_task; idempotent by source_ref; frozen Pact shape). Negative/no-answer → DLQ.

**D-11:** Single hard send-path gate function (evaluated immediately before WhatsAppClientProtocol.send_template): legal basis recorded + Norteia identification present + opt-out honored + approved BSP template + 24h window respected + human gate + ramp satisfied + data minimization. Failed assertion raises and blocks send. Auto-pause on degraded quality rating. Consent/opt-out log table records legal basis + opt-out per contact. Every gate condition has an offline unit test.

### Claude's Discretion

AtrativoResult / conversation-state Pydantic schemas, FSM task topology and Celery queue/task names, consent-log table DDL vs reusing audit log, precise ramp window (per-UF vs global) and default cap, FastAPI request/response models for gate/queue endpoints, LangGraph node layout and prompt text, and test-fixture structure.

### Deferred Ideas (OUT OF SCOPE)

- Dashboard WhatsApp-gate / conversations / funnels UI (Phase 4)
- Meta Cloud API direct BSP migration
- Temporal durable-workflow engine
- Active freshness-decay / re-score cron (FRESH-01, §7.8)
- OTA price cross-check full integration (OTA-01)
- Auto-tuning of §7.6 weights from steward/owner decisions (TUNE-01)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ATR-01 | Persisted, resumable sub_state machine: discovered → contacts_found → signals_gathered → score → [borderline] aguardando_consulta_whatsapp → whatsapp_in_progress → re-score | D-01/D-02: RioRecord.sub_state already exists (String(64)); Celery idempotent-task FSM pattern; PostgresSaver checkpointer for LangGraph conversation layer |
| ATR-02 | DiscoveryAgent sweeps Google Places (UF/município) + gov, maps via DeepSeek → schema → Nascente, resolves parent destino from Mar, persists place_id | D-03/D-04: parent resolution query against Mar; AtrativoResult Pydantic schema; google-maps-places 0.9.x text_search; COMP-03 place_id-only persistence |
| ATR-03 | ContactFinderAgent finds contacts via Places Details (phone/website/WhatsApp link) + site/IG-FB/email | PlacesClientProtocol.place_details already seamed; FakePlacesClient already exists; ContactResult schema needed |
| ATR-04 | SignalAgent: business_status CLOSED_* → descarte; weekday_text → hours; reviews[].publishTime ≤ 30d → atualidade; IG/X via Apify best-effort | D-05: deterministic field mapping; ApifyClientProtocol.scrape_ig already seamed; best-effort/non-blocking pattern |
| ATR-05 | WhatsApp gate: human approve/reject borderline (<85%) atrativos, volume ramp | D-06/D-07: FastAPI endpoint mirrors dlq.py steward pattern; Redis ramp counter with CR-04 atomicity |
| ATR-06 | WhatsAppAgent: Sonnet asks PT-BR + DeepSeek extracts existe?/funcionando?/horários/valor; owner-validation → re-score → Mar/DLQ | D-08/D-10: LangGraph graph + PostgresSaver; push_attraction_task mirrors push_destination_task |
| COMP-01 | LGPD: legal basis + Norteia identification + opt-out + consent log + data minimization | D-11: consent_log table; send-path gate function; opt-out suppression check |
| COMP-02 | BSP: approved templates + 24h window + human gate + ramp + opt-out + auto-pause on degraded quality rating | D-11: quality rating auto-pause; template category compliance; 24h window awareness in FSM |
| COMP-03 | Google Places: persist only place_id as cache; canonical = first-party validated | D-04: enforced at PlacesClient layer — raw Google content is transient signal only |
</phase_requirements>

---

## Summary

Phase 3 is the hardest and riskiest phase because it combines five independent complexity domains that must all work correctly together before the first real WhatsApp message can be sent: (1) a multi-step durable sub-state machine that survives 24/7 worker restarts, (2) a LangGraph multi-turn conversation graph with PostgreSQL-persisted state across days, (3) a WhatsApp BSP integration with strict template/quality-rating/window compliance, (4) LGPD-mandatory consent and opt-out gates enforced in code before every send, and (5) four real external clients (Places, Apify, WhatsApp, Anthropic) that must all be fakeable for a 100%-offline default test suite. Phase 1 and Phase 2 together have already built the entire foundation this phase plugs into: RioRecord.sub_state exists, WhatsAppClientProtocol and all other client Protocol seams exist, FakePlacesClient exists, the DLQ steward endpoint pattern exists, push_destination_task exists. This phase writes the real implementations and adds the new column types and tables — it does not redesign foundations.

The key architectural insight is the Celery+LangGraph split: Celery drives the macro FSM (days-scale state transitions keyed off sub_state), while LangGraph drives the micro FSM (multi-turn conversation within one whatsapp_in_progress step). LangGraph's PostgresSaver (`langgraph-checkpoint-postgres` 3.1.x) provides the persistence bridge that lets a conversation survive Celery worker restarts. The thread_id for each conversation maps to the RioRecord.id (or a stable derivative), so any worker can resume any conversation.

The Phase 3 research flag mandate is satisfied: Twilio BSP pricing has shifted to per-message billing (July 2025), template categories are authentication/utility/marketing with utility messages free inside the 24h service window, and WhatsApp Business messaging limits since October 2025 are portfolio-wide (not per-number) starting at 250/24h for new portfolios — facts that directly affect ramp design and template strategy.

**Primary recommendation:** Build the compliance gate first (COMP-01/02 — it blocks everything), then the FSM scaffold (ATR-01), then agents (ATR-02..04) in discovery-order, then the WhatsApp gate (ATR-05), then the WhatsAppAgent conversation (ATR-06). Never write a real send before every offline gate test passes green.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Atrativo sub-state FSM (discovered→re-score) | Celery worker (RioRecord.sub_state in Postgres) | FastAPI (gate endpoint) | Day-scale latency; state must survive restarts; DB is the durable store |
| WhatsApp conversation turns (multi-turn LLM) | LangGraph graph (inside Celery task) | PostgresSaver (checkpoint in Postgres) | Bounded multi-turn within one FSM step; LangGraph's checkpointer handles node-level persistence |
| Human WhatsApp gate (approve/reject queue) | FastAPI router (API layer) | Celery task (outreach dispatch) | Same pattern as DLQ steward endpoint; dashboard (Phase 4) will consume this surface |
| LGPD/BSP compliance gate | Python send-path gate function (collector) | — | Must run in-process, synchronously, before every call to WhatsAppClientProtocol.send_template |
| Consent/opt-out log | Postgres table (consent_log) | AuditLog (piggyback audit) | Separate table for suppression lookups; audit_log for regulatory trail |
| Volume ramp counter | Redis (atomic INCR, daily UTC key) | pydantic-settings (config) | Mirrors CR-04-hardened cost-guard pattern; must be atomic across workers |
| Discovery / Contact / Signal agents | Celery tasks advancing sub_state | PlacesClientProtocol / ApifyClientProtocol (behind network boundary) | Every external call behind fakeable client interface |
| Owner-validation → re-score → push | Existing reprocess_record + promote_to_mar + push_attraction_task | NorteiaApiClientProtocol (Pact-frozen) | Mirror push_destination_task exactly; idempotent by source_ref |
| Quality rating auto-pause | Redis flag (set by webhook / quality-rating probe) | FastAPI webhook receiver | Pause ramp counter / gate before sends when rating is Red |

---

## Standard Stack

### Core (all already in pyproject.toml from Phases 1+2 — no new installs except one)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| **langgraph** | 1.2.5 | WhatsApp conversation graph + agent orchestration | Already in stack; 1.x GA line; graph/state-machine maps directly to multi-turn conversation |
| **langgraph-checkpoint-postgres** | 3.1.0 (NEW) | PostgreSQL-backed checkpointer for LangGraph conversation state | Required to persist conversation turns across Celery worker restarts; uses existing Postgres/psycopg3 infrastructure |
| **anthropic** | 0.109.x | Sonnet 4.5 PT-BR conversation (WhatsAppAgent) | Already wired in settings.anthropic_api_key (CR-02 no-alias); native SDK for streaming/tool-use |
| **openai** | 2.41.x | OpenRouter/DeepSeek extraction (DeepSeek extracts existe?/funcionando?/horários/valor) | Already in stack; OpenRouter-compatible |
| **instructor** | 1.15.x | Structured LLM output + 2nd-layer validator (DeepSeek extraction) | Already in stack; Mode.Tools for DeepSeek |
| **twilio** | 9.10.x | WhatsApp BSP transport (send_template behind WhatsAppClientProtocol) | Already in stack; launch BSP per D-09 |
| **google-maps-places** | 0.9.x | Places (New) text_search + place_details for Discovery/Contact/Signal agents | Already in stack; New API has business_status, weekday_text, reviews[].publishTime |
| **apify-client** | 3.0.x | IG/X scraping (best-effort SignalAgent signal) | Already in stack; behind ApifyClientProtocol |
| **celery** | 5.6.x | Macro-FSM orchestration (sub_state transitions as idempotent tasks) | Already in stack |
| **redis** | 8.0.x | Celery broker + ramp counter | Already in stack |
| **pydantic** | 2.13.x | AtrativoResult, ContactResult, ConversationExtractionResult schemas; consent log validation | Already in stack |
| **pydantic-settings** | 2.14.x | WhatsApp/BSP/ramp config extension (ramp caps, template names, quality rating threshold) | Already in stack |
| **sqlalchemy** | 2.0.x | consent_log table + new Alembic migration | Already in stack |
| **alembic** | 1.18.x | Migration: consent_log table | Already in stack |
| **structlog** | 26.x | Structured audit logging for compliance trail | Already in stack |
| **httpx** | 0.28.x | Async HTTP (Places real client, Apify real client) | Already in stack |
| **tenacity** | 9.1.x | Retry/backoff for Places/Apify/Twilio clients | Already in stack |

**The only new pip dependency for Phase 3 is `langgraph-checkpoint-postgres` 3.1.0.** All other packages are already declared in pyproject.toml.

### Test/Dev additions

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **fakeredis** | 2.36.x | Redis ramp counter tests without a broker | Already in stack |
| **respx** | 0.23.x | Mock httpx calls (Places, Apify real client paths) | Already in stack |

**Installation (Phase 3 incremental):**
```bash
pip install "langgraph-checkpoint-postgres==3.1.*"
```

**Version verification (conducted during research):** [VERIFIED: PyPI registry]
- `langgraph` 1.2.5 — current on PyPI
- `langgraph-checkpoint-postgres` 3.1.0 — current on PyPI
- `twilio` 9.10.9 — current on PyPI
- `google-maps-places` 0.9.0 — current on PyPI
- `apify-client` 3.0.2 — current on PyPI
- `anthropic` 0.109.1 — current on PyPI

---

## Package Legitimacy Audit

> slopcheck was unavailable at research time. All packages below are tagged [ASSUMED] per protocol. Planner must gate each new install behind a checkpoint:human-verify before first use.

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| langgraph-checkpoint-postgres | PyPI | ~2 yrs (from langchain-ai) | High (part of langchain ecosystem) | github.com/langchain-ai/langgraph | [ASSUMED] | Flagged — planner must add checkpoint:human-verify |
| langgraph | PyPI | 3+ yrs | Very high | github.com/langchain-ai/langgraph | [ASSUMED] | Flagged — planner must add checkpoint:human-verify (already in stack, confirm on reinstall) |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none identified via secondary signals (both packages are from the official `langchain-ai` GitHub org, match the expected ecosystem)

*slopcheck was unavailable at research time. All packages above are tagged [ASSUMED] and the planner must gate each install behind a checkpoint:human-verify task.*

**Secondary verification (non-slopcheck):**
- `langgraph-checkpoint-postgres` 3.1.0: published on pypi.org under `langchain-ai` maintainer; source at `github.com/langchain-ai/langgraph` confirmed in multiple official docs. [ASSUMED — registry existence alone does not confer VERIFIED status]

---

## Architecture Patterns

### System Architecture Diagram

```
Human steward (dashboard - Phase 4)
        |
        | PATCH /api/v1/atrativos/gate/{rio_id}/approve
        v
FastAPI gate router ──────────────────────────────────────────────
        |                                                          |
        | advance sub_state: aguardando → whatsapp_in_progress    |
        | dispatch outreach_task.delay(rio_id)                     |
        v                                                          |
Celery broker (Redis) ◄── beat fan-out by UF (sweep_atrativos)   |
        |                                                          |
        |──[discover_atrativo_task]──► DiscoveryAgent             |
        |        |                         |                       |
        |        |        PlacesClientProtocol.text_search         |
        |        |        DeepSeek/instructor (AtrativoResult)      |
        |        |        Mar query (parent destino resolution)     |
        |        |        store_raw → sub_state=discovered          |
        |        |                                                  |
        |──[find_contacts_task]──► ContactFinderAgent              |
        |        |                    PlacesClientProtocol.details  |
        |        |                    sub_state=contacts_found      |
        |        |                                                  |
        |──[gather_signals_task]──► SignalAgent                    |
        |        |                    business_status → descarte?   |
        |        |                    ApifyClientProtocol.scrape_ig |
        |        |                    sub_state=signals_gathered    |
        |        |                    ──► process_nascente_record   |
        |        |                           │ score <85%?          |
        |        |                     sub_state=aguardando_consulta_whatsapp (WAIT)
        |        |                                                  |
        |──[outreach_task (after human approve)]──►                |
        |                                                          |
        |    COMPLIANCE GATE (runs before every send)             |
        |    ┌──────────────────────────────────────┐             |
        |    │ legal_basis_recorded?                 │             |
        |    │ norteia_identified_in_message?        │             |
        |    │ not opted_out? (consent_log check)    │             |
        |    │ approved_template_used?               │             |
        |    │ 24h_window_respected?                 │             |
        |    │ human_gate_approved?                  │             |
        |    │ ramp_not_exceeded? (Redis INCR)       │             |
        |    │ quality_rating_not_red? (Redis flag)  │             |
        |    └──────────────────────────────────────┘             |
        |         │ any check fails → raise ComplianceError        |
        |         │ all pass → WhatsAppClientProtocol.send_template|
        |         v                                                 |
        |    LangGraph WhatsAppAgent (PostgresSaver checkpoint)    |
        |    ┌─────────────────────────────────────────┐          |
        |    │ thread_id = f"atrativo:{rio_id}"         │          |
        |    │                                          │          |
        |    │ START──► send_opening_template           │          |
        |    │              │ (Sonnet generates PT-BR)  │          |
        |    │              │ Twilio sends template     │          |
        |    │ ◄────────────┘  (n8n relays inbound)    │          |
        |    │ RECV_REPLY──► extract_answers            │          |
        |    │              │ (DeepSeek/instructor)     │          |
        |    │              │ 2nd-layer Pydantic valid  │          |
        |    │              │ check 24h window          │          |
        |    │              │ check opt-out keywords    │          |
        |    │          ┌──► ask_followup (Sonnet)      │          |
        |    │          │   or END (answers complete)   │          |
        |    └──────────────────────────────────────────┘          |
        |                    │                                      |
        |              ConversationExtractionResult                |
        |              ──► raise validacao_humana_value            |
        |              ──► reprocess_record → route_by_score       |
        |                     │ ≥85% → promote_to_mar             |
        |                     │        push_attraction_task        |
        |                     └ <85% → DLQ                        |
```

### Recommended Project Structure (Phase 3 additions)

```
brave/
├── lanes/atrativos/              # NEW — mirrors brave/lanes/destinos/ structure
│   ├── __init__.py
│   ├── schemas.py               #   AtrativoResult, ContactResult, SignalResult
│   │                            #   ConversationExtractionResult (Pydantic v2)
│   ├── discovery_agent.py       #   DiscoveryAgent — Places sweep + parent resolution
│   ├── contact_finder_agent.py  #   ContactFinderAgent — Places Details + site/IG
│   ├── signal_agent.py          #   SignalAgent — business_status/reviews/Apify
│   ├── whatsapp_agent.py        #   LangGraph graph (Sonnet ask + DeepSeek extract)
│   └── state_machine.py         #   advance_sub_state() dispatcher + transition guards
│
├── clients/                     # Phase 3 fills real impls for Phase 1 stubs
│   ├── places.py                #   RealPlacesClient (google-maps-places 0.9.x)
│   ├── apify.py                 #   RealApifyClient (apify-client 3.0.x)
│   ├── whatsapp.py              #   TwilioWhatsAppClient (twilio 9.10.x)
│   └── null_whatsapp.py         #   NullWhatsAppClient (records sends, never transmits)
│
├── compliance/                  # NEW — compliance gate and consent log
│   ├── __init__.py
│   ├── gate.py                  #   send_path_gate(rio_record, contact, template, params)
│   │                            #   raises ComplianceError on any failed check
│   ├── consent_log.py           #   write_consent_record(), is_opted_out()
│   └── quality_rating.py        #   is_quality_red(redis_client) + set_quality_flag()
│
├── api/routers/
│   └── atrativos_gate.py        #   NEW — WhatsApp gate endpoints (mirrors dlq.py)
│                                #   GET /api/v1/atrativos/gate (list aguardando queue)
│                                #   PATCH /api/v1/atrativos/gate/{rio_id}/approve
│                                #   PATCH /api/v1/atrativos/gate/{rio_id}/reject
│                                #   POST /api/v1/atrativos/gate/quality-rating-webhook
│
├── tasks/
│   └── pipeline.py              #   ADD: discover_atrativo_task, find_contacts_task,
│                                #   gather_signals_task, outreach_task, push_attraction_task
│
├── config/
│   └── settings.py              #   EXTEND: WhatsAppConfig, RampConfig
│
└── core/models.py               #   ADD: ConsentLog model (Alembic migration required)

tests/
├── fakes/
│   ├── fake_apify.py            #   NEW — FakeApifyClient
│   ├── fake_whatsapp.py         #   NEW — FakeWhatsAppClient (records calls, returns fixture)
│   └── fake_places.py           #   EXTEND — add fixture shapes for business_status fields
└── unit/
    └── compliance/
        └── test_gate.py         #   NEW — one test per D-11 gate condition (8 tests min)
```

### Pattern 1: Celery Idempotent Sub-State FSM

**What:** Each FSM transition is a separate Celery task. The task reads `sub_state`, asserts it matches the expected input state (optimistic check), does its work, and advances `sub_state` in one write. Replay is safe because the task short-circuits if `sub_state` is already past the expected input.

**When to use:** All atrativo lifecycle transitions (D-01/D-02). This is the core durability mechanism — `sub_state` in Postgres survives worker restarts; Celery's `acks_late=True` + `reject_on_worker_lost=True` ensures at-least-once delivery.

**State transition guard pattern:**
```python
# Source: Architecture pattern — Celery + sub_state FSM (D-01/D-02)
def find_contacts_task_body(session: Session, rio: RioRecord, config: ScoreConfig) -> None:
    # Idempotency: short-circuit if already past this step
    if rio.sub_state != "discovered":
        return  # already advanced — safe replay

    # SELECT FOR UPDATE: prevents two workers racing on the same record
    # (re-fetch with lock inside the task — the canonical_key index covers this)
    contacts = run_contact_finder(rio, places_client=...)
    
    from sqlalchemy.orm.attributes import flag_modified
    normalized = dict(rio.normalized or {})
    normalized["contacts"] = contacts
    rio.normalized = normalized
    flag_modified(rio, "normalized")
    rio.sub_state = "contacts_found"
    
    write_audit(session, action="sub_state_advanced",
                entity_type="attraction", record_id=rio.id,
                before_state={"sub_state": "discovered"},
                after_state={"sub_state": "contacts_found"},
                actor="contact_finder_agent")
    session.flush()
```

### Pattern 2: LangGraph WhatsApp Conversation with PostgresSaver

**What:** The WhatsAppAgent is a LangGraph `StateGraph` using `AsyncPostgresSaver` from `langgraph-checkpoint-postgres`. The graph runs inside the Celery `outreach_task`, using the same Postgres connection string as the rest of the collector. `thread_id = f"atrativo:{rio_id}"` maps each conversation to its RioRecord. The graph is instantiated fresh each time, but resumes from checkpoint automatically.

**Why PostgresSaver (not InMemorySaver):** The conversation spans days (owner may not reply for 24h+). An in-memory checkpointer loses state on worker restart. The conversation must resume exactly where it left off — that requires a durable external store.

**Key implementation details:** [CITED: fast.io/resources/langgraph-persistence/ + pypi.org/project/langgraph-checkpoint-postgres]
- `langgraph-checkpoint-postgres` 3.1.0 uses `psycopg` 3 (already in stack — compatible)
- `AsyncPostgresSaver.setup()` must be called once at startup (in an Alembic Wave 0 step or the FastAPI lifespan) to create `checkpoints` and `checkpoint_blobs` tables
- Use `AsyncPostgresSaver` (not `PostgresSaver`) inside FastAPI/async Celery contexts; `PostgresSaver` blocks the event loop
- `autocommit=True` and `row_factory=dict_row` required on the connection passed to the checkpointer

**Graph node layout:**
```python
# Source: LangGraph 1.x pattern + Anthropic SDK + instructor (D-08)
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

class ConversationState(TypedDict):
    messages: list[dict]         # full turn history
    extraction: dict | None      # ConversationExtractionResult
    opted_out: bool
    window_open: bool            # 24h window state
    turns: int

graph_builder = StateGraph(ConversationState)
graph_builder.add_node("send_opening", send_opening_node)   # Sonnet generates + Twilio sends
graph_builder.add_node("recv_reply", recv_reply_node)       # called on inbound webhook
graph_builder.add_node("extract_answers", extract_node)     # DeepSeek/instructor extraction
graph_builder.add_node("ask_followup", followup_node)       # Sonnet asks missing fields
graph_builder.add_node("finalize", finalize_node)           # update RioRecord + re-score

# thread_id pattern
config = {"configurable": {"thread_id": f"atrativo:{rio_id}"}}
```

### Pattern 3: LGPD/BSP Compliance Gate

**What:** A single synchronous function `send_path_gate(session, redis_client, rio_record, contact, template_name, params)` that raises `ComplianceError` if any of the 8 conditions in D-11 fails. Called immediately before every `WhatsAppClientProtocol.send_template`. The function is pure code (no LLM, no network) and is fully unit-testable offline.

**Gate conditions and how to implement them:**

| Condition | Check | Data Source |
|-----------|-------|-------------|
| Legal basis recorded | `consent_log` has a row for this contact | Postgres |
| Norteia identification in message | `"Norteia"` in `params["body"]` (or template verified) | In-memory string check |
| Opt-out not set | `consent_log.opted_out IS False` for this contact | Postgres |
| Approved BSP template | `template_name` in `settings.whatsapp_approved_templates` | Config |
| 24h window respected | template-type check: if `window_open=False`, only utility/auth templates | In-memory |
| Human gate approved | `rio.sub_state == "whatsapp_in_progress"` | RioRecord |
| Ramp not exceeded | Redis `INCR` + check vs. daily cap | Redis atomic |
| Quality not Red | Redis flag `wa:quality_red` is not set | Redis |

**Ramp counter (CR-04 hardening applied):**
```python
# Source: CR-04 lesson — atomic reserve-before-call (D-07)
def check_and_increment_ramp(redis_client, cap: int, uf: str | None = None) -> None:
    """Atomic reserve-before-call: INCR then check. TTL=UTC day boundary."""
    from datetime import datetime, timezone
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"wa:ramp:{date_key}" if uf is None else f"wa:ramp:{uf}:{date_key}"
    
    # Atomic INCR — if this task crashes after INCR but before send,
    # the counter is slightly over-counted (conservative; safe)
    count = redis_client.incr(key)
    # Set TTL on first call only (prevents permanent accumulation on crash)
    if count == 1:
        redis_client.expireat(key, _next_utc_midnight())
    if count > cap:
        redis_client.decr(key)  # undo the reserve
        raise ComplianceError(f"Ramp cap {cap} exceeded for {date_key}")
```

### Pattern 4: Inbound WhatsApp Reply Routing (n8n thin transport)

**What:** n8n's WhatsApp Cloud node (or Twilio webhook) relays inbound messages to a FastAPI endpoint `POST /api/v1/atrativos/whatsapp/inbound`. FastAPI looks up the conversation by `from_number` → `consent_log` → `RioRecord` (via contact phone), then resumes the LangGraph graph by dispatching `resume_conversation_task.delay(rio_id, message_text)`. All logic (opt-out keyword detection, answer extraction) lives in LangGraph nodes — n8n touches nothing.

**Opt-out keyword handling (COMP-01/02):**
SAIR, PARAR, CANCELAR, REMOVER, STOP, NÃO detected in the `recv_reply` node → write `consent_log.opted_out = True` + `opted_out_at = now()` → route graph to END → advance `rio.sub_state` to `dlq` with `dlq_reason="owner_opted_out"`. [CITED: wuseller.com/blog/the-only-whatsapp-opt-out-system-you-need]

### Anti-Patterns to Avoid

- **Wrapping the whole multi-day FSM in one LangGraph run:** LangGraph checkpoints persist between nodes within a single run, but the multi-day "wait for human gate → wait for reply → wait for next reply" lifecycle should be driven by Celery tasks (each triggering a bounded LangGraph invocation), not by a single indefinitely-suspended LangGraph run. [CITED: ARCHITECTURE.md Anti-Pattern 4]
- **Calling send_template before the compliance gate function:** the gate is the single enforcement point — bypassing it by calling the client directly is an LGPD/BSP violation that has no code-time protection.
- **Putting opt-out keyword detection in n8n:** n8n is un-unit-testable; all keyword logic lives in the LangGraph `recv_reply` node.
- **Using IVFFlat for the consent_log lookup index:** consent_log uses a standard B-tree on `phone_e164` — no vector index needed here.
- **Issuing CONCURRENTLY index creation in an Alembic migration transaction:** same lesson as Phase 2 D-08 — run `CREATE INDEX CONCURRENTLY` outside Alembic's transaction block with `op.execute(..., execution_options={"autocommit": True})` or accept a blocking index creation.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| LangGraph conversation state persistence across worker restarts | Custom JSON serialization + Postgres table | `langgraph-checkpoint-postgres` 3.1.x `AsyncPostgresSaver` | Handles serialization, thread isolation, concurrent access, and node-level checkpoint granularity correctly |
| WhatsApp template sending + delivery status | Raw httpx calls to Twilio REST API | `twilio` 9.10.x SDK `MessagingServiceSid` path | SDK handles auth, retry, error normalization, and delivery webhook parsing |
| Structured extraction from PT-BR WhatsApp replies | Custom regex parser | `instructor` + Mode.Tools + `ConversationExtractionResult` Pydantic model | DeepSeek schema adherence is weak without instructor's retry-with-validation-errors loop |
| 24h window calculation | Custom timezone math | Record `last_inbound_at` in LangGraph state + compare to `now()` with `datetime.timezone.utc` | The window is defined as 24h after last inbound message — simple arithmetic, but must be UTC-consistent and tested |
| Opt-out keyword matching for PT-BR | NLP model or fuzzy match | Exact match against known keyword set (SAIR, PARAR, CANCELAR, REMOVER, STOP, NÃO) in `recv_reply` node | Meta's system handles canonical opt-out; the app must honor any of the PT-BR standard keywords |
| Ramp counter atomicity | Redis GET + compare + SET | Redis `INCR` + `EXPIREAT` (atomic, crash-safe) | Non-atomic GET/SET allows overshoot under concurrent workers — exact same failure as CR-04 cost guard |

**Key insight:** The compliance gate is zero-dependency code. Every check in it must be implementable without a real network call. Tests should directly call `send_path_gate(...)` with fixture data and assert that each failure mode raises `ComplianceError` with the right message.

---

## WhatsApp BSP: Verified Current State (Phase 3 Research Flag)

> This section satisfies the STATE.md Phase 3 research flag: "re-verify Twilio-vs-Meta-Cloud BSP pricing/policy/template categorization/rate caps at build time."

### Pricing (as of July 2025 — per-message billing, not conversation-based)

[CITED: engagelab.com/blog/whatsapp-business-api-pricing + chatarmin.com/en/blog/twilio-whats-app-api]

| Category | Brazil rate (Meta fee) | Twilio markup | Inside 24h utility window |
|----------|----------------------|---------------|--------------------------|
| Marketing | ~$0.0625/msg | +$0.005 | Always charged |
| Utility | ~$0.004/msg | +$0.005 | **Free inside 24h window** |
| Authentication | ~$0.0135/msg | +$0.005 | Charged |

**Implication for template design:** The Norteia outreach template must be classified as **Utility** (transaction/verification-related, not promotional). A single sentence of promotional content causes Meta to reclassify it as Marketing — costing 15x more and requiring more scrutiny. Write the template as "we are reaching out to verify information about your business" (utility, not marketing). Never include offers or CTAs.

**Twilio vs Meta Cloud at launch:**
- Twilio: +$0.005/msg markup, but managed infra, webhook hosting, faster first-message
- Meta Cloud Direct: cheaper at scale but requires own webhook server, own rate-tier management, own compliance engineering
- Decision D-09 is confirmed correct: **launch on Twilio**, migrate behind `WhatsAppClientProtocol` when volume warrants.

### Messaging Limits (post October 2025 — portfolio-wide)

[CITED: uptail.ai/blog/how-many-messages-can-you-send-on-whatsapp-business-limits-explained-for-2026 + Meta for Developers docs]

| Tier | Portfolio daily limit | Progression |
|------|----------------------|-------------|
| Unverified (new) | 250 unique contacts/24h | Default for new portfolios |
| Tier 1 | 1,000 | Verification + Green quality |
| Tier 2 | 10,000 | Consistent usage at Tier 1 |
| Tier 3 | 100,000 | Consistent usage at Tier 2 |
| Unlimited | No cap | Sustained quality at Tier 3 |

**Critical change (October 2025):** Limits are now **portfolio-wide** (shared across all phone numbers in the Meta Business Portfolio), not per-number. Adding a second number does NOT double capacity. [CITED: uptail.ai + pickyassist.com/blog]

**Tier evaluation:** Meta evaluates every 6 hours (down from 24h pre-2026). Quality rating now only prevents tier upgrades — a Yellow rating freezes progression; a Red rating can reduce the limit. Tier downgrades from quality degradation are less automatic post-2025 but Red+sustained can still trigger suspension.

**Ramp design implication:** Start the ramp cap at **50-100/day** (well under the 250 cold-start portfolio limit), with the cap configurable in `pydantic-settings` (`BRAVE_WA_RAMP_DAILY_CAP`). A per-UF cap is an optional refinement. Auto-pause on Red quality rating is mandatory (D-11).

### Template Requirements

[CITED: twilio.com/docs/whatsapp/key-concepts]

- Three categories: **Authentication**, **Utility**, **Marketing** (Meta-defined)
- Template body text is reviewed and can be paused/rejected at any time by Meta
- Mixed content (e.g., verification message + promotional sentence) → classified as Marketing
- Template must include opt-out instructions (e.g., "Responda SAIR para não receber mais mensagens")
- Template name must be registered in Twilio console before use; `template_name` in config must match exactly

### Quality Rating Auto-Pause

[CITED: pickyassist.com/blog/whatsapps-messaging-limits-quality-ratings-on-2025 + turn.io]

| Rating | Effect | Action |
|--------|--------|--------|
| Green | Normal operation | Continue |
| Yellow | Tier progression frozen | Auto-throttle (reduce ramp cap to 50%) |
| Red | Possible limit reduction / suspension risk | Auto-pause (set `wa:quality_red` flag in Redis; gate blocks all sends) |

**Implementation:** A `POST /api/v1/atrativos/whatsapp/quality-rating-webhook` endpoint receives quality rating change events (from Twilio callbacks or Meta Platform webhooks) and sets/clears a Redis flag `wa:quality_red`. The compliance gate checks this flag. A separate scheduled probe (celery-redbeat task) can also fetch quality rating via the Twilio API and update the flag.

---

## LangGraph Durable Conversation: Design Pass

> This section satisfies the STATE.md Phase 3 research flag: "design the durable-FSM + multi-day LangGraph conversation."

### Celery-as-Macro-FSM + LangGraph-as-Conversation-Brain (confirmed hybrid pattern)

[CITED: ARCHITECTURE.md Pattern 3 + dasroot.net/posts/2026/02/orchestrating-ai-tasks-celery-temporal]

The two concerns are separable:

1. **Celery drives the macro FSM** (`sub_state` transitions: discovered → contacts_found → signals_gathered → aguardando → whatsapp_in_progress). Each transition is one Celery task. The `sub_state` column in Postgres is the durable record. A Celery worker restart is a no-op because the task re-reads `sub_state` and short-circuits if already advanced.

2. **LangGraph drives the micro FSM** (conversation turns within `whatsapp_in_progress`). The `AsyncPostgresSaver` persists the LangGraph checkpoint (graph state + turn history) in Postgres. A Celery worker restart resumes the conversation from the last checkpoint using the same `thread_id`.

### Conversation Lifecycle (whatsapp_in_progress)

```
outreach_task fired (triggered by gate approval)
  → compliance gate passes
  → WhatsAppAgent graph initialized with thread_id = f"atrativo:{rio_id}"
  → AsyncPostgresSaver: no checkpoint exists → START node
  → send_opening_template: Sonnet generates body → Twilio sends template
  → checkpoint saved at: {"messages": [opening], "extraction": None, "turns": 1}
  
  [WAIT — hours or days for owner reply]
  
inbound webhook fires (owner replies)
  → POST /api/v1/atrativos/whatsapp/inbound
  → resume_conversation_task.delay(rio_id, reply_text)
  
resume_conversation_task fired
  → compliance gate: is still whatsapp_in_progress? not opted_out? window_open?
  → WhatsAppAgent graph loaded with thread_id → AsyncPostgresSaver: checkpoint found → RECV_REPLY node
  → recv_reply: detect opt-out keywords? (if yes → END + DLQ)
  → extract_answers: DeepSeek/instructor → ConversationExtractionResult
  → all answers present? → finalize → re-score → Mar/DLQ
  → missing answers? → ask_followup → Sonnet → Twilio → checkpoint saved
  
  [WAIT again for follow-up reply]
  
  [MAX_TURNS exceeded or timeout] → END with partial extraction → DLQ
```

### Graph State Schema

```python
# Source: LangGraph 1.x StateGraph pattern (D-08)
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph

class ConversationState(TypedDict):
    rio_id: str                            # immutable — links to RioRecord
    messages: list[dict]                   # full turn history [{role, content}]
    extraction: dict | None                # ConversationExtractionResult dict
    opted_out: bool
    window_open: bool                      # True if within 24h of last inbound
    last_inbound_at: str | None            # ISO UTC timestamp
    turns: int                             # guard against infinite loops
    max_turns: int                         # from config (default 3)
    outreach_template: str                 # template name used for opening
```

### ConversationExtractionResult (Pydantic schema for DeepSeek extraction)

```python
# Source: instructor Mode.Tools + Pydantic v2 pattern (Phase 1 D-09)
from pydantic import BaseModel, Field
from typing import Literal

class ConversationExtractionResult(BaseModel):
    existe: Literal["sim", "nao"] | None = Field(
        None, description="O negócio/atrativo existe? ('sim'/'nao')"
    )
    funcionando: Literal["sim", "nao", "temporariamente_fechado"] | None = Field(
        None, description="Está funcionando atualmente?"
    )
    horarios: str | None = Field(
        None, description="Horários de funcionamento (texto livre)"
    )
    valor: str | None = Field(
        None, description="Valor de entrada ou faixa de preço (texto livre, None se gratuito)"
    )
    confidence: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Confiança geral da extração (0-1)"
    )
```

### Checkpointer Setup

```python
# Source: langgraph-checkpoint-postgres 3.1.x docs (verified on PyPI)
# Called once at application startup (in FastAPI lifespan or a Wave 0 migration step)
import asyncio
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def setup_checkpointer(db_url: str) -> AsyncPostgresSaver:
    # db_url = postgresql+psycopg://... (existing BRAVE_DB_URL format is compatible)
    saver = await AsyncPostgresSaver.from_conn_string(
        db_url.replace("postgresql+psycopg://", "postgresql://")
        # langgraph-checkpoint-postgres uses psycopg3 directly, not SQLAlchemy prefix
    )
    await saver.setup()  # Creates checkpoints + checkpoint_blobs tables
    return saver
```

**Note on db_url format:** `langgraph-checkpoint-postgres` expects a standard `postgresql://` DSN, not SQLAlchemy's `postgresql+psycopg://` prefix. Strip the SQLAlchemy driver prefix before passing to the checkpointer. The underlying driver is still psycopg 3. [ASSUMED — verify against 3.1.x docs at build time]

---

## Common Pitfalls

### Pitfall 1: Celery task races on sub_state transitions

**What goes wrong:** Two workers both pick up `discover_atrativo_task` for the same RioRecord (after a visibility timeout). Both read `sub_state == "discovered"`, both proceed, one advances to `contacts_found`, the other overwrites with stale data.

**Why it happens:** Redis at-least-once + long task duration (Places API can be slow) = occasional double delivery within visibility timeout.

**How to avoid:** Use `SELECT ... FOR UPDATE SKIP LOCKED` at the start of each transition task to acquire a row-level lock. If the lock cannot be acquired (another worker holds it), skip (return immediately). The Celery retry will pick it up later. This is the same pattern as Phase 1 dedup.

**Warning signs:** Two `contacts_found` audit rows for the same `rio_id`; `sub_state` oscillating in logs.

### Pitfall 2: LangGraph thread_id collision or loss

**What goes wrong:** Two different conversations share the same `thread_id`, or a conversation's checkpoint is stored under a key that doesn't survive the RioRecord's lifecycle (e.g., keyed by phone number which can change).

**How to avoid:** Key by `rio_id` (UUID, immutable, unique): `thread_id = f"atrativo:{rio_id}"`. Never use phone number or contact email as thread_id.

**Warning signs:** A new conversation picks up history from a previous one; re-sent opening message appears mid-conversation.

### Pitfall 3: Compliance gate bypass via direct client call

**What goes wrong:** A developer adds a code path that calls `WhatsAppClientProtocol.send_template` directly (e.g., in a test, in a helper function) without going through `send_path_gate`. This creates an untested, unchecked path.

**How to avoid:** Enforce the gate architecturally: the `WhatsAppAgent` graph's `send_opening` and `ask_followup` nodes call a single `_compliant_send(session, redis, rio, template, params)` function that always invokes the gate. Never call `send_template` elsewhere. Add a test that proves calling `send_template` directly is the only unchecked path (and that path only exists in `fake_whatsapp.py`).

**Warning signs:** A `send_template` call appears outside `brave/lanes/atrativos/whatsapp_agent.py` or `brave/compliance/gate.py`.

### Pitfall 4: Portfolio-level ramp miscount (post-Oct 2025 portfolio-sharing)

**What goes wrong:** The ramp counter is implemented per-phone-number or per-UF, but WhatsApp's portfolio limit applies globally across all numbers. If we run two phone numbers from the same portfolio, our per-number counters each allow 250, but the portfolio total is still 250.

**How to avoid:** The ramp counter's primary scope is **global portfolio daily** (`wa:ramp:{date}`). A per-UF split can be an optional additional cap layered on top, but the global cap must always be checked first.

**Warning signs:** Two UFs running simultaneously each approach their per-UF cap, but the portfolio quality starts degrading at lower volumes.

### Pitfall 5: AsyncPostgresSaver and sync Celery worker event loop conflict

**What goes wrong:** `AsyncPostgresSaver` requires an async context. A synchronous Celery worker (the standard pattern in this project) cannot `await` the checkpointer without a running event loop.

**How to avoid:** Use `asyncio.run(...)` inside the Celery task body (same pattern as `push_mar` uses `asyncio.run(_push())`). Each task invocation creates and tears down its own event loop. This is compatible with `langgraph-checkpoint-postgres` because the checkpointer is stateless (connection created per invocation from a URL).

```python
# Source: pattern from brave/tasks/pipeline.py push_mar (existing codebase)
@shared_task(bind=True, ...)
def outreach_task(self, rio_id: str) -> None:
    async def _run():
        saver = await AsyncPostgresSaver.from_conn_string(...)
        await saver.setup()
        graph = whatsapp_agent_graph.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": f"atrativo:{rio_id}"}}
        await graph.ainvoke({"rio_id": rio_id, ...}, config=config)
    asyncio.run(_run())
```

**Warning signs:** `RuntimeError: no running event loop` inside the Celery task; or `SyncError: cannot use async checkpointer in sync context`.

### Pitfall 6: Template classification drift (utility vs. marketing)

**What goes wrong:** The outreach template passes Meta's initial review as "Utility", but after an edit to add a CTA or a discount mention, Meta silently reclassifies it as "Marketing" — costing 15x more per message and potentially triggering a template pause. The pipeline doesn't detect the reclassification.

**How to avoid:** Template content must be verifiably utility-only: state purpose, identify sender (Norteia), ask factual questions about the business, offer opt-out. No promotional content, no CTAs, no discount mentions. Before any template edit, re-submit for review. The `settings.whatsapp_approved_templates` config list serves as the allowlist; an unregistered template name is a ComplianceError.

**Warning signs:** Unexpected per-message cost increases; Twilio console shows template status "REJECTED" or "PAUSED".

### Pitfall 7: SQLAlchemy flag_modified omission on JSON consent_log mutation

**What goes wrong:** In-place mutation of a JSON column (e.g., `consent_record.metadata["opted_out"] = True`) is not tracked by SQLAlchemy's change detection. The UPDATE is silently skipped.

**How to avoid:** Always reassign the JSON column AND call `flag_modified(obj, "field_name")` after any JSON mutation. This is the same lesson as Phase 2 DLQ validate endpoint (T-02-06-04). Apply it to every consent_log write.

**Warning signs:** `opted_out = True` in code but the DB row still shows `False`; opt-out not suppressing subsequent sends.

---

## Consent / Opt-Out Log Design (COMP-01)

### consent_log Table DDL (Alembic migration required)

```python
# Source: Phase 3 research — LGPD consent log design (D-11 / COMP-01)
class ConsentLog(Base):
    """LGPD consent and opt-out log per contact.
    
    Separate table from audit_log because it serves a different query pattern:
    audit_log = historical trail (append-only reads)
    consent_log = real-time suppression lookup (is_opted_out check before every send)
    
    Indexed on phone_e164 for fast suppression lookups.
    """
    __tablename__ = "consent_log"

    id: UUID (PK)
    phone_e164: str (NOT NULL, indexed)         # E.164 format (+55...)
    rio_id: UUID (FK → rio_records.id)          # which atrativo contact
    legal_basis: str (NOT NULL)                 # "legitimate_interest_commercial_verification"
    norteia_identified: bool (NOT NULL)         # was Norteia identified in outreach?
    opted_out: bool (NOT NULL, default=False)
    opted_out_at: datetime | None
    opted_out_keyword: str | None               # e.g. "SAIR"
    first_contact_at: datetime (NOT NULL)
    last_contact_at: datetime (NOT NULL)
    purpose: str (NOT NULL)                     # "business_validation"
    created_at: datetime (server_default=now())
```

### LGPD Legal Basis

[CITED: messagecentral.com/blog/lgpd-whatsapp-business + PLANO-BRAVE.md §B.8]

For automated outreach to business owners for verification purposes, the applicable LGPD legal basis is **"legítimo interesse"** (Art. 7, VI, LGPD) for the specific purpose of commercial-territorial-data verification — not consent (which requires prior explicit opt-in). This is a defensible basis for B2B outreach where:
1. The purpose is clearly stated (data verification, not marketing)
2. Norteia is identified in the opening message
3. Opt-out is offered immediately and honored immediately
4. The data is limited to business contact information (not personal residential data)

**This is [ASSUMED]** — the exact legal-basis selection for this specific use case requires legal review at build time. The consent_log table should be designed to record whichever basis is confirmed. Do not change the data model based on legal-basis selection; only the recorded `legal_basis` string changes.

---

## Score Input Mapping for Atrativos (ATR-01 / D-05)

### §7.6 Criterion Values from Agent Pipeline

| §7.6 Criterion | Source | Value | Notes |
|----------------|--------|-------|-------|
| `origem_value` | `source="places_discovery"` | 60 | Google Places is authoritative but not official gov data |
| `completude_value` | Fields populated in ContactResult | 0–100 | % of required fields: name, coords, phone/WA, hours, type |
| `corroboracao_value` | Cross-source hits | 0 (single source) → 40 (Apify confirms) → 60 (OTA confirms) | Additive; Places alone = 0 |
| `atualidade_value` | `reviews[].publishTime ≤ 30 days` | 100 if recent review; 50 if 1–6mo; 0 if no reviews | SignalAgent deterministic mapping |
| `validacao_humana_value` | 0 (initial) → 100 (owner-confirmed via WhatsApp) | Owner says `existe=sim, funcionando=sim` → 100 | boost by owner-validation outreach |

**CLOSED_* business_status → hard descarte** (before scoring): if `business_status` is `CLOSED_PERMANENTLY` or `CLOSED_TEMPORARILY`, set `rio.routing = "descarte"` and `rio.sub_state = None` immediately. Never run §7.6 on a closed place.

**Cold-start score estimate:** A newly discovered atrativo with Places only, no reviews, no contacts:
- origem: 60×0.30 = 18
- completude: ~50×0.20 = 10 (name+coords only)
- corroboração: 0×0.20 = 0
- atualidade: 0×0.15 = 0
- validação humana: 0×0.15 = 0
- **Total: ~28 → descarte band**

This means most raw Places discoveries will land in **descarte** unless ContactFinder and SignalAgent fill completude + corroboração sufficiently. The atual threshold (40.0 after Phase 2 calibration) means: with completude=70 + corroboração=20, score ≈ 18+14+4 = 36, still descarte. Need atualidade or validação signals to reach DLQ. This is expected — the WhatsApp gate is designed for borderline records that are already enriched.

**Implication:** `origem_value` for Places may need calibration upward (from 60 to 70-80) to ensure enriched atrativos land in DLQ for human gate, not descarte. Treat `origem_value` for `source="places_discovery"` as a tunable setting in `pydantic-settings`. [ASSUMED — calibrate on first BA state run]

---

## AtrativoResult Schema (ATR-02 / D-04)

The instructor-validated schema for DiscoveryAgent's DeepSeek extraction:

```python
# Source: Pattern from brave/lanes/destinos/schemas.py + PLANO-BRAVE.md §B.4
from pydantic import BaseModel, Field
from typing import Literal

class AtrativoResult(BaseModel):
    """One atrativo extracted by DiscoveryAgent from a Places result.
    
    Maps via DeepSeek/instructor to the Nascente payload.
    Persists only place_id as cache (D-04 / COMP-03).
    """
    nome: str = Field(..., min_length=2, description="Nome turístico do atrativo")
    tipo: Literal[
        "praia", "parque", "museu", "cachoeira", "trilha", "mirante",
        "centro_historico", "experiencia_gastronomica", "show_cultural",
        "esporte_aventura", "outros"
    ]
    posicionamento: str = Field(..., min_length=10)
    municipio_nome: str
    municipio_ibge: str = Field(..., pattern=r"^\d{7}$")
    uf: str = Field(..., min_length=2, max_length=2)
    place_id: str = Field(..., description="Google place_id — only Google field persisted long-term")
    # Score criterion hints from Places metadata (set by DiscoveryAgent)
    origem_value: float = 60.0        # source=places_discovery default
    completude_value: float           # computed from field coverage
```

---

## Runtime State Inventory

This is not a rename/refactor phase. However, there are new runtime state elements being introduced:

| Category | Items Added | Action Required |
|----------|-------------|------------------|
| Stored data (DB) | `consent_log` table (new); `checkpoints` + `checkpoint_blobs` tables (AsyncPostgresSaver) | Alembic migration (Wave 0) + `saver.setup()` on startup |
| Stored data (Redis) | Ramp counters `wa:ramp:{date}`, quality flag `wa:quality_red` | Created at first use; no migration needed |
| Stored data (DB) | LangGraph checkpoint rows accumulate per conversation | Retention/TTL policy needed; checkpoints can be pruned after conversation completes |
| Live service config | Twilio WhatsApp template names must be pre-registered in Twilio console before the lane runs | Manual step — document as Wave 0 pre-condition |
| Secrets/env vars | `BRAVE_WA_TWILIO_ACCOUNT_SID`, `BRAVE_WA_TWILIO_AUTH_TOKEN`, `BRAVE_WA_FROM_NUMBER`, `BRAVE_WA_RAMP_DAILY_CAP`, `BRAVE_WA_QUALITY_PAUSE_THRESHOLD`, `BRAVE_WA_APPROVED_TEMPLATES` (list) | New settings in `WhatsAppConfig` |
| Build artifacts | No stale artifacts expected (new package addition only) | None |

**Nothing found in rename/migration categories** — this is a greenfield lane addition, not a refactor.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL (local) | Integration tests, LangGraph checkpointer | Not running at research time | — | Use docker-compose (already in repo) |
| Redis (local) | Celery broker, ramp counter tests | Not running at research time | — | Use fakeredis (already in stack) for unit tests; docker-compose for integration |
| Python 3.13 | Runtime | Available | 3.13.11 | Python 3.12 is the project floor; 3.13 works (psycopg/pgvector wheels confirmed) |
| `langgraph-checkpoint-postgres` 3.1.x | WhatsAppAgent LangGraph persistence | Not installed locally | 3.1.0 on PyPI | — |
| Twilio account / approved templates | Real WhatsApp sends | Not verified | — | Faked via FakeWhatsAppClient in default suite; real requires `BRAVE_RUN_REAL_EXTERNALS=true` |
| Google Places API key | Real discovery/signal | Not verified | — | FakePlacesClient in default suite |
| Apify API key | Real IG/X scraping | Not verified | — | FakeApifyClient in default suite |
| Anthropic API key | Sonnet conversation | Already wired in settings (stubbed since Phase 1) | — | FakeLLMClient for offline tests |

**Missing dependencies with no fallback:**
- None for the default offline suite — all externals are behind fakeable client interfaces.

**Missing dependencies with fallback:**
- PostgreSQL + Redis: docker-compose (already in repo) provides both for integration tests.
- All real client dependencies require `BRAVE_RUN_REAL_EXTERNALS=true` and are opt-in only.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Yes — gate endpoints + quality-rating webhook | `require_steward` dependency (X-Steward-Secret, hmac.compare_digest) — same as DLQ steward pattern |
| V3 Session Management | No | LangGraph uses thread_id (not HTTP sessions) |
| V4 Access Control | Yes — gate approve/reject are privileged mutations | `require_steward` on all mutating gate endpoints |
| V5 Input Validation | Yes — inbound WhatsApp text (opt-out keyword detection, reply parsing) | Pydantic schema + instructor validation on extraction; keyword exact-match (no eval/exec) |
| V6 Cryptography | No — no custom crypto | Twilio SDK handles transport TLS; HMAC for steward auth |
| V8 Data Protection (LGPD) | Yes — PII: phone/email/WA number/conversation | Consent log; data minimization; `data_collection: deny` on LLM; retention policy |

### Threat Model for Phase 3

| Threat | STRIDE | Standard Mitigation |
|--------|--------|---------------------|
| Unauthenticated gate approval | Elevation of Privilege | `require_steward` on PATCH /approve and /reject; fail-closed (empty secret = 401) |
| Opt-out bypass (send after SAIR) | Tampering | `is_opted_out()` check inside `send_path_gate` before every send; test: opted-out contact → ComplianceError |
| Unregistered template used | Tampering | `template_name in settings.whatsapp_approved_templates` check in gate |
| LGPD consent log tampering | Tampering | consent_log is append-only + audit-backed; no DELETE endpoint |
| PII (phone) sent to DeepSeek | Information Disclosure | DeepSeek extraction receives only the reply text (not phone number); `data_collection: deny` on every request |
| Quality-rating webhook spoofing | Spoofing | Authenticate quality-rating webhook with Twilio signature verification (Twilio SDK `validate_signature`) |
| Ramp counter race condition → overshoot | Tampering | Atomic Redis `INCR` (CR-04 pattern); overshoot = conservative (safe) |
| LangGraph checkpoint row injection via thread_id collision | Spoofing | thread_id scoped to `f"atrativo:{rio_id}"` (UUID); impossible to guess without DB access |
| PII persisted beyond retention period | Information Disclosure | Retention policy: purge `consent_log` + conversation checkpoint rows for descarte/closed records after N days (v2 task, document as technical debt) |

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Conversation-based WhatsApp billing | Per-message billing | July 2025 | Utility messages free inside 24h window; marketing always charged; ramp cost model changes |
| Per-number messaging limits | Portfolio-wide limits | October 2025 | Adding numbers doesn't scale capacity; one portfolio limit shared across all numbers |
| Tier evaluation every 24h | Every 6 hours | 2026 | Faster tier progression for quality accounts |
| Quality rating causes automatic downgrades | Downgrades less automatic (Yellow = freeze only) | Late 2025 | Red rating still dangerous (suspension risk); Yellow no longer automatically shrinks limits |
| LangGraph in-memory checkpointer | `langgraph-checkpoint-postgres` 3.x (AsyncPostgresSaver) | 2024→2025 | Production-grade persistence; thread_id isolation; no node-level state loss on restart |

**Deprecated/outdated:**
- `:online` OpenRouter variant: deprecated (as documented in PITFALLS.md Pitfall 12) — do not use
- `googlemaps` (legacy) client: targeting deprecated Places API — do not use (CLAUDE.md + STACK.md)
- `IVFFlat` pgvector index: deprecated for active-write workloads — HNSW already in use

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `origem_value = 60` for `source=places_discovery` is a reasonable default | Score Input Mapping | Places-discovered atrativos may land in descarte; calibrate on first BA state before setting national default |
| A2 | LGPD legal basis = "legítimo interesse" (Art. 7, VI) for B2B commercial verification outreach | Consent Log Design | Wrong legal basis = LGPD violation; requires legal review before first real send |
| A3 | `langgraph-checkpoint-postgres` 3.1.x DSN format strips `postgresql+psycopg://` prefix to `postgresql://` | LangGraph Pattern 2 | Connection error at runtime; verify against 3.1.x docs before finalizing task code |
| A4 | Ramp cap default of 50-100/day is appropriate for launch | BSP Verified State | Too low = slow ramp-up; too high = quality degradation; calibrate against portfolio verification status |
| A5 | FakeApifyClient (new) follows same structural pattern as FakePlacesClient | Project Structure | Minor — structural only; easily corrected |
| A6 | `AsyncPostgresSaver.from_conn_string()` is the correct 3.1.x constructor | LangGraph Pattern 2 | API may have changed; verify against official docs at build time |

**If this table is empty:** All claims were verified — this table has 6 entries requiring build-time confirmation.

---

## Open Questions

1. **`n8n` presence in production vs. direct Twilio webhook**
   - What we know: CONTEXT.md requires n8n as "thin transport"; STACK.md notes "seriously consider dropping n8n entirely and calling the BSP from a typed httpx client — simpler test story"
   - What's unclear: Does n8n actually add value beyond relay, or can the inbound webhook be a direct FastAPI endpoint receiving Twilio's webhook?
   - Recommendation: Plan both paths; prefer direct FastAPI webhook (fewer moving parts, simpler test boundary); keep n8n option if there's an existing n8n infra reason to use it. The compliance gate and LangGraph logic are identical either way.

2. **Consent log legal basis confirmation**
   - What we know: "legítimo interesse" is the candidate basis for B2B verification outreach
   - What's unclear: ANPD guidance on this specific use case; may require consent instead of legitimate interest
   - Recommendation: Ship the consent_log table with a `legal_basis: str` field that records whichever basis is confirmed. Do not hardcode; make it a config default. Block first real send until legal review is complete.

3. **LangGraph checkpoint retention / pruning**
   - What we know: Checkpoints accumulate per conversation turn; each node execution = one checkpoint row
   - What's unclear: At scale (thousands of atrativos per state × 3-5 turns = tens of thousands of rows), how quickly does `checkpoints` table grow? Is TTL pruning needed in v1?
   - Recommendation: Add a periodic cleanup task (via celery-redbeat) that deletes checkpoint rows where the conversation's `rio.sub_state` is terminal (`mar`/`dlq`/`descarte`). Mark as Wave 0 technical debt if descoping from Phase 3.

---

## Project Constraints (from CLAUDE.md)

| Directive | Impact on Phase 3 |
|-----------|-------------------|
| Collector stack: Python — FastAPI, Celery+Redis, LangGraph, Pydantic+instructor, PostgreSQL | No deviation; all Phase 3 code follows this exactly |
| No test hits Places/OTA/Apify/WhatsApp/OpenRouter/Anthropic/Mtur/norteia-api by default; real = opt-in flag | All new agents must use fake clients in the default suite; `BRAVE_RUN_REAL_EXTERNALS=True` enables real calls |
| n8n is thin transport; ALL conversation/opt-out/extraction logic lives in LangGraph code | No logic in n8n nodes; inbound webhook routes to FastAPI → Celery → LangGraph |
| CI runs without keys | `FakeWhatsAppClient`, `FakePlacesClient` (extended), `FakeApifyClient` (new), `FakeLLMClient` cover all externals |
| LGPD, WhatsApp BSP (templates/24h window/opt-out), Meta ToS (no automated DM) | Hard compliance gate; consent_log; quality rating auto-pause; no IG/FB DM |
| Google Places ToS: persist place_id, canonical = first-party validated | `AtrativoResult.place_id` only; Places content is transient signal |
| Brave core is frozen (Phases 1+2) — extend behind existing seams, do not modify | All Phase 3 code is in `brave/lanes/atrativos/` and `brave/compliance/`; core/ is read-only |
| D-18 package boundary: lanes import core, never reverse; no lane-to-lane imports | `brave/lanes/atrativos/` imports from `brave/core/` only; resolves parent destino via Mar data query, not Destinos code |
| CR-04 lesson: atomic reserve-before-call for counters | Apply to ramp counter (Redis INCR + EXPIREAT) exactly |
| CR-02: no env-var alias on API keys | New `WhatsAppConfig` must not alias any env var; each key resolves from its exact prefixed name only |

---

## Sources

### Primary (HIGH confidence)
- `docs/PLANO-BRAVE.md` §B.4, §B.6, §B.8, §C — authoritative plan for Phase 3 (Lane de Atrativos, LLM split, Compliance, Testability)
- `.planning/phases/03-atrativos-lane-whatsapp-compliance/03-CONTEXT.md` — locked decisions D-01..D-11
- `brave/clients/base.py` — Protocol seams to implement (verified in codebase)
- `brave/core/models.py` — RioRecord.sub_state, ConsentLog table location (verified in codebase)
- `brave/api/routers/dlq.py` — steward endpoint pattern this phase mirrors (verified in codebase)
- `brave/tasks/pipeline.py` — push_destination_task pattern for push_attraction_task (verified in codebase)
- PyPI version index — langgraph 1.2.5, langgraph-checkpoint-postgres 3.1.0, twilio 9.10.9, anthropic 0.109.1, google-maps-places 0.9.0, apify-client 3.0.2 (verified via `pip index versions`)
- `.planning/research/STACK.md` — project stack research (HIGH, verified 2026-06-11)
- `.planning/research/PITFALLS.md` — Pitfalls 5, 6, 7, 9, 13 directly apply to Phase 3 (HIGH)
- `.planning/research/ARCHITECTURE.md` — Patterns 3, 4; Anti-Patterns 3, 4 (HIGH)

### Secondary (MEDIUM confidence)
- [uptail.ai — WhatsApp Business Message Limits 2026](https://www.uptail.ai/blog/how-many-messages-can-you-send-on-whatsapp-business-limits-explained-for-2026) — portfolio-wide limits, tier table, Oct 2025 changes
- [fast.io — LangGraph Persistence Guide 2026](https://fast.io/resources/langgraph-persistence/) — PostgresSaver, setup() requirements, thread_id pattern
- [pickyassist.com — WhatsApp Messaging Limits & Quality Ratings 2025](https://pickyassist.com/blog/whatsapps-messaging-limits-quality-ratings-on-2025/) — quality rating tier effects
- [twilio.com/docs/whatsapp/key-concepts](https://www.twilio.com/docs/whatsapp/key-concepts) — template categories, 24h service window
- [engagelab.com — WhatsApp Business API Pricing 2026](https://www.engagelab.com/blog/whatsapp-business-api-pricing) — per-message pricing, Brazil rates
- [wuseller.com — WhatsApp opt-out keywords](https://www.wuseller.com/blog/the-only-whatsapp-opt-out-system-you-need-stop-keywords-preferences-and-compliance/) — PT-BR keywords (SAIR, PARAR, CANCELAR)
- [messagecentral.com — LGPD WhatsApp Business](https://www.messagecentral.com/blog/lgpd-whatsapp-business) — LGPD legal basis for WhatsApp contact
- [pypi.org/project/langgraph-checkpoint-postgres](https://pypi.org/project/langgraph-checkpoint-postgres/1.0.3) — package registry (version history)

### Tertiary (LOW confidence / [ASSUMED])
- Exact LGPD legal basis selection (Art. 7 VI "legítimo interesse") — requires legal review
- `origin_value = 60` for Places-discovered atrativos — calibrate on first BA state
- `AsyncPostgresSaver.from_conn_string()` constructor API for v3.1.x — verify against build-time docs

---

## Metadata

**Confidence breakdown:**
- Standard stack (packages + versions): HIGH — all verified via PyPI registry
- BSP pricing/policy (Twilio, Meta): MEDIUM-HIGH — verified against current docs (Jul/Oct 2025 changes confirmed)
- LangGraph checkpointer pattern: MEDIUM — verified against official guides; specific 3.1.x API calls need build-time confirmation
- LGPD legal basis: LOW [ASSUMED] — requires legal review; schema designed to be flexible
- Score calibration for atrativos: LOW [ASSUMED] — requires calibration on first state

**Research date:** 2026-06-12
**Valid until:** 2026-07-12 (BSP pricing/policy: 7 days; LangGraph API: 30 days; stack: 30 days)
