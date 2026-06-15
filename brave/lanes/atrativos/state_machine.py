"""Sub-state FSM for the Atrativos lane (D-01, D-02).

advance_sub_state is the single entry point for all sub_state transitions.
It implements the idempotency guard (returns False if already past expected state),
writes an audit row on every transition, and sets the new sub_state.

D-01: Celery+Redis idempotent FSM — each transition is a separate Celery task;
      the task reads sub_state, asserts it matches, does work, advances sub_state.
D-02: sub_state is the single source of truth; transitions write audit rows.

D-18 boundary: only imports from brave.core — never from brave.lanes.destinos
or brave.tasks.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from brave.observability.audit import write_audit

if TYPE_CHECKING:
    from brave.core.models import RioRecord


def advance_sub_state(
    session: Session,
    rio: "RioRecord",
    expected_state: str | None,
    next_state: str | None,
    actor: str = "state_machine",
) -> bool:
    """Guard + advance the sub_state FSM for an atrativo RioRecord.

    Implements the idempotency guard from D-01: if rio.sub_state != expected_state,
    returns False immediately (safe replay — already advanced or wrong state).
    On advance: writes an AuditLog row (D-02) and sets rio.sub_state = next_state.

    Args:
        session:        SQLAlchemy synchronous Session.
        rio:            RioRecord whose sub_state is being advanced.
        expected_state: The state the record must be in for this transition to proceed.
                        None means the FSM is being reset (e.g. hard descarte).
        next_state:     The state to advance to. None means FSM reset (terminal).
        actor:          Name of the agent / component driving this transition.
                        Recorded in the audit row (D-02).

    Returns:
        True if the transition was applied; False if sub_state != expected_state
        (idempotency guard fired — already advanced or wrong state).
    """
    if rio.sub_state != expected_state:
        return False  # Already advanced or wrong state — idempotent no-op

    write_audit(
        session=session,
        action="sub_state_advanced",
        entity_type="attraction",
        record_id=rio.id if isinstance(rio.id, uuid.UUID) else None,
        before_state={"sub_state": expected_state},
        after_state={"sub_state": next_state},
        actor=actor,
    )

    rio.sub_state = next_state
    session.flush()
    return True
