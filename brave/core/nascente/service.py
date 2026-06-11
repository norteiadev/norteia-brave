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

from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.core.models import NascenteRecord


def store_raw(
    session: Session,
    source: str,
    source_ref: str,
    entity_type: str,
    uf: str,
    payload: dict[str, Any],
) -> NascenteRecord:
    """Ingest a raw payload into the Nascente layer.

    Idempotent: If a NascenteRecord with the same source, source_ref, and
    content_hash already exists, returns it without creating a duplicate.

    Supersession (D-03): If source_ref exists but with a different payload
    (different content_hash), creates a new version row (version += 1) and
    sets superseded_by_id on the old row to point to the new one.

    Args:
        session:    SQLAlchemy synchronous Session.
        source:     Data source identifier (e.g., "mtur", "notebooklm").
        source_ref: Unique source-scoped reference (e.g., "mtur:BA:12345").
        entity_type: "destination" or "attraction".
        uf:         Two-letter state code.
        payload:    Raw source payload (JSON-serializable dict).

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
