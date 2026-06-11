"""Audit logging — write_audit function (D-21, OBS-04).

Writes AuditLog rows for steward decisions and pipeline actions.
Also emits structlog JSON log entries for correlation with llm_generations.

Audit events:
  - pipeline actions: "nascente_ingested", "rio_routed", "mar_promoted", "record_quarantined"
  - steward actions: "dlq_approved", "dlq_rejected", "dlq_reprocessed", "error_report_received"
"""

import uuid
from typing import Any

import structlog
from sqlalchemy.orm import Session

from brave.core.models import AuditLog

logger = structlog.get_logger(__name__)


def write_audit(
    session: Session,
    action: str,
    entity_type: str | None = None,
    record_id: uuid.UUID | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    actor: str = "pipeline",
) -> AuditLog:
    """Write an AuditLog entry for a steward or pipeline action.

    Also emits a structlog JSON log entry for log correlation.
    The AuditLog row is written to the DB; the structlog entry goes to stdout/file.

    Args:
        session:      SQLAlchemy synchronous Session.
        action:       Action identifier (e.g., "dlq_approved", "rio_routed").
        entity_type:  "destination" or "attraction" (optional).
        record_id:    UUID of the affected record (optional).
        before_state: State snapshot before the action (optional).
        after_state:  State snapshot after the action (optional).
        actor:        Who performed the action ("pipeline", "steward", or username).

    Returns:
        The created AuditLog entry.
    """
    audit = AuditLog(
        id=uuid.uuid4(),
        action=action,
        entity_type=entity_type,
        record_id=record_id,
        before_state=before_state,
        after_state=after_state,
        actor=actor,
    )
    session.add(audit)
    session.flush()

    # Emit structlog JSON entry for log correlation (OBS-04)
    # NOTE: Do NOT include raw payload content — potential PII
    log_data: dict[str, Any] = {
        "audit_id": str(audit.id),
        "action": action,
        "actor": actor,
    }
    if entity_type:
        log_data["entity_type"] = entity_type
    if record_id:
        log_data["record_id"] = str(record_id)

    logger.info("audit_event", **log_data)

    return audit
