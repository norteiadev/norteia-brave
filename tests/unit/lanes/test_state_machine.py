"""Unit tests for the atrativos sub-state FSM guard (D-01, D-02, CR-04).

The row-level lock path (with_for_update) is exercised by the integration suite
against a real Postgres. These unit tests cover the guard semantics with
lock=False (mock session) and assert the lock path re-fetches under FOR UPDATE.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from brave.lanes.atrativos.state_machine import (
    ATRATIVO_SUB_STATE_EDGES,
    advance_sub_state,
    is_allowed_sub_state_edge,
)


def _rio(sub_state: str | None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), sub_state=sub_state)


def test_advance_applies_transition_when_state_matches() -> None:
    session = MagicMock()
    rio = _rio("aguardando_consulta_whatsapp")

    applied = advance_sub_state(
        session,
        rio,
        expected_state="aguardando_consulta_whatsapp",
        next_state="whatsapp_in_progress",
        lock=False,
    )

    assert applied is True
    assert rio.sub_state == "whatsapp_in_progress"


def test_advance_is_noop_when_state_already_advanced() -> None:
    """D-01 idempotency: wrong/advanced state → no-op, no write."""
    session = MagicMock()
    rio = _rio("whatsapp_in_progress")

    applied = advance_sub_state(
        session,
        rio,
        expected_state="aguardando_consulta_whatsapp",
        next_state="whatsapp_in_progress",
        lock=False,
    )

    assert applied is False
    assert rio.sub_state == "whatsapp_in_progress"


def test_advance_locks_row_for_update_when_lock_true() -> None:
    """CR-04: the guard re-fetches the row with with_for_update=True."""
    session = MagicMock()
    rio = _rio("aguardando_consulta_whatsapp")
    # session.get returns the same logical row (locked)
    session.get.return_value = rio

    applied = advance_sub_state(
        session,
        rio,
        expected_state="aguardando_consulta_whatsapp",
        next_state="whatsapp_in_progress",
        lock=True,
    )

    assert applied is True
    # The lock fetch must request a row-level lock.
    assert session.get.called
    _, kwargs = session.get.call_args
    assert kwargs.get("with_for_update") is True


# ---------------------------------------------------------------------------
# Phase F: manual WhatsApp-gate edges (dlq <-> aguardando_consulta_whatsapp)
# ---------------------------------------------------------------------------
#
# DLQ = routing="dlq" with sub_state=None, so the two moves are expressed against
# sub_state None (advance_sub_state mutates sub_state only; callers set routing).


def test_manual_move_in_edge_is_allowed() -> None:
    """dlq -> aguardando_consulta_whatsapp (steward manual move into the gate queue)."""
    assert is_allowed_sub_state_edge(None, "aguardando_consulta_whatsapp") is True
    assert (None, "aguardando_consulta_whatsapp") in ATRATIVO_SUB_STATE_EDGES


def test_bounce_back_edge_is_allowed() -> None:
    """aguardando_consulta_whatsapp -> dlq (no contact found / bounce back)."""
    assert is_allowed_sub_state_edge("aguardando_consulta_whatsapp", None) is True
    assert ("aguardando_consulta_whatsapp", None) in ATRATIVO_SUB_STATE_EDGES


def test_existing_gate_approve_edge_preserved() -> None:
    """Regression: the aguardando -> whatsapp_in_progress edge (gate approve) survives."""
    assert is_allowed_sub_state_edge(
        "aguardando_consulta_whatsapp", "whatsapp_in_progress"
    ) is True


def test_advance_applies_manual_move_in_with_validate() -> None:
    session = MagicMock()
    rio = _rio(None)  # DLQ record: sub_state cleared

    applied = advance_sub_state(
        session,
        rio,
        expected_state=None,
        next_state="aguardando_consulta_whatsapp",
        lock=False,
        validate=True,
    )

    assert applied is True
    assert rio.sub_state == "aguardando_consulta_whatsapp"


def test_advance_applies_bounce_back_with_validate() -> None:
    session = MagicMock()
    rio = _rio("aguardando_consulta_whatsapp")

    applied = advance_sub_state(
        session,
        rio,
        expected_state="aguardando_consulta_whatsapp",
        next_state=None,
        lock=False,
        validate=True,
    )

    assert applied is True
    assert rio.sub_state is None


def test_validate_rejects_unknown_edge() -> None:
    """validate=True raises on an edge that is not in ATRATIVO_SUB_STATE_EDGES."""
    session = MagicMock()
    rio = _rio("discovered")

    with pytest.raises(ValueError, match="unsupported atrativo sub_state edge"):
        advance_sub_state(
            session,
            rio,
            expected_state="discovered",
            next_state="whatsapp_in_progress",  # not a real edge
            lock=False,
            validate=True,
        )
    # The record must not have been mutated.
    assert rio.sub_state == "discovered"


def test_validate_false_default_preserves_generic_behavior() -> None:
    """Existing callers (validate=False) keep the historical generic FSM behaviour."""
    session = MagicMock()
    rio = _rio("discovered")

    applied = advance_sub_state(
        session,
        rio,
        expected_state="discovered",
        next_state="whatsapp_in_progress",  # not an allow-listed edge
        lock=False,
    )

    assert applied is True
    assert rio.sub_state == "whatsapp_in_progress"
