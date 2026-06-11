"""Mar service — promote_to_mar, reopen_from_error_report (D-03, D-15).

promote_to_mar:
  Idempotent Mar push keyed by source_ref (D-15).
  If the MarRecord already exists, update reliability_score and supersede old row (D-03).
  Carries full per-criterion provenance/lineage (D-06).

reopen_from_error_report (CNTR-02):
  Community error reports reopen a published record back into the review DLQ.
  Locates active MarRecord by source_ref, resets its linked RioRecord to routing='dlq'.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.core.models import MarRecord, RioRecord


def promote_to_mar(
    session: Session,
    rio_record: RioRecord,
) -> MarRecord:
    """Create or update a MarRecord for a scored RioRecord.

    Idempotent: If a MarRecord with the same source_ref already exists,
    update reliability_score and supersede the old row (D-03, D-15).

    Provenance carries full per-criterion §7.6 breakdown (D-06).

    Args:
        session:    SQLAlchemy synchronous Session.
        rio_record: Scored RioRecord (must have routing='mar').

    Returns:
        The MarRecord (existing updated, or newly created).
    """
    source_ref = rio_record.canonical_key or str(rio_record.id)
    normalized = rio_record.normalized or {}
    entity_type = rio_record.entity_type

    # Build canonical payload from normalized record
    canonical: dict[str, Any] = {
        k: v for k, v in normalized.items()
        if k not in ("origem_value", "completude_value", "corroboracao_value",
                     "atualidade_value", "validacao_humana_value")
    }

    # Build provenance (D-06) — full per-criterion breakdown + score_version
    provenance: dict[str, Any] = {
        "score_breakdown": rio_record.score_breakdown or {},
        "score_version": rio_record.score_version or "v1.0",
        "nascente_id": str(rio_record.nascente_id),
        "rio_id": str(rio_record.id),
    }

    reliability_score = float(rio_record.score or 0.0)
    score_version = rio_record.score_version or "v1.0"

    # Check for existing active MarRecord (D-15)
    existing = session.scalar(
        select(MarRecord).where(
            MarRecord.source_ref == source_ref,
            MarRecord.superseded_by_id.is_(None),  # Active record
        )
    )

    if existing is not None:
        # Idempotent no-op (D-15): re-promoting unchanged data (e.g. a CLI
        # re-run or a reprocess that did not change the score) returns the
        # existing active row without writing — "re-push is a no-op upsert".
        if (
            float(existing.reliability_score) == reliability_score
            and existing.canonical == canonical
            and existing.provenance == provenance
            and existing.score_version == score_version
        ):
            return existing

        # Supersession pattern (D-03): the data changed (e.g. re-score after
        # human validation). Append a NEW active row and mark the old one
        # superseded BEFORE the single flush — at flush only the new row is
        # active, so the partial unique index uq_mar_active_source_ref holds.
        new_mar = MarRecord(
            id=uuid.uuid4(),
            rio_id=rio_record.id,
            entity_type=entity_type,
            source_ref=source_ref,
            canonical=canonical,
            provenance=provenance,
            reliability_score=reliability_score,
            score_version=score_version,
            parent_mar_id=existing.id,
        )
        existing.superseded_by_id = new_mar.id
        session.add(new_mar)
        session.flush()
        return new_mar

    # First-time creation
    mar = MarRecord(
        id=uuid.uuid4(),
        rio_id=rio_record.id,
        entity_type=entity_type,
        source_ref=source_ref,
        canonical=canonical,
        provenance=provenance,
        reliability_score=reliability_score,
        score_version=score_version,
    )
    session.add(mar)
    session.flush()
    return mar


def reopen_from_error_report(
    session: Session,
    source_ref: str,
) -> RioRecord | None:
    """Reopen a published MarRecord back into the review DLQ (CNTR-02).

    Finds the active MarRecord by source_ref, then resets its linked
    RioRecord to routing='dlq' with dlq_reason='community_error_report'.

    Args:
        session:    SQLAlchemy synchronous Session.
        source_ref: Source reference identifying the published Mar record.

    Returns:
        The DLQ'd RioRecord, or None if source_ref not found in Mar.
    """
    mar = session.scalar(
        select(MarRecord).where(
            MarRecord.source_ref == source_ref,
            MarRecord.superseded_by_id.is_(None),  # Active record
        )
    )
    if mar is None:
        return None

    rio = session.get(RioRecord, mar.rio_id)
    if rio is None:
        return None

    # Reset RioRecord to DLQ for community review
    rio.routing = "dlq"
    rio.dlq_reason = "community_error_report"
    session.flush()

    return rio
