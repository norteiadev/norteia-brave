"""Nascente service — store_raw and get_nascente (D-04).

Immutable append-only store. Records are never updated in-place.
If the same source_ref arrives with a different payload, a new version
row is created and the old row's superseded_by_id is set (D-03).

SHA-256 content_hash over sorted-key JSON ensures deterministic dedup.
"""

import hashlib
import json
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.core.models import NascenteRecord
from brave.observability.record_events import record_event

logger = structlog.get_logger(__name__)


def store_raw(
    session: Session,
    source: str,
    source_ref: str,
    entity_type: str,
    uf: str,
    payload: dict[str, Any],
    *,
    timeline: list[dict[str, Any]] | None = None,
) -> NascenteRecord:
    """Ingest a raw payload into the Nascente layer.

    Idempotent: If a NascenteRecord with the same source, source_ref, and
    content_hash already exists, returns it without creating a duplicate.

    Supersession (D-03): If source_ref exists but with a different payload
    (different content_hash), creates a new version row (version += 1) and
    sets superseded_by_id on the old row to point to the new one.

    Args:
        session:    SQLAlchemy synchronous Session.
        source:     Data source identifier (e.g., "mtur", "places_discovery").
        source_ref: Unique source-scoped reference (e.g., "mtur:BA:12345").
        entity_type: "destination" or "attraction".
        uf:         Two-letter state code.
        payload:    Raw source payload (JSON-serializable dict).
        timeline:   Optional list of buffered success-stage RecordEvent kwargs
                    (each dict is the record_event(...) kwargs MINUS session /
                    nascente_id). Flushed ONLY when a NEW row is created, so the
                    caller's Log-tab success events sit behind the same content_hash
                    idempotency as the ingest event and do not re-emit on re-sweep.
                    The early-return (already-ingested) path stays silent.

    Returns:
        The NascenteRecord for this payload (existing or newly created).
    """
    # Compute deterministic content hash (D-04)
    content_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()

    # Stage 1: Check for exact match (idempotency)
    existing = session.scalar(
        select(NascenteRecord).where(
            NascenteRecord.source == source,
            NascenteRecord.source_ref == source_ref,
            NascenteRecord.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing

    # Stage 2: Check for same source_ref with different payload (supersession)
    latest = session.scalar(
        select(NascenteRecord).where(
            NascenteRecord.source == source,
            NascenteRecord.source_ref == source_ref,
            NascenteRecord.superseded_by_id.is_(None),  # Active (not yet superseded)
        )
    )

    new_version = 1
    if latest is not None:
        new_version = latest.version + 1

    # Create new row
    new_record = NascenteRecord(
        id=uuid.uuid4(),
        source=source,
        source_ref=source_ref,
        entity_type=entity_type,
        uf=uf,
        payload=payload,
        content_hash=content_hash,
        version=new_version,
    )
    session.add(new_record)
    session.flush()  # Assign ID before FK update

    # Set supersession pointer on old record (D-03)
    if latest is not None:
        latest.superseded_by_id = new_record.id
        session.flush()

    # Per-entity sync log — emitted only when a NEW or SUPERSEDED record is
    # created (the idempotent early-return above stays silent to avoid re-ingest
    # log spam). LGPD: public-geo fields only (name, uf, municipio, source).
    canonical = payload.get("canonical") or {}
    logger.info(
        "nascente_ingerido",
        source=source,
        entity_type=entity_type,
        uf=uf,
        name=(payload.get("name") or source_ref),
        municipio=canonical.get("municipio"),
    )

    # Flush the caller's buffered pre-ingest success-stage events FIRST, so the Log-tab
    # timeline reads in chronological order (synced → município → validado → ingested →
    # …). NEW-row branch only, so they inherit the same content_hash idempotency as the
    # 'ingested' event below and never re-emit on a re-sweep. clock_timestamp() gives each
    # a distinct created_at within the transaction. Each entry is record_event(...) kwargs
    # minus session / nascente_id (both supplied here).
    if timeline:
        for ev in timeline:
            record_event(session, nascente_id=new_record.id, **ev)

    # Append-only Log-tab timeline event. Placed BEHIND the idempotent early-return
    # (content_hash exact-match) so a re-sweep of an already-ingested payload does
    # NOT re-emit. LGPD: public-geo fields only (name/uf/municipio/source/version).
    record_event(
        session,
        source=source,
        source_ref=source_ref,
        stage="ingested",
        status="ok",
        message=(payload.get("name") or source_ref),
        entity_type=entity_type,
        uf=uf,
        nascente_id=new_record.id,
        data={"municipio": canonical.get("municipio"), "version": new_version},
    )

    return new_record


def get_nascente(
    session: Session,
    nascente_id: uuid.UUID,
) -> NascenteRecord | None:
    """Retrieve a NascenteRecord by primary key.

    Args:
        session:     SQLAlchemy synchronous Session.
        nascente_id: UUID primary key.

    Returns:
        NascenteRecord if found, None otherwise.
    """
    return session.get(NascenteRecord, nascente_id)
