"""Unit tests for WhatsAppAgent LangGraph graph (D-08, D-11, COMP-01/02).

100% offline — no real DB, Redis, Twilio, Anthropic, or OpenRouter calls.
Uses:
  - MemorySaver (langgraph.checkpoint.memory) as checkpointer (no real Postgres)
  - FakeWhatsAppClient (tests/fakes/fake_whatsapp.py) for offline template sends
  - FakeLLMClient (tests/fakes/fake_llm.py) for offline LLM extraction/generation
  - fakeredis for Redis ramp/quality flag checks
  - Mock SQLAlchemy Session (MagicMock) for consent_log / opt-out lookups
  - Mock RioRecord for sub_state / uf / normalized access

Test coverage:
  1. test_opt_out_keyword_routes_to_end         — SAIR → opted_out=True → finalize → dlq
  2. test_norteia_not_in_params_raises_compliance_error — gate condition 2
  3. test_extraction_result_validated_by_pydantic    — FakeLLMClient + valid schema
  4. test_extraction_quarantines_invalid_schema       — FakeLLMClient raises → extraction=None
  5. test_build_graph_returns_compiled_graph          — build_graph returns Pregel

All tests are async (pytest-asyncio, asyncio_mode=auto set in pyproject.toml).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from brave.compliance.gate import ComplianceError
from brave.lanes.atrativos.schemas import ConversationExtractionResult
from brave.lanes.atrativos.whatsapp_agent import (
    OPT_OUT_KEYWORDS,
    ConversationState,
    _extract_answers_node,
    _recv_reply_node,
    build_graph,
)
from tests.fakes.fake_llm import FakeLLMClient
from tests.fakes.fake_whatsapp import FakeWhatsAppClient


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_rio(sub_state: str = "whatsapp_in_progress") -> MagicMock:
    """Create a minimal mock RioRecord for tests."""
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.sub_state = sub_state
    rio.uf = "BA"
    rio.normalized = {"window_open": True}
    rio.routing = "dlq"
    rio.dlq_reason = None
    return rio


def _make_settings(approved_templates: list[str] | None = None) -> MagicMock:
    """Create a mock WhatsAppConfig for tests."""
    settings = MagicMock()
    settings.approved_templates = approved_templates or ["norteia_v1"]
    settings.ramp_cap = 100
    return settings


def _make_session() -> MagicMock:
    """Create a minimal mock SQLAlchemy Session."""
    session = MagicMock()
    session.flush = MagicMock()
    session.scalar = MagicMock(return_value=None)  # default: no consent_log rows
    session.add = MagicMock()
    return session


def _make_initial_state(rio_id: str | None = None) -> ConversationState:
    """Create a minimal initial ConversationState."""
    return ConversationState(
        rio_id=rio_id or str(uuid.uuid4()),
        contact_phone="+5573999999999",
        messages=[],
        extraction=None,
        opted_out=False,
        window_open=True,
        last_inbound_at=None,
        turns=0,
        max_turns=3,
        outreach_template="norteia_v1",
        message_text="",
    )


# ---------------------------------------------------------------------------
# Test 1: Opt-out keyword detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opt_out_keyword_routes_to_end() -> None:
    """test_opt_out_keyword_routes_to_end:
    Calling recv_reply_node with message_text="SAIR" sets opted_out=True.
    record_opt_out is called with keyword="SAIR".
    """
    session = _make_session()
    rio_id = str(uuid.uuid4())
    state = _make_initial_state(rio_id)
    state["message_text"] = "SAIR"

    with patch("brave.compliance.consent_log.record_opt_out") as mock_opt_out:
        result = await _recv_reply_node(state, session=session)

    # opted_out=True must be in the state update
    assert result.get("opted_out") is True
    # record_opt_out must have been called with the keyword
    mock_opt_out.assert_called_once()
    call_kwargs = mock_opt_out.call_args
    assert call_kwargs.kwargs.get("keyword") == "SAIR"
    session.flush.assert_called()


@pytest.mark.asyncio
async def test_opt_out_keyword_with_politeness_filler() -> None:
    """Opt-out detected when the reply is the keyword plus polite filler.

    CR-01: detection is message-anchored. "quero sair, obrigado" reduces to a
    single meaningful token (SAIR) and counts as an opt-out, while a keyword
    buried in a real sentence does not.
    """
    session = _make_session()
    state = _make_initial_state()
    state["message_text"] = "quero sair, obrigado"

    with patch("brave.compliance.consent_log.record_opt_out") as mock_opt_out:
        result = await _recv_reply_node(state, session=session)

    assert result.get("opted_out") is True
    mock_opt_out.assert_called_once()


@pytest.mark.asyncio
async def test_opt_out_keyword_in_sentence_not_detected() -> None:
    """CR-01: a keyword token inside a longer, meaningful sentence does NOT opt out."""
    session = _make_session()
    state = _make_initial_state()
    state["message_text"] = "Obrigado mas vamos parar amanhã"

    with patch("brave.compliance.consent_log.record_opt_out") as mock_opt_out:
        result = await _recv_reply_node(state, session=session)

    assert result.get("opted_out") is False
    mock_opt_out.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_text",
    [
        # "NÃO" as a common in-context word — must NOT opt out (only bare "não" does)
        "NÃO sei o horário, mas estamos abertos",
        "Não vamos parar de funcionar",
        "Estamos sempre disponíveis, não pare de nos chamar",
        # keyword as a standalone token but inside a real sentence — not opt-out
        "Pode cancelar minha dúvida anterior",
        "Vou remover a foto antiga do perfil",
        # keyword only as a substring of a larger word — not a standalone token
        "Vou recancelar a reserva mais tarde",
        "O parador da cidade fica aberto",
    ],
)
async def test_opt_out_no_false_positive_on_substring(message_text: str) -> None:
    """CR-01 regression: legitimate replies that merely CONTAIN an opt-out keyword
    as a substring (or the common word "não" in-context) must NOT opt the contact out.

    Before the fix these triggered a false opt-out via unanchored substring match,
    silently suppressing high-value owner-validated records (LGPD data loss).
    """
    session = _make_session()
    state = _make_initial_state()
    state["message_text"] = message_text

    with patch("brave.compliance.consent_log.record_opt_out") as mock_opt_out:
        result = await _recv_reply_node(state, session=session)

    assert result.get("opted_out") is False, (
        f"false-positive opt-out on legitimate reply: {message_text!r}"
    )
    mock_opt_out.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("message_text", ["sair", "Não", "  PARAR ", "stop", "Cancelar."])
async def test_opt_out_bare_keyword_variants(message_text: str) -> None:
    """CR-01: a bare opt-out keyword (any case, surrounding punctuation/space) opts out."""
    session = _make_session()
    state = _make_initial_state()
    state["message_text"] = message_text

    with patch("brave.compliance.consent_log.record_opt_out") as mock_opt_out:
        result = await _recv_reply_node(state, session=session)

    assert result.get("opted_out") is True, f"bare keyword not detected: {message_text!r}"
    mock_opt_out.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: Compliance gate blocks when Norteia not in params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_norteia_not_in_params_raises_compliance_error() -> None:
    """test_norteia_not_in_params_raises_compliance_error:
    Calling _compliant_send with params["body"] lacking "Norteia" raises ComplianceError.
    FakeWhatsAppClient.sent_messages is empty (send_template never called).
    """
    from brave.lanes.atrativos.whatsapp_agent import _compliant_send

    wa_client = FakeWhatsAppClient()
    redis = fakeredis.FakeRedis()
    rio = _make_rio(sub_state="whatsapp_in_progress")
    settings = _make_settings(approved_templates=["norteia_v1"])

    # Mock session with a consent_log row so gate condition 1 passes
    session = _make_session()
    consent_row = MagicMock()
    consent_row.opted_out = False
    session.scalar = MagicMock(return_value=consent_row)

    params_without_norteia = {"body": "Olá! Poderia confirmar seu horário?"}

    with pytest.raises(ComplianceError, match="Norteia"):
        await _compliant_send(
            session=session,
            redis_client=redis,
            rio=rio,
            wa_client=wa_client,
            contact_phone="+5573999999999",
            template_name="norteia_v1",
            params=params_without_norteia,
            settings=settings,
        )

    # send_template must NOT have been called
    assert wa_client.sent_messages == []


# ---------------------------------------------------------------------------
# Test 3: Extraction result validated by Pydantic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_result_validated_by_pydantic() -> None:
    """test_extraction_result_validated_by_pydantic:
    FakeLLMClient returns a valid ConversationExtractionResult.
    extract_answers_node produces extraction dict with existe='sim'.
    """
    valid_result = ConversationExtractionResult(
        existe="sim",
        funcionando="sim",
        horarios="9h-18h",
        valor=None,
        confidence=0.95,
    )
    llm_client = FakeLLMClient(fixture_result=valid_result)

    state = _make_initial_state()
    state["messages"] = [
        {"role": "assistant", "content": "Da Norteia: seu negócio está funcionando?"},
        {"role": "user", "content": "Sim, estamos funcionando das 9h às 18h!"},
    ]

    result = await _extract_answers_node(state, llm_client=llm_client)

    assert result["extraction"] is not None
    assert result["extraction"]["existe"] == "sim"
    assert result["extraction"]["funcionando"] == "sim"
    assert result["extraction"]["horarios"] == "9h-18h"
    assert result["extraction"]["confidence"] == 0.95
    # LLM extract was called once with mode="tools"
    assert len(llm_client.calls) == 1
    assert llm_client.calls[0]["mode"] == "tools"
    assert llm_client.calls[0]["schema"] == "ConversationExtractionResult"


# ---------------------------------------------------------------------------
# Test 4: Invalid schema → extraction quarantined
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_quarantines_invalid_schema() -> None:
    """test_extraction_quarantines_invalid_schema:
    FakeLLMClient raises ValidationError (simulates instructor retry exhaustion).
    extract_answers_node returns extraction=None (DLQ path, not an exception).
    """
    from pydantic import ValidationError

    class _BadResult:
        """Simulate a completely wrong return type from the LLM."""
        pass

    # Use raise_on_call to simulate instructor retry exhaustion
    llm_client = FakeLLMClient(
        raise_on_call=ValidationError.from_exception_data(
            title="ConversationExtractionResult",
            input_type="python",
            line_errors=[
                {
                    "type": "missing",
                    "loc": ("existe",),
                    "msg": "Field required",
                    "input": {},
                    "url": "https://errors.pydantic.dev/2/v/missing",
                }
            ],
        )
    )

    state = _make_initial_state()
    state["messages"] = [
        {"role": "user", "content": "Não sei responder"},
    ]

    result = await _extract_answers_node(state, llm_client=llm_client)

    # extraction=None — DLQ path, no exception propagated
    assert result["extraction"] is None


@pytest.mark.asyncio
async def test_extraction_quarantines_on_generic_exception() -> None:
    """extract_answers_node handles generic LLM errors gracefully (extraction=None)."""
    llm_client = FakeLLMClient(raise_on_call=RuntimeError("LLM timeout"))

    state = _make_initial_state()
    state["messages"] = [{"role": "user", "content": "..."}]

    result = await _extract_answers_node(state, llm_client=llm_client)
    assert result["extraction"] is None


# ---------------------------------------------------------------------------
# Test 5: build_graph returns a compiled LangGraph object
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_graph_returns_compiled_graph() -> None:
    """test_build_graph_returns_compiled_graph:
    build_graph(...) with MemorySaver returns a compiled LangGraph Pregel object.
    The compiled graph has .ainvoke() attribute.
    No real DB/Redis/Twilio needed.
    """
    from langgraph.checkpoint.memory import MemorySaver

    wa_client = FakeWhatsAppClient()
    llm_client = FakeLLMClient(
        fixture_result=ConversationExtractionResult(
            existe="sim", funcionando="sim", confidence=0.9
        )
    )
    session = _make_session()
    redis = fakeredis.FakeRedis()
    rio = _make_rio()
    from brave.config.settings import ScoreConfig, WhatsAppConfig
    config = ScoreConfig()
    settings = WhatsAppConfig()

    compiled = build_graph(
        wa_client=wa_client,
        llm_client=llm_client,
        session=session,
        redis_client=redis,
        rio=rio,
        config=config,
        settings=settings,
        checkpointer=MemorySaver(),
    )

    # Must be a compiled LangGraph graph
    assert hasattr(compiled, "ainvoke"), "build_graph must return a Pregel object with ainvoke"
    assert callable(compiled.ainvoke)


# ---------------------------------------------------------------------------
# Additional: OPT_OUT_KEYWORDS constant integrity check
# ---------------------------------------------------------------------------


def test_opt_out_keywords_constant() -> None:
    """Opt-out keyword sets cover the 6 required PT-BR/Meta opt-out keywords (CR-01).

    The unambiguous command keywords match as standalone tokens; "NÃO" is honored
    only as a full-message reply, so it lives in a separate set. The union
    (ALL_OPT_OUT_KEYWORDS) preserves the documented COMP-02 keyword list.
    """
    from brave.lanes.atrativos.whatsapp_agent import ALL_OPT_OUT_KEYWORDS

    # Internal match set (includes the accent-less "NAO" spelling)
    assert OPT_OUT_KEYWORDS == {"SAIR", "PARAR", "CANCELAR", "REMOVER", "STOP", "NÃO", "NAO"}
    assert isinstance(OPT_OUT_KEYWORDS, frozenset)
    # Documented COMP-02 set (canonical spelling)
    assert ALL_OPT_OUT_KEYWORDS == {"SAIR", "PARAR", "CANCELAR", "REMOVER", "STOP", "NÃO"}
