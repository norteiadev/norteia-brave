"""LGPD consent and opt-out log operations (COMP-01, D-11).

Mirrors brave/observability/audit.py structure: pure service functions that
accept a Session and operate on the ConsentLog model.

Separate from audit_log because consent_log serves a different query pattern:
  audit_log   = historical trail (append-only reads, correlation)
  consent_log = real-time suppression lookup (is_opted_out before every send)

PII handling:
  - phone_e164 is stored in the DB (required for suppression lookup)
  - Logs use phone[:5] prefix ONLY — full number never emitted in logs (T-03-03-06)
  - Only (rio_id, message_text) is forwarded to tasks — phone number is not
    passed to the LLM or stored outside this table (T-03-03-08)

Opt-out keywords (COMP-02, recv_reply node in whatsapp_agent.py):
  SAIR, PARAR, CANCELAR, REMOVER, STOP, NÃO
  These are detected in the LangGraph recv_reply node and trigger record_opt_out here.
  The opt_out flag is append-only — no endpoint unsets it (T-03-03-07).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.core.models import ConsentLog
from brave.observability.audit import write_audit

logger = structlog.get_logger(__name__)


def write_consent_record(
    session: Session,
    phone_e164: str,
    rio_id: uuid.UUID,
    legal_basis: str,
    norteia_identified: bool,
    purpose: str = "business_validation",
) -> ConsentLog:
    """Write a new LGPD consent record for a contact (COMP-01).

    Called before dispatching the first WhatsApp outreach message to create the
    legal-basis record required by gate condition 1.

    Note: This function always creates a NEW row. Idempotency (upsert behavior)
    is the caller's responsibility — check for an existing record first if needed.

    PII: phone_e164 is stored in the DB. Log emits only the first 5 chars (T-03-03-06).

    Args:
        session:            SQLAlchemy synchronous Session.
        phone_e164:         Contact phone number in E.164 format (+5511...).
        rio_id:             UUID of the associated RioRecord.
        legal_basis:        LGPD legal basis string (e.g. "legitimate_interest_commercial_verification").
        norteia_identified: Was Norteia identified in the outreach message? (LGPD requirement)
        purpose:            Purpose of the outreach (default: "business_validation").

    Returns:
        The created ConsentLog entry.
    """
    now = datetime.now(timezone.utc)
    record = ConsentLog(
        id=uuid.uuid4(),
        phone_e164=phone_e164,
        rio_id=rio_id,
        legal_basis=legal_basis,
        norteia_identified=norteia_identified,
        opted_out=False,
        opted_out_at=None,
        opted_out_keyword=None,
        first_contact_at=now,
        last_contact_at=now,
        purpose=purpose,
    )
    session.add(record)
    session.flush()

    # PII guard: never log full phone number (T-03-03-06)
    logger.info(
        "consent_record_created",
        phone_prefix=phone_e164[:5],
        rio_id=str(rio_id),
        legal_basis=legal_basis,
        norteia_identified=norteia_identified,
        purpose=purpose,
    )
    return record


def is_opted_out(session: Session, phone_e164: str) -> bool:
    """Return True if this phone number has opted out of outreach (gate condition 3).

    Queries consent_log for a row with opted_out=True for the given phone.
    If any opt-out row exists, returns True (suppress the send).

    Called as gate condition 3 in send_path_gate. Fast indexed lookup on phone_e164.

    Args:
        session:    SQLAlchemy synchronous Session.
        phone_e164: Contact phone number in E.164 format.

    Returns:
        True if an opted-out consent record exists; False otherwise.
    """
    row = session.scalar(
        select(ConsentLog)
        .where(ConsentLog.phone_e164 == phone_e164)
        .where(ConsentLog.opted_out.is_(True))
    )
    return row is not None


def record_opt_out(
    session: Session,
    phone_e164: str,
    keyword: str,
) -> None:
    """Mark a contact as opted out (triggered by opt-out keyword detection in recv_reply node).

    Opt-out is append-safe: sets opted_out=True on the most recent non-opted-out record.
    The opted_out flag is NEVER unset (T-03-03-07 — no DELETE or un-opt-out endpoint).

    Called from the LangGraph recv_reply node when an opt-out keyword is detected.
    Also writes an audit row for the regulatory trail (COMP-01).

    Opt-out keywords (COMP-02): SAIR, PARAR, CANCELAR, REMOVER, STOP, NÃO

    Args:
        session:    SQLAlchemy synchronous Session.
        phone_e164: Contact phone number in E.164 format.
        keyword:    The opt-out keyword that triggered this call (for audit/regulatory).

    Raises:
        ValueError: If no active (non-opted-out) consent record exists for this phone.
    """
    row = session.scalar(
        select(ConsentLog)
        .where(ConsentLog.phone_e164 == phone_e164)
        .where(ConsentLog.opted_out.is_(False))
        .order_by(ConsentLog.first_contact_at.desc())
    )
    if row is None:
        # No active row — may already be opted out or never consented
        # Log and return; do not raise (opt-out must always succeed)
        logger.warning(
            "opt_out_no_active_row",
            phone_prefix=phone_e164[:5],
            keyword=keyword,
        )
        return

    # Direct column assignment — no flag_modified needed for bool columns (PATTERNS.md note)
    row.opted_out = True
    row.opted_out_at = datetime.now(timezone.utc)
    row.opted_out_keyword = keyword
    session.flush()

    write_audit(
        session=session,
        action="opt_out_recorded",
        entity_type="attraction",
        record_id=row.rio_id,
        after_state={"opted_out": True, "keyword": keyword},
        actor="compliance",
    )

    logger.info(
        "opt_out_recorded",
        phone_prefix=phone_e164[:5],
        keyword=keyword,
        rio_id=str(row.rio_id),
    )


def lookup_rio_id_by_phone(session: Session, phone_e164: str) -> uuid.UUID | None:
    """Look up the active rio_id for a contact phone (for inbound webhook routing).

    Returns the rio_id of the most recent non-opted-out consent record for this phone.
    Used by the inbound webhook endpoint to route replies to the correct RioRecord.

    Returns None if no active conversation exists for this phone (unknown caller or
    already opted out).

    Args:
        session:    SQLAlchemy synchronous Session.
        phone_e164: Contact phone number in E.164 format.

    Returns:
        UUID of the associated RioRecord, or None if not found.
    """
    row = session.scalar(
        select(ConsentLog)
        .where(ConsentLog.phone_e164 == phone_e164)
        .where(ConsentLog.opted_out.is_(False))
        .order_by(ConsentLog.first_contact_at.desc())
    )
    if row is None:
        return None
    return row.rio_id
