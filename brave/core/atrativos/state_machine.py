"""Sub-state FSM for the Atrativos lane (D-01, D-02).

advance_sub_state is the single entry point for all sub_state transitions.
It implements the idempotency guard (returns False if already past expected state),
writes an audit row on every transition, and sets the new sub_state.

D-01: Celery+Redis idempotent FSM — each transition is a separate Celery task;
      the task reads sub_state, asserts it matches, does work, advances sub_state.
D-02: sub_state is the single source of truth; transitions write audit rows.

D-18 boundary: this module lives under brave.core (Phase G kernel move). It only
imports from brave.core and brave.observability — never from brave.lanes,
brave.domains, or brave.tasks.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from brave.observability.audit import write_audit

if TYPE_CHECKING:
    from brave.core.models import RioRecord


# ---------------------------------------------------------------------------
# Canonical atrativos sub_state FSM edges (D-01, D-02)
# ---------------------------------------------------------------------------
#
# DLQ is NOT a sub_state — it is routing="dlq" with sub_state=None. The two Phase F
# WhatsApp-gate moves are therefore expressed against sub_state None:
#   (None -> "aguardando_consulta_whatsapp") : dlq -> gate  (steward MANUAL move in)
#   ("aguardando_consulta_whatsapp" -> None) : gate -> dlq  (no contact / bounce back)
# advance_sub_state mutates sub_state ONLY — the caller must set routing separately
# (the gate queue query keys on sub_state, so routing must be updated in the same txn).
ATRATIVO_SUB_STATE_EDGES: frozenset[tuple[str | None, str | None]] = frozenset(
    {
        # Forward discovery → gate FSM.
        (None, "discovered"),
        ("discovered", "contacts_found"),
        ("contacts_found", "signals_gathered"),
        # Description-enrichment step (post-Signal): fetch MD → Norteia-voice rewrite →
        # descricao_editorial → re-score. Inserted between signals_gathered and the gate.
        ("signals_gathered", "description_enriched"),
        ("description_enriched", "aguardando_consulta_whatsapp"),
        ("description_enriched", None),  # re-score → dlq (bounce back to DLQ)
        ("signals_gathered", "aguardando_consulta_whatsapp"),
        ("aguardando_consulta_whatsapp", "whatsapp_in_progress"),
        # Phase F manual WhatsApp gate moves (DLQ = routing="dlq", sub_state=None):
        (None, "aguardando_consulta_whatsapp"),   # dlq -> gate (manual move in)
        ("aguardando_consulta_whatsapp", None),   # gate -> dlq (no contact / bounce back)
        # outreach found no phone → back to DLQ.
        ("whatsapp_in_progress", None),
    }
)


def is_allowed_sub_state_edge(
    expected_state: str | None,
    next_state: str | None,
) -> bool:
    """True iff (expected_state, next_state) is a canonical atrativos FSM edge."""
    return (expected_state, next_state) in ATRATIVO_SUB_STATE_EDGES


def advance_sub_state(
    session: Session,
    rio: RioRecord,
    expected_state: str | None,
    next_state: str | None,
    actor: str = "state_machine",
    lock: bool = True,
    validate: bool = False,
) -> bool:
    """Guard + advance the sub_state FSM for an atrativo RioRecord.

    Implements the idempotency guard from D-01: if rio.sub_state != expected_state,
    returns False immediately (safe replay — already advanced or wrong state).
    On advance: writes an AuditLog row (D-02) and sets rio.sub_state = next_state.

    CR-04 concurrency: the bare ``if rio.sub_state != expected_state`` check is not
    safe under concurrent inbound webhooks for the same rio_id — two tasks can both
    read the same state and both advance/send. When ``lock`` is True (default) the
    row is re-fetched with ``SELECT ... FOR UPDATE`` BEFORE the guard, so the guard
    and the write happen inside a single row-level lock. The second concurrent
    caller blocks on the lock, then re-reads the already-advanced state and returns
    False. ``lock=False`` is provided only for unit tests / mock sessions that do
    not support row locking.

    Args:
        session:        SQLAlchemy synchronous Session.
        rio:            RioRecord whose sub_state is being advanced.
        expected_state: The state the record must be in for this transition to proceed.
                        None means the FSM is being reset (e.g. hard descarte).
        next_state:     The state to advance to. None means FSM reset (terminal).
        actor:          Name of the agent / component driving this transition.
                        Recorded in the audit row (D-02).
        lock:           Acquire a row-level lock (SELECT ... FOR UPDATE) before the
                        guard (default True). Disable only for mock/unit sessions.
        validate:       When True, assert (expected_state, next_state) is a canonical
                        FSM edge (ATRATIVO_SUB_STATE_EDGES) and raise ValueError on an
                        unknown edge — the server-side guard for the Phase F manual
                        WhatsApp-gate moves. Default False preserves the historical
                        generic behaviour for existing callers.

    Returns:
        True if the transition was applied; False if sub_state != expected_state
        (idempotency guard fired — already advanced or wrong state).

    Raises:
        ValueError: When validate=True and (expected_state, next_state) is not an
        allowed edge in ATRATIVO_SUB_STATE_EDGES.
    """
    if validate and not is_allowed_sub_state_edge(expected_state, next_state):
        raise ValueError(
            f"unsupported atrativo sub_state edge: "
            f"{expected_state!r} -> {next_state!r}"
        )

    if lock:
        from brave.core.models import RioRecord

        # Re-fetch the same row under a row-level lock so the guard below and the
        # write are serialized against concurrent FSM-advancing tasks (CR-04).
        locked = session.get(RioRecord, rio.id, with_for_update=True)
        if locked is not None:
            rio = locked

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
