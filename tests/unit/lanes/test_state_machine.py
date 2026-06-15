"""Unit tests for the atrativos sub-state FSM guard (D-01, D-02, CR-04).

The row-level lock path (with_for_update) is exercised by the integration suite
against a real Postgres. These unit tests cover the guard semantics with
lock=False (mock session) and assert the lock path re-fetches under FOR UPDATE.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from brave.lanes.atrativos.state_machine import advance_sub_state


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
