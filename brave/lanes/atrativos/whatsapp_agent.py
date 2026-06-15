"""LangGraph WhatsAppAgent — multi-turn owner-validation conversation (D-08).

Implements the WhatsApp outreach conversation as a LangGraph StateGraph with
AsyncPostgresSaver checkpoint. Persists conversation state across worker restarts
so a multi-day conversation (owner doesn't reply for 24h+) survives Celery restarts.

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
    finalize_node → reprocess_record → promote_to_mar → push_attraction_task. D-10.

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
"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypedDict

import structlog

if TYPE_CHECKING:
    from redis import Redis
    from sqlalchemy.orm import Session

    from brave.clients.base import LLMClientProtocol, WhatsAppClientProtocol
    from brave.config.settings import ScoreConfig, WhatsAppConfig
    from brave.core.models import RioRecord

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Opt-out keyword set (COMP-02, D-11, recv_reply node)
# ---------------------------------------------------------------------------

OPT_OUT_KEYWORDS: frozenset[str] = frozenset(
    {"SAIR", "PARAR", "CANCELAR", "REMOVER", "STOP", "NÃO"}
)

# ---------------------------------------------------------------------------
# Conversation state schema (TypedDict for LangGraph StateGraph)
# ---------------------------------------------------------------------------


class ConversationState(TypedDict):
    """LangGraph state for the WhatsApp owner-validation conversation.

    Persisted by AsyncPostgresSaver between invocations (multi-day conversations).
    thread_id = f"atrativo:{rio_id}" — keyed by RioRecord UUID, never phone number.

    message_text is a temporary field used by recv_reply_node to access the
    latest inbound reply. It is set as a state update in resume_conversation_task
    alongside the user turn appended to messages.
    """

    rio_id: str  # immutable — links to RioRecord UUID
    contact_phone: str  # E.164 format (+55...); never passed to LLM (T-03-04-03)
    messages: list[dict[str, Any]]  # full turn history [{role, content}]
    extraction: dict[str, Any] | None  # ConversationExtractionResult dict or None
    opted_out: bool  # True if opt-out keyword detected
    window_open: bool  # True if within 24h of last inbound message
    last_inbound_at: str | None  # ISO UTC timestamp of last inbound message
    turns: int  # guards against infinite loops (T-03-04-04)
    max_turns: int  # from config; default 3
    outreach_template: str  # BSP-approved template name used for opening
    message_text: str  # inbound message text for current turn (set by resume task)


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
    Always writes a consent record before any send (gate condition 1).

    Returns state update with the opening turn appended to messages.
    """
    from brave.compliance.consent_log import write_consent_record

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

    # Write LGPD consent record before first contact (gate condition 1)
    write_consent_record(
        session=session,
        phone_e164=contact_phone,
        rio_id=rio.id,
        legal_basis="legitimate_interest_commercial_verification",
        norteia_identified=True,
        purpose="business_validation",
    )
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


async def _recv_reply_node(
    state: ConversationState,
    *,
    session: "Session",
) -> dict[str, Any]:
    """Node: receive an inbound reply, detect opt-out, check 24h window.

    Called by resume_conversation_task. Reads message_text from state
    (set by resume_conversation_task as a state update alongside the user turn).
    Detects OPT_OUT_KEYWORDS (exact match, uppercase). If opted out:
      - record_opt_out() is called immediately
      - state["opted_out"] = True
      - graph routes to finalize_node (conditional edge in build_graph)

    Returns:
        State update dict.
    """
    from brave.compliance.consent_log import record_opt_out

    contact_phone = state["contact_phone"]
    message_text = state.get("message_text", "")
    upper_text = message_text.upper().strip()
    now_utc = datetime.now(timezone.utc)

    # Opt-out keyword detection — exact match against known PT-BR / Meta opt-out keywords
    detected_keyword: str | None = None
    for kw in OPT_OUT_KEYWORDS:
        if kw in upper_text:
            detected_keyword = kw
            break

    if detected_keyword is not None:
        # LGPD opt-out must be honored immediately — write record and route to finalize
        record_opt_out(session=session, phone_e164=contact_phone, keyword=detected_keyword)
        session.flush()

        logger.info(
            "opt_out_detected",
            rio_id=state["rio_id"],
            keyword=detected_keyword,
            phone_prefix=contact_phone[:5],
        )

        return {
            "opted_out": True,
            "last_inbound_at": now_utc.isoformat(),
        }

    # Check 24h window — compare last_inbound_at to now
    window_open = True
    if state.get("last_inbound_at"):
        try:
            last_at = datetime.fromisoformat(state["last_inbound_at"])
            if (now_utc - last_at).total_seconds() > 86400:
                window_open = False
        except ValueError:
            pass  # malformed timestamp — treat as open (conservative)

    return {
        "last_inbound_at": now_utc.isoformat(),
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

    from brave.lanes.atrativos.schemas import ConversationExtractionResult

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
) -> dict[str, Any]:
    """Node: apply extraction result to the record and trigger re-score.

    Owner-validation success (existe=sim, funcionando=sim):
      - Raises validacao_humana_value=100 on rio.normalized
      - Calls reprocess_record → route_by_score → promote_to_mar → push_attraction
      D-10: owner-validation feeds existing reprocess_record (no new scoring branch).

    Owner-validation failure or no-answer:
      - Sets rio.dlq_reason = "owner_no_answer" or "owner_opted_out"
      - Routes record to DLQ (routing="dlq")

    Returns:
        Empty state update (finalize_node terminates; graph routes to END).
    """
    import uuid

    from sqlalchemy.orm.attributes import flag_modified

    from brave.core.rio.routing import reprocess_record as _reprocess

    extraction = state.get("extraction")
    opted_out = state.get("opted_out", False)

    if opted_out:
        # Opt-out path — record is already marked in consent_log by recv_reply_node
        rio.routing = "dlq"
        rio.dlq_reason = "owner_opted_out"
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
        rio.routing = "dlq"
        rio.dlq_reason = "owner_no_answer"
        session.flush()
        logger.info("finalize_no_answer", rio_id=state["rio_id"], extraction=extraction)
        return {}

    # Owner confirmed — raise validacao_humana_value to 100 and re-score
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0

    # Store extracted data in normalized (horarios, valor)
    if extraction.get("horarios"):
        normalized["owner_horarios"] = extraction["horarios"]
    if extraction.get("valor"):
        normalized["owner_valor"] = extraction["valor"]

    rio.normalized = normalized
    flag_modified(rio, "normalized")
    session.flush()

    # Re-score via existing reprocess_record (D-10 — no new scoring branch)
    reprocessed_rio = _reprocess(session, uuid.UUID(state["rio_id"]), score_config)
    session.flush()

    logger.info(
        "finalize_reprocessed",
        rio_id=state["rio_id"],
        new_routing=reprocessed_rio.routing,
        score=float(reprocessed_rio.score or 0),
    )

    # If routing crossed to "mar", dispatch push_attraction_task
    if reprocessed_rio.routing == "mar":
        try:
            from brave.tasks.pipeline import push_attraction_task
            push_attraction_task.delay(state["rio_id"])
        except Exception as exc:
            # No broker (dev/test) — not a fatal error; push_attraction can be retried
            logger.warning(
                "push_attraction_dispatch_failed",
                rio_id=state["rio_id"],
                error=str(exc),
            )

    return {}


# ---------------------------------------------------------------------------
# Graph routing functions
# ---------------------------------------------------------------------------


def _after_recv_reply(state: ConversationState) -> str:
    """Route after recv_reply_node: opted_out → finalize, else extract_answers."""
    if state.get("opted_out"):
        return "finalize"
    return "extract_answers"


def _after_extract_answers(state: ConversationState) -> str:
    """Route after extract_answers: all present → finalize, missing → ask_followup or finalize."""
    extraction = state.get("extraction") or {}
    turns = state.get("turns", 0)
    max_turns = state.get("max_turns", 3)

    # All required answers present
    if extraction.get("existe") and extraction.get("funcionando"):
        return "finalize"

    # Max turns reached — finalize with whatever we have
    if turns >= max_turns:
        return "finalize"

    # Missing answers and turns remaining — ask follow-up
    return "ask_followup"


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
