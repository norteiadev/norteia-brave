"""Per-record event logging — record_event function (Log tab timeline).

Writes RecordEvent rows for each stage a record passes through the Brave
pipeline (TripAdvisor synced → município resolved → validated → ingested →
deduped → scored → routed, or a terminal ``quarantined`` on failure). Powers the
drawer "Log" tab. Mirrors ``brave.observability.audit.write_audit`` (insert +
session.flush() + structlog).

Append-only: callers emit alongside the existing pipeline emission points and
ALWAYS behind the idempotency early-returns (``store_raw`` content_hash /
``process_nascente_record`` canonical_key), so a re-sweep of an already-ingested
record does not re-emit DB-stage events.

Stage vocabulary:
  tripadvisor_synced, review_enriched, municipio_resolved, geo_enriched,
  parent_destino_linked, validated, ingested, deduped, scored, routed,
  quarantined.

Status vocabulary: 'ok' | 'fail' | 'skip'.

LGPD: ``data`` carries ONLY public-geo + engineering fields (score, routing,
dlq_reason, IBGE reason, name/uf, locationId) — NEVER a phone, PII, review text,
or a username. The structlog line likewise emits only stage/status/source_ref
(public-geo/engineering) — never raw payload content.
"""

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.core.models import RecordEvent

logger = structlog.get_logger(__name__)


def record_event(
    session: Session,
    *,
    source: str,
    source_ref: str,
    stage: str,
    status: str,
    message: str | None = None,
    entity_type: str | None = None,
    uf: str | None = None,
    nascente_id: uuid.UUID | None = None,
    rio_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
) -> RecordEvent:
    """Write a RecordEvent row for one pipeline stage of one record.

    Also emits a structlog JSON entry for log correlation. The RecordEvent row
    is written to the DB (on the caller's session — never a separate one); the
    structlog entry goes to stdout/file.

    Args:
        session:     SQLAlchemy synchronous Session (the caller's — this appends
                     to it and flushes; the caller owns the commit).
        source:      Collection lane / source slug (e.g. "tripadvisor", "mtur").
        source_ref:  Universal drawer key. For a TA attraction:
                     "tripadvisor:attraction:{locationId}" (== RioRecord.canonical_key).
        stage:       Pipeline stage (see module docstring vocabulary).
        status:      "ok" | "fail" | "skip".
        message:     Short human-readable note (e.g. the attraction name, or the
                     ibge_unmatched reason). NEVER PII/review text.
        entity_type: "attraction" | "destination" (optional).
        uf:          Two-letter Brazilian state code (optional, public-geo).
        nascente_id: UUID of the NascenteRecord once it exists (optional).
        rio_id:      UUID of the RioRecord once it exists (optional).
        data:        Public-geo + engineering fields only (score, routing,
                     dlq_reason, IBGE reason, name/uf, locationId). NEVER PII.

    Returns:
        The created RecordEvent row (already flushed).
    """
    event = RecordEvent(
        id=uuid.uuid4(),
        source=source,
        source_ref=source_ref,
        entity_type=entity_type,
        uf=uf,
        nascente_id=nascente_id,
        rio_id=rio_id,
        stage=stage,
        status=status,
        message=message,
        data=data,
    )
    session.add(event)
    session.flush()

    # Emit structlog JSON entry for log correlation.
    # NOTE: Only public-geo / engineering fields — never raw payload content / PII.
    logger.info(
        "record_event",
        stage=stage,
        status=status,
        source_ref=source_ref,
    )

    return event


def record_event_once(
    session: Session,
    *,
    source: str,
    source_ref: str,
    stage: str,
    status: str,
    message: str | None = None,
    entity_type: str | None = None,
    uf: str | None = None,
    nascente_id: uuid.UUID | None = None,
    rio_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
) -> RecordEvent | None:
    """Idempotent terminal-event variant of ``record_event``.

    Inserts a RecordEvent only when no row already matches
    (source_ref, stage, status). This prevents re-emitting a terminal
    ``quarantined`` event on every re-sweep for a persistently-failing card (a
    card that never resolves would otherwise duplicate its failure step forever,
    since terminal failures are NOT behind ``store_raw``'s content_hash
    early-return like the success-stage events are).

    Args:
        Same as ``record_event`` (see its docstring). The identity check is on
        (source_ref, stage, status) — the terminal-failure key.

    Returns:
        The created RecordEvent, or None when a matching row already exists
        (insert skipped).
    """
    existing = session.scalar(
        select(RecordEvent).where(
            RecordEvent.source_ref == source_ref,
            RecordEvent.stage == stage,
            RecordEvent.status == status,
        )
    )
    if existing is not None:
        return None

    return record_event(
        session,
        source=source,
        source_ref=source_ref,
        stage=stage,
        status=status,
        message=message,
        entity_type=entity_type,
        uf=uf,
        nascente_id=nascente_id,
        rio_id=rio_id,
        data=data,
    )
