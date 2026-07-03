"""Pure conversation primitives for the WhatsApp owner-validation graph (D-08).

This module is the dependency-free core of the WhatsApp conversation (Phase G
split out of ``whatsapp_agent.py``). It holds:

  - ``ConversationState`` — the LangGraph StateGraph state schema (TypedDict).
  - Opt-out keyword detection (COMP-02, CR-01) — ``OPT_OUT_KEYWORDS`` /
    ``ALL_OPT_OUT_KEYWORDS`` + ``_detect_opt_out_keyword``.
  - Post-node routing predicates — ``_after_recv_reply`` / ``_after_extract_answers``.

It imports NOTHING from ``brave.*`` (only stdlib), so it sits cleanly at the
bottom of ``brave.shared`` (D-18). The I/O-bearing graph nodes, ``_compliant_send``,
and ``build_graph`` live in the sibling ``agent`` module.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Opt-out keyword set (COMP-02, D-11, recv_reply node)
# ---------------------------------------------------------------------------

# BSP-standard opt-out keywords (COMP-02). A contact opts out by replying with
# the bare command word. CR-01: matching is anchored to the *whole message* —
# the stripped reply must consist solely of the keyword (or a short keyword
# phrase) — NOT an unanchored substring and NOT a keyword token buried in a
# longer sentence. This is what BSPs (Twilio/Meta) treat as a valid opt-out and
# avoids false positives on common PT-BR words ("não", "parar", "cancelar")
# that appear inside legitimate replies:
#   "NÃO sei o horário"               → not opt-out
#   "Não vamos parar de funcionar"    → not opt-out
#   "Pode cancelar minha dúvida"      → not opt-out
#   "SAIR" / "parar" / "  NÃO. "      → opt-out (message IS the keyword)
OPT_OUT_KEYWORDS: frozenset[str] = frozenset(
    {"SAIR", "PARAR", "CANCELAR", "REMOVER", "STOP", "NÃO", "NAO"}
)

# Public set documented in COMP-02 (kept stable for callers/tests). "NAO" is an
# accent-less spelling normalized to "NÃO" on match.
ALL_OPT_OUT_KEYWORDS: frozenset[str] = frozenset(
    {"SAIR", "PARAR", "CANCELAR", "REMOVER", "STOP", "NÃO"}
)

# Tokenizer for opt-out detection. Accents are part of the token alphabet so a
# keyword is never matched as a substring prefix of another word.
_WORD_RE = re.compile(r"[0-9A-ZÀ-ÖØ-Þ]+", re.UNICODE)

# A reply may carry a tiny amount of politeness around the bare command and
# still count as an opt-out (e.g. "sair por favor", "quero sair"). We allow the
# message to be the keyword alone, optionally surrounded by a few filler tokens,
# but the keyword must be the dominant content. Keep this conservative.
_OPT_OUT_FILLER: frozenset[str] = frozenset(
    {"POR", "FAVOR", "QUERO", "ME", "QUER", "PFV", "PLEASE", "OBRIGADO", "OBRIGADA"}
)


def _detect_opt_out_keyword(message_text: str) -> str | None:
    """Return the matched opt-out keyword, or None.

    CR-01: message-anchored matching, not substring containment.

    A reply opts the contact out only when, after dropping punctuation and a few
    politeness filler tokens, the *only* meaningful token is an opt-out keyword.
    This honors the BSP opt-out convention ("reply SAIR to stop") while never
    triggering on common PT-BR words embedded in a real answer.
    """
    tokens = _WORD_RE.findall(message_text.upper())
    if not tokens:
        return None

    meaningful = [t for t in tokens if t not in _OPT_OUT_FILLER]

    # Exactly one meaningful token, and it is an opt-out keyword.
    if len(meaningful) == 1 and meaningful[0] in OPT_OUT_KEYWORDS:
        return "NÃO" if meaningful[0] in ("NÃO", "NAO") else meaningful[0]

    return None


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
# Graph routing functions (pure — read state, return next node name)
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
