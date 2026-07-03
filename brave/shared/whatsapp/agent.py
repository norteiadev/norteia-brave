"""LangGraph WhatsAppAgent — multi-turn owner-validation conversation (D-08).

Implements the WhatsApp outreach conversation as a LangGraph StateGraph with
AsyncPostgresSaver checkpoint. Persists conversation state across worker restarts
so a multi-day conversation (owner doesn't reply for 24h+) survives Celery restarts.

Phase G: moved from ``brave.lanes.atrativos.whatsapp_agent`` to ``brave.shared.whatsapp``.
The pure conversation state / opt-out / routing primitives live in the sibling
``conversation`` module; this module holds the I/O-bearing graph nodes,
``_compliant_send``, and ``build_graph``.

D-18 note: ``brave.shared`` must not import ``brave.domains`` or ``brave.tasks``.
The former ``push_attraction_task`` dispatch inside ``_finalize_node`` has been
inverted to an injected ``push_confirmed_fn`` callback supplied by the caller, so
no ``brave.tasks`` import remains. (``_finalize_node`` still imports
``brave.core.models`` / ``brave.core.rio.routing`` and reaches ``brave.core`` via
``brave.compliance`` — tracked follow-up; see the package docstring.)

Architecture:
  - Sonnet 4.5 via native Anthropic SDK generates PT-BR turns (asks questions,
    identifies Norteia, states opt-out option). D-08.
  - DeepSeek via instructor+Mode.Tools extracts structured answers from replies.
    ConversationExtractionResult is the 2nd-layer Pydantic validator. D-08, D-09.
  - All outbound sends pass through _compliant_send() → send_path_gate() → wa_client.
    NO direct send_template() call exists outside _compliant_send(). D-11.
  - Opt-out keywords (SAIR, PARAR, etc.) detected in recv_reply_node → record_opt_out
    → state["opted_out"] = True → graph routes to finalize_node → DLQ. COMP-01/02.
  - Owner-validation success (existe=sim, funcionando=sim) triggers re-score:
    finalize_node → reprocess_record → promote_to_mar → push_confirmed_fn (injected). D-10.

thread_id = f"atrativo:{rio_id}" (keyed by UUID, never by phone). RESEARCH Pitfall 2.
max_turns guard prevents infinite loops (configurable, default 3). T-03-04-04.

Node layout:
  outreach_task fires:
    ainvoke(initial_state) → send_opening_node → END (await inbound)

  resume_conversation_task fires (inbound reply appended to messages in state):
    ainvoke(state_update) → recv_reply_node
      ├─ opted_out → finalize_node → END
      └─ extract_answers_node
            ├─ all answers present → finalize_node → END
            └─ missing + turns < max → ask_followup_node → END (await inbound)
            └─ missing + turns >= max → finalize_node → END

build_graph() factory injects all dependencies via closures. The checkpointer
parameter is injectable so unit tests can pass MemorySaver instead of
AsyncPostgresSaver.

KNOWN LIMITATION — sync session across the asyncio boundary (WR-06):
  The graph nodes mutate a synchronous SQLAlchemy Session (`session.flush()`)
  from inside `async def` nodes driven by `asyncio.run(_run())` in the Celery
  task, while AsyncPostgresSaver commits checkpoints on a SEPARATE async
  connection. The two are NOT in a shared transaction, so on an exception path
  the checkpoint can commit while the sync session rolls back (or vice-versa),
  leaving partial state and a possible duplicate-send on task retry.

  Mitigations in place until this is restructured to an async session (deferred):
    - The FSM-advancing tasks hold a row-level lock (SELECT ... FOR UPDATE) on
      the RioRecord for the whole task transaction (CR-04), serializing
      concurrent resumes so two checkpoint resumes cannot interleave sends.
    - The send path is idempotency-gated on sub_state and the consent-row upsert
      (WR-09), and the ramp counter reserves before call (CR-04), bounding the
      blast radius of a single duplicate attempt.
  A full fix (async session, or moving all DB mutations into the sync task body
  with nodes returning pure state deltas) is tracked as a follow-up.
"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from brave.shared.whatsapp.conversation import (
    ALL_OPT_OUT_KEYWORDS,
    OPT_OUT_KEYWORDS,
    ConversationState,
    _after_extract_answers,
    _after_recv_reply,
    _detect_opt_out_keyword,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from redis import Redis
    from sqlalchemy.orm import Session

    from brave.clients.base import LLMClientProtocol, WhatsAppClientProtocol
    from brave.config.settings import ScoreConfig, WhatsAppConfig
    from brave.core.models import RioRecord

logger = structlog.get_logger(__name__)

# Public surface. ALL_OPT_OUT_KEYWORDS / OPT_OUT_KEYWORDS / ConversationState are
# re-exported from the sibling ``conversation`` module so callers and tests keep a
# single import site (``brave.shared.whatsapp.agent``).
__all__ = [
    "ALL_OPT_OUT_KEYWORDS",
    "OPT_OUT_KEYWORDS",
    "ConversationState",
    "build_graph",
    "_compliant_send",
    "_extract_answers_node",
    "_recv_reply_node",
]


# ---------------------------------------------------------------------------
# Private gate wrapper — the ONLY allowed send_template call site (D-11)
# ---------------------------------------------------------------------------


async def _compliant_send(
    session: "Session",
    redis_client: "Redis",
    rio: "RioRecord",
    wa_client: "WhatsAppClientProtocol",
    contact_phone: str,
    template_name: str,
    params: dict[str, Any],
    settings: "WhatsAppConfig",
) -> dict[str, Any]:
    """Gate-then-send wrapper — the single call site for wa_client.send_template.

    Architecture invariant (D-11, T-03-04-01):
      Every outbound WhatsApp message MUST pass through this function.
      send_path_gate is called first — if it raises ComplianceError, send_template
      is never called. No direct wa_client.send_template() call exists outside
      this function. grep-verifiable: "send_template" appears here and ONLY here.

    Args:
        session:        SQLAlchemy synchronous Session.
        redis_client:   Redis client for ramp + quality flag checks.
        rio:            RioRecord being processed (for sub_state + uf).
        wa_client:      WhatsAppClientProtocol implementation (Twilio or Null).
        contact_phone:  Recipient phone in E.164 format.
        template_name:  Approved BSP template name.
        params:         Template parameters; must include "body" with "Norteia".
        settings:       WhatsAppConfig with approved_templates + ramp_cap.

    Returns:
        Delivery status dict from send_template ({"message_sid": ..., "status": ...}).

    Raises:
        ComplianceError: If any gate condition fails (message NOT sent).
    """
    from brave.compliance.gate import send_path_gate

    # Gate runs synchronously before every send — no network, no LLM. Pure checks.
    send_path_gate(
        session=session,
        redis_client=redis_client,
        rio=rio,
        contact_phone=contact_phone,
        template_name=template_name,
        params=params,
        settings=settings,
    )

    # Gate passed — invoke the BSP client (Twilio in production, Null offline)
    # send_template is called here and ONLY here in the production graph
    return await wa_client.send_template(to=contact_phone, template=template_name, params=params)


# ---------------------------------------------------------------------------
# Graph node functions (async for LangGraph 1.x compatibility)
# Dependencies are injected via functools.partial in build_graph().
# ---------------------------------------------------------------------------


async def _send_opening_node(
    state: ConversationState,
    *,
    session: "Session",
    redis_client: "Redis",
    rio: "RioRecord",
    wa_client: "WhatsAppClientProtocol",
    settings: "WhatsAppConfig",
) -> dict[str, Any]:
    """Node: write consent record and send opening template.

    Called at graph START — sends the first outreach message to the owner.
    Always writes (upserts) a consent record before any send (gate condition 1).

    WR-09: if the phone already opted out, write_consent_record raises
    OptedOutError — the outreach is aborted (record → DLQ) instead of creating a
    contradictory active consent row.

    Returns state update with the opening turn appended to messages.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from brave.compliance.consent_log import OptedOutError, write_consent_record

    contact_phone = state["contact_phone"]
    template_name = state["outreach_template"]

    # PT-BR template params — body must include "Norteia" (gate condition 2)
    params = {
        "body": (
            "Olá! Sou da Norteia, plataforma de destinos turísticos do Brasil. "
            "Estamos validando informações sobre seu estabelecimento. "
            "Poderia nos confirmar: seu negócio ainda está em funcionamento? "
            "Responda SAIR para não receber mais mensagens."
        )
    }

    # Write (upsert) LGPD consent record before first contact (gate condition 1).
    # WR-09: refuses + raises for an already-opted-out phone.
    try:
        write_consent_record(
            session=session,
            phone_e164=contact_phone,
            rio_id=rio.id,
            legal_basis="legitimate_interest_commercial_verification",
            norteia_identified=True,
            purpose="business_validation",
        )
    except OptedOutError:
        # Opted-out phone — abort outreach, route to DLQ (no send, no new row).
        rio.routing = "dlq"
        rio.dlq_reason = "owner_opted_out"
        flag_modified(rio, "normalized")
        session.flush()
        logger.info("opening_aborted_opted_out", rio_id=state["rio_id"])
        return {"opted_out": True}
    session.flush()

    # Send through compliance gate — will raise ComplianceError if any condition fails
    result = await _compliant_send(
        session=session,
        redis_client=redis_client,
        rio=rio,
        wa_client=wa_client,
        contact_phone=contact_phone,
        template_name=template_name,
        params=params,
        settings=settings,
    )

    opening_turn = {
        "role": "assistant",
        "content": params["body"],
        "message_sid": result.get("message_sid", ""),
    }

    logger.info(
        "whatsapp_opening_sent",
        rio_id=state["rio_id"],
        template=template_name,
        phone_prefix=contact_phone[:5],
    )

    return {
        "messages": state["messages"] + [opening_turn],
        "turns": 1,
    }


def _persist_window_state(
    rio: "RioRecord",
    *,
    window_open: bool,
    last_inbound_at: str,
) -> None:
    """Persist the 24h-window state to rio.normalized so the gate can read it (WR-04).

    The compliance gate's condition 5 reads ``rio.normalized["window_open"]``.
    The conversation node previously only returned this in LangGraph state, which
    the gate never sees — so condition 5 always saw the default True and the 24h
    window was never enforced. This writes it (with flag_modified, the Phase 2
    JSONB-mutation lesson) to the key the gate actually reads.
    """
    from sqlalchemy.orm.attributes import flag_modified

    normalized = dict(rio.normalized or {})
    normalized["window_open"] = window_open
    normalized["last_inbound_at"] = last_inbound_at
    rio.normalized = normalized
    flag_modified(rio, "normalized")


async def _recv_reply_node(
    state: ConversationState,
    *,
    session: "Session",
    rio: "RioRecord",
) -> dict[str, Any]:
    """Node: receive an inbound reply, detect opt-out, check 24h window.

    Called by resume_conversation_task. Reads message_text from state
    (set by resume_conversation_task as a state update alongside the user turn).
    Detects OPT_OUT_KEYWORDS (exact match, uppercase). If opted out:
      - record_opt_out() is called immediately
      - state["opted_out"] = True
      - graph routes to finalize_node (conditional edge in build_graph)

    WR-04: the 24h window is computed against the GENUINE previous inbound
    timestamp (the one persisted before this reply), then both ``window_open``
    and the new ``last_inbound_at`` are persisted to rio.normalized so the gate's
    condition 5 reads a real value instead of the default True.

    Returns:
        State update dict.
    """
    from brave.compliance.consent_log import record_opt_out

    contact_phone = state["contact_phone"]
    message_text = state.get("message_text", "")
    now_utc = datetime.now(timezone.utc)

    # Opt-out keyword detection — whole-token match against known PT-BR / Meta
    # opt-out keywords. CR-01: NOT a substring match, so "NÃO sei o horário"
    # does not opt the contact out.
    detected_keyword: str | None = _detect_opt_out_keyword(message_text)

    now_iso = now_utc.isoformat()

    if detected_keyword is not None:
        # LGPD opt-out must be honored immediately — write record and route to finalize
        record_opt_out(session=session, phone_e164=contact_phone, keyword=detected_keyword)
        # Persist window state even on opt-out so the gate never reads a stale True
        # (no send happens on this path, but keep rio.normalized consistent).
        _persist_window_state(rio, window_open=False, last_inbound_at=now_iso)
        session.flush()

        logger.info(
            "opt_out_detected",
            rio_id=state["rio_id"],
            keyword=detected_keyword,
            phone_prefix=contact_phone[:5],
        )

        return {
            "opted_out": True,
            "window_open": False,
            "last_inbound_at": now_iso,
        }

    # WR-04: an inbound reply reopens the BSP 24h customer-service window. The
    # follow-up sent later in this same resume is therefore within the window.
    # We persist window_open=True keyed to THIS inbound's timestamp; a future
    # send-time check (e.g. a scheduled re-engagement after a long gap) compares
    # against last_inbound_at and would mark the window closed once >24h elapses.
    window_open = True
    _persist_window_state(rio, window_open=window_open, last_inbound_at=now_iso)
    session.flush()

    return {
        "last_inbound_at": now_iso,
        "window_open": window_open,
        "opted_out": False,
    }


async def _extract_answers_node(
    state: ConversationState,
    *,
    llm_client: "LLMClientProtocol",
) -> dict[str, Any]:
    """Node: extract structured answers from the conversation using DeepSeek/instructor.

    Uses llm_client.extract() with ConversationExtractionResult schema and mode="tools"
    (instructor Mode.Tools — mandatory for DeepSeek schema adherence, D-09).
    The 2nd-layer Pydantic validator is built into ConversationExtractionResult.

    On ValidationError or extraction failure, returns extraction=None (DLQ path).
    Never propagates extraction exceptions — quarantine happens in finalize_node.

    Returns:
        State update with extraction dict or None.
    """
    from pydantic import ValidationError

    from brave.shared.whatsapp.schemas import ConversationExtractionResult

    # Build a minimal prompt with only message text (no phone number — T-03-04-03)
    conversation_text = "\n".join(
        f"{turn['role'].upper()}: {turn['content']}"
        for turn in state["messages"]
    )
    prompt = (
        "Você é um extrator de informações de conversas de WhatsApp entre a Norteia "
        "e proprietários de estabelecimentos turísticos no Brasil.\n\n"
        f"Conversa:\n{conversation_text}\n\n"
        "Extraia as informações do estabelecimento conforme solicitado."
    )

    try:
        result = await llm_client.extract(
            prompt=prompt,
            schema=ConversationExtractionResult,
            mode="tools",
        )

        # 2nd-layer validation: re-parse through Pydantic (instructor may skip on retries)
        if isinstance(result, ConversationExtractionResult):
            validated = result
        elif isinstance(result, dict):
            validated = ConversationExtractionResult(**result)
        else:
            logger.warning(
                "extraction_unexpected_type",
                rio_id=state["rio_id"],
                result_type=type(result).__name__,
            )
            return {"extraction": None}

        logger.info(
            "extraction_success",
            rio_id=state["rio_id"],
            existe=validated.existe,
            funcionando=validated.funcionando,
            confidence=validated.confidence,
        )
        return {"extraction": validated.model_dump()}

    except ValidationError as exc:
        # 2nd-layer validation failed — quarantine in finalize_node
        logger.warning(
            "extraction_validation_error",
            rio_id=state["rio_id"],
            error=str(exc),
        )
        return {"extraction": None}

    except Exception as exc:
        # Instructor retry exhaustion or LLM error — DLQ path
        logger.warning(
            "extraction_failed",
            rio_id=state["rio_id"],
            error=str(exc),
        )
        return {"extraction": None}


async def _ask_followup_node(
    state: ConversationState,
    *,
    session: "Session",
    redis_client: "Redis",
    rio: "RioRecord",
    wa_client: "WhatsAppClientProtocol",
    llm_client: "LLMClientProtocol",
    settings: "WhatsAppConfig",
) -> dict[str, Any]:
    """Node: generate a follow-up question via Sonnet and send it.

    Called when extraction is incomplete and turns < max_turns.
    Sonnet generates a PT-BR follow-up question targeting the missing fields.
    Send passes through _compliant_send (gate enforced).

    Returns state update with follow-up turn appended and turns incremented.
    """
    contact_phone = state["contact_phone"]
    new_turns = state["turns"] + 1

    # Determine what information is still missing
    extraction = state.get("extraction") or {}
    missing_fields = []
    if not extraction.get("existe"):
        missing_fields.append("se o estabelecimento ainda existe")
    if not extraction.get("funcionando"):
        missing_fields.append("se está funcionando atualmente")
    if not extraction.get("horarios"):
        missing_fields.append("os horários de funcionamento")

    missing_str = " e ".join(missing_fields) if missing_fields else "mais detalhes"

    # Generate follow-up via Sonnet (conversation quality + PT-BR, D-08)
    conversation_text = "\n".join(
        f"{turn['role'].upper()}: {turn['content']}"
        for turn in state["messages"]
    )
    prompt = (
        f"Você é um assistente da Norteia fazendo validação de dados de atrativos turísticos. "
        f"Continue a conversa abaixo em português, perguntando educadamente sobre: {missing_str}. "
        f"Identifique-se como Norteia. Seja breve e direto. Não use jargão técnico.\n\n"
        f"Conversa até agora:\n{conversation_text}"
    )

    try:
        followup_text = await llm_client.generate(
            messages=[{"role": "user", "content": prompt}],
            model="claude-sonnet-4-5",
        )
    except Exception as exc:
        logger.warning(
            "followup_generation_failed",
            rio_id=state["rio_id"],
            error=str(exc),
        )
        # Fallback to a generic follow-up message if Sonnet fails
        followup_text = (
            f"Olá novamente! Da Norteia. Poderia nos confirmar: {missing_str}? "
            "Responda SAIR para não receber mais mensagens."
        )

    params = {"body": followup_text}

    # Ensure Norteia is in the message (gate condition 2)
    if "Norteia" not in params["body"]:
        params["body"] = f"Da Norteia: {params['body']}"

    result = await _compliant_send(
        session=session,
        redis_client=redis_client,
        rio=rio,
        wa_client=wa_client,
        contact_phone=contact_phone,
        template_name=state["outreach_template"],
        params=params,
        settings=settings,
    )

    followup_turn = {
        "role": "assistant",
        "content": params["body"],
        "message_sid": result.get("message_sid", ""),
    }

    logger.info(
        "followup_sent",
        rio_id=state["rio_id"],
        turns=new_turns,
        phone_prefix=contact_phone[:5],
    )

    return {
        "messages": state["messages"] + [followup_turn],
        "turns": new_turns,
    }


async def _finalize_node(
    state: ConversationState,
    *,
    session: "Session",
    rio: "RioRecord",
    score_config: "ScoreConfig",
    push_confirmed_fn: "Callable[[str], None] | None" = None,
) -> dict[str, Any]:
    """Node: apply extraction result to the record and trigger re-score.

    Owner-validation success (existe=sim, funcionando=sim):
      - Raises validacao_humana_value=100 on rio.normalized
      - Calls reprocess_record → route_by_score → promote_to_mar → push_confirmed_fn
      D-10: owner-validation feeds existing reprocess_record (no new scoring branch).

    Owner-validation failure or no-answer:
      - Sets rio.dlq_reason = "owner_no_answer" or "owner_opted_out"
      - Routes record to DLQ (routing="dlq")

    push_confirmed_fn is the injected push dispatcher (D-18: keeps brave.tasks out
    of brave.shared). When routing crosses to "mar" it is called with the rio_id;
    the caller (tasks layer) owns the actual push_attraction_task.delay. None (the
    default in unit build_graph) skips the push — the promotion still persists.

    Returns:
        Empty state update (finalize_node terminates; graph routes to END).
    """
    import uuid

    from sqlalchemy.orm.attributes import flag_modified

    from brave.core.models import RioRecord
    from brave.core.rio.routing import reprocess_record as _reprocess

    extraction = state.get("extraction")
    opted_out = state.get("opted_out", False)

    # WR-07: operate on a SINGLE freshly-fetched record, never the closure-captured
    # `rio`. After asyncio.run boundaries and intermediate flushes the captured
    # instance may be detached/stale; mixing it with the reprocessed record can
    # read stale routing/normalized. Re-fetch here and use `record` throughout.
    rio_uuid = uuid.UUID(state["rio_id"])
    record = session.get(RioRecord, rio_uuid)
    if record is None:
        # Fall back to the captured instance only if the fetch fails (should not
        # happen — the record exists for the whole conversation).
        record = rio

    if opted_out:
        # Opt-out path — record is already marked in consent_log by recv_reply_node
        record.routing = "dlq"
        record.dlq_reason = "owner_opted_out"
        session.flush()
        logger.info("finalize_opted_out", rio_id=state["rio_id"])
        return {}

    # Evaluate extraction result
    owner_confirmed = (
        extraction is not None
        and extraction.get("existe") == "sim"
        and extraction.get("funcionando") in ("sim", "temporariamente_fechado")
    )

    if not owner_confirmed:
        # No answer or negative answer → DLQ
        record.routing = "dlq"
        record.dlq_reason = "owner_no_answer"
        session.flush()
        logger.info("finalize_no_answer", rio_id=state["rio_id"], extraction=extraction)
        return {}

    # Owner confirmed — raise validacao_humana_value to 100 and re-score
    normalized = dict(record.normalized or {})
    normalized["validacao_humana_value"] = 100.0

    # Store extracted data in normalized (horarios, valor)
    if extraction.get("horarios"):
        normalized["owner_horarios"] = extraction["horarios"]
    if extraction.get("valor"):
        normalized["owner_valor"] = extraction["valor"]

    record.normalized = normalized
    flag_modified(record, "normalized")
    session.flush()

    # Re-score via existing reprocess_record (D-10 — no new scoring branch)
    reprocessed_rio = _reprocess(session, rio_uuid, score_config)
    session.flush()

    logger.info(
        "finalize_reprocessed",
        rio_id=state["rio_id"],
        new_routing=reprocessed_rio.routing,
        score=float(reprocessed_rio.score or 0),
    )

    # If routing crossed to "mar", dispatch via the injected push callback.
    # push_confirmed_fn is supplied by the caller (tasks layer) so this shared
    # module never imports brave.tasks (D-18). Best-effort: a missing broker
    # (dev/test) or a None callback (unit build_graph) is not fatal — the Mar
    # promotion already persisted and the push can be retried.
    if reprocessed_rio.routing == "mar" and push_confirmed_fn is not None:
        try:
            push_confirmed_fn(state["rio_id"])
        except Exception as exc:
            logger.warning(
                "push_attraction_dispatch_failed",
                rio_id=state["rio_id"],
                error=str(exc),
            )

    return {}


# ---------------------------------------------------------------------------
# build_graph() factory
# ---------------------------------------------------------------------------


def build_graph(
    wa_client: "WhatsAppClientProtocol",
    llm_client: "LLMClientProtocol",
    session: "Session",
    redis_client: "Redis",
    rio: "RioRecord",
    config: "ScoreConfig",
    settings: "WhatsAppConfig",
    push_confirmed_fn: "Callable[[str], None] | None" = None,
    checkpointer: Any = None,
) -> Any:
    """Build and compile the WhatsAppAgent LangGraph StateGraph.

    Dependencies are injected via functools.partial closures so each node
    receives state: ConversationState (plus keyword-only deps via partial) at call time.

    In production (outreach_task / resume_conversation_task):
      checkpointer = AsyncPostgresSaver (from langgraph-checkpoint-postgres)
      AsyncPostgresSaver.setup() must be called before first use.

    In unit tests:
      checkpointer = MemorySaver (from langgraph.checkpoint.memory)
      No real DB needed.

    Args:
        wa_client:    WhatsApp client (TwilioWhatsAppClient or NullWhatsAppClient).
        llm_client:   LLM client (real or FakeLLMClient).
        session:      SQLAlchemy synchronous Session.
        redis_client: Redis client (real or fakeredis).
        rio:          RioRecord being processed.
        config:       ScoreConfig for re-scoring in finalize_node.
        settings:     WhatsAppConfig with approved_templates + ramp_cap.
        push_confirmed_fn: Injected callback invoked with rio_id when finalize
                      promotes the record to Mar (D-18: keeps brave.tasks out of
                      brave.shared). None skips the push (unit tests).
        checkpointer: LangGraph checkpointer (AsyncPostgresSaver or MemorySaver).

    Returns:
        Compiled LangGraph Pregel object with .ainvoke() method.
    """
    from langgraph.graph import END, StateGraph

    builder = StateGraph(ConversationState)

    # Bind node functions with their dependencies via functools.partial
    send_opening = functools.partial(
        _send_opening_node,
        session=session,
        redis_client=redis_client,
        rio=rio,
        wa_client=wa_client,
        settings=settings,
    )
    recv_reply = functools.partial(
        _recv_reply_node,
        session=session,
        rio=rio,
    )
    extract_answers = functools.partial(
        _extract_answers_node,
        llm_client=llm_client,
    )
    ask_followup = functools.partial(
        _ask_followup_node,
        session=session,
        redis_client=redis_client,
        rio=rio,
        wa_client=wa_client,
        llm_client=llm_client,
        settings=settings,
    )
    finalize = functools.partial(
        _finalize_node,
        session=session,
        rio=rio,
        score_config=config,
        push_confirmed_fn=push_confirmed_fn,
    )

    # Register nodes
    builder.add_node("send_opening", send_opening)
    builder.add_node("recv_reply", recv_reply)
    builder.add_node("extract_answers", extract_answers)
    builder.add_node("ask_followup", ask_followup)
    builder.add_node("finalize", finalize)

    # Entry point: send_opening is the START for new conversations
    builder.set_entry_point("send_opening")

    # Edges
    builder.add_edge("send_opening", END)  # Wait for inbound webhook to resume
    builder.add_conditional_edges(
        "recv_reply",
        _after_recv_reply,
        {
            "finalize": "finalize",
            "extract_answers": "extract_answers",
        },
    )
    builder.add_conditional_edges(
        "extract_answers",
        _after_extract_answers,
        {
            "finalize": "finalize",
            "ask_followup": "ask_followup",
        },
    )
    builder.add_edge("ask_followup", END)  # Wait for next inbound webhook
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
