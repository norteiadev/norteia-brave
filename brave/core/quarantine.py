"""Core quarantine utility — importable by both brave/tasks/ and brave/lanes/ without
creating a tasks→lanes or lanes→tasks coupling (D-18).

quarantine_poison is extracted from brave/tasks/pipeline.py so that lane code
(e.g. producers under brave/lanes/) can write to PoisonQuarantine without
importing from the tasks layer.

D-18 boundary note: brave/core/ never imports from brave/lanes/ or brave/tasks/.
"""

import uuid

from sqlalchemy.orm import Session

from brave.core.models import PoisonQuarantine


def quarantine_poison(
    session: Session,
    nascente_id: uuid.UUID | None,
    task_name: str,
    error: str,
    payload: dict | None = None,
) -> PoisonQuarantine:
    """Insert a PoisonQuarantine row for a permanently failed task.

    This is DISTINCT from the §7.6 review DLQ (routing='dlq' on RioRecord).
    PoisonQuarantine = operational failure (Celery task failure or malformed LLM output).
    §7.6 DLQ = score gate routing for human review.

    Used by:
      - brave/tasks/pipeline.py (Celery task failures, re-exported via re-import)
      - lane producers under brave/lanes/ (e.g. malformed LLM output)

    Args:
        session:     SQLAlchemy Session.
        nascente_id: The nascente_id being processed (if known). None for lane-level failures
                     where no NascenteRecord was successfully created.
        task_name:   The task or agent name (e.g., "brave.process_nascente",
                     "brave.sweep_uf").
        error:       Error message or traceback summary.
        payload:     Optional payload dict for debugging (e.g., municipio context).

    Returns:
        The created PoisonQuarantine row (already flush()ed into session).
    """
    quarantine = PoisonQuarantine(
        id=uuid.uuid4(),
        nascente_id=nascente_id,
        task_name=task_name,
        error_message=error,
        payload=payload or {},
    )
    session.add(quarantine)
    session.flush()
    return quarantine
