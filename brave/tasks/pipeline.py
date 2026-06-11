"""Celery pipeline tasks (D-05, D-06, CORE-10).

Three tasks:
  process_nascente      — ingest NascenteRecord through Rio pipeline
  push_mar              — push scored RioRecord to Mar layer
  reprocess_record_task — re-score an existing RioRecord

Idempotency: Every task is a no-op on re-run (D-03, D-15).
Poison quarantine: After max_retries failures, the task goes to PoisonQuarantine,
                   NOT to the review DLQ (see PITFALLS §7, T-02-02).

Error classification:
  TransientError (network flap, DB timeout) → self.retry with backoff
  PermanentError (malformed payload, schema violation) → quarantine_poison
  Any exception after max_retries → quarantine_poison
"""

import uuid
from typing import Any

from celery import shared_task
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session

from brave.core.models import PoisonQuarantine, RioRecord
from brave.core.nascente.service import get_nascente
from brave.core.rio.routing import process_nascente_record, reprocess_record
from brave.config.settings import AppConfig, DBConfig, ScoreConfig

import os


# ---------------------------------------------------------------------------
# Exceptions for error classification
# ---------------------------------------------------------------------------


class TransientError(Exception):
    """Transient failure — retry with backoff (network, DB timeout, etc.)."""


class PermanentError(Exception):
    """Permanent failure — quarantine, do not retry (malformed payload, etc.)."""


# ---------------------------------------------------------------------------
# Session factory (lazy — resolved at task call time, not import time)
# ---------------------------------------------------------------------------


def _get_session() -> tuple[Session, Any]:
    """Create a synchronous SQLAlchemy session from environment config.

    Returns (session, engine) pair; caller must close both.
    """
    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        raise PermanentError("BRAVE_DB_URL not set — cannot create DB session")
    engine = create_engine(db_url, echo=False)
    SessionFactory = sessionmaker(bind=engine)
    return SessionFactory(), engine


# ---------------------------------------------------------------------------
# Poison quarantine helper
# ---------------------------------------------------------------------------


def quarantine_poison(
    session: Session,
    nascente_id: uuid.UUID | None,
    task_name: str,
    error: str,
    payload: dict | None = None,
) -> PoisonQuarantine:
    """Insert a PoisonQuarantine row for a permanently failed task.

    This is DISTINCT from the §7.6 review DLQ (routing='dlq' on RioRecord).
    PoisonQuarantine = Celery operational failure.
    §7.6 DLQ = score gate routing for human review.

    Args:
        session:     SQLAlchemy Session.
        nascente_id: The nascente_id being processed (if known).
        task_name:   The Celery task name (e.g., "brave.process_nascente").
        error:       Error message or traceback summary.
        payload:     Optional payload dict for debugging.

    Returns:
        The created PoisonQuarantine row.
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


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.process_nascente",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def process_nascente(self, nascente_id: str) -> None:
    """Process a NascenteRecord through the Rio pipeline.

    Idempotent: If a RioRecord already exists for this nascente_id, returns
    immediately without re-processing.

    Error handling:
      TransientError → retry (up to max_retries=3)
      PermanentError or unhandled after max_retries → quarantine_poison

    Args:
        nascente_id: UUID string of the NascenteRecord to process.
    """
    session, engine = _get_session()
    try:
        nascente_uuid = uuid.UUID(nascente_id)
        config = ScoreConfig()

        nascente = get_nascente(session, nascente_uuid)
        if nascente is None:
            raise PermanentError(f"NascenteRecord {nascente_id} not found")

        # Idempotency check: RioRecord with matching canonical_key
        canonical_key = nascente.source_ref
        existing = session.scalar(
            select(RioRecord).where(RioRecord.canonical_key == canonical_key)
        )
        if existing is not None:
            return  # Already processed — idempotent no-op

        process_nascente_record(session, nascente, config)
        session.commit()

    except PermanentError as exc:
        session.rollback()
        # Re-open session for quarantine write
        q_session, q_engine = _get_session()
        try:
            quarantine_poison(
                session=q_session,
                nascente_id=uuid.UUID(nascente_id) if nascente_id else None,
                task_name="brave.process_nascente",
                error=str(exc),
            )
            q_session.commit()
        finally:
            q_session.close()
            q_engine.dispose()

    except Exception as exc:
        session.rollback()
        try:
            # Retry transient errors
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            # After max_retries, quarantine
            q_session, q_engine = _get_session()
            try:
                quarantine_poison(
                    session=q_session,
                    nascente_id=uuid.UUID(nascente_id) if nascente_id else None,
                    task_name="brave.process_nascente",
                    error=str(exc),
                )
                q_session.commit()
            finally:
                q_session.close()
                q_engine.dispose()

    finally:
        session.close()
        engine.dispose()


@shared_task(
    bind=True,
    max_retries=3,
    name="brave.push_mar",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def push_mar(self, rio_id: str) -> None:
    """Push a scored RioRecord to the Mar layer.

    Idempotent: If routing != 'mar', returns immediately (nothing to push).
    Real NorteiaApi push deferred to Plan 03; Phase 1 records intent via audit log only.

    Args:
        rio_id: UUID string of the RioRecord to push.
    """
    session, engine = _get_session()
    try:
        rio_uuid = uuid.UUID(rio_id)
        rio = session.get(RioRecord, rio_uuid)
        if rio is None:
            raise PermanentError(f"RioRecord {rio_id} not found")

        # Idempotency: only process mar-routed records
        if rio.routing != "mar":
            return  # Not ready for Mar — idempotent no-op

        # Phase 1: promote to Mar layer (real push to norteia-api in Plan 03)
        from brave.core.mar.service import promote_to_mar
        promote_to_mar(session, rio)
        session.commit()

    except PermanentError:
        session.rollback()
        # No quarantine for push_mar permanent errors in Phase 1 — log and return
        pass

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            pass  # Non-critical in Phase 1; Phase 3 adds DLQ for failed pushes

    finally:
        session.close()
        engine.dispose()


@shared_task(
    bind=True,
    name="brave.reprocess_record",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def reprocess_record_task(self, rio_id: str) -> None:
    """Re-score an existing RioRecord (reset → re-route).

    Idempotent: re-running with the same config produces the same result.

    Args:
        rio_id: UUID string of the RioRecord to reprocess.
    """
    session, engine = _get_session()
    try:
        config = ScoreConfig()
        reprocess_record(session, uuid.UUID(rio_id), config)
        session.commit()
    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            pass
    finally:
        session.close()
        engine.dispose()
