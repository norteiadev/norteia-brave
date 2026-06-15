"""Rio routing — §7.6 score, routing column, and pipeline orchestration (D-02, D-12, D-13).

route_by_score:          Score a RioRecord and set its routing column.
process_nascente_record: Full Rio pipeline (dedup → normalize → label → route).
reprocess_record:        Re-score an existing RioRecord (reset → re-route).
reprocess_record_inline: Pure in-memory reprocess (no DB session required; for unit tests).
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.models import NascenteRecord, RioRecord
from brave.core.rio.dedup import compute_embedding, find_duplicate
from brave.core.rio.label import label_entity
from brave.core.rio.normalize import normalize_address, normalize_coordinates, normalize_name
from brave.core.score.engine import compute_score
from brave.core.score.schemas import ScoreInput


def route_by_score(
    session: Session | None,
    rio_record: RioRecord,
    config: ScoreConfig,
) -> RioRecord:
    """Apply §7.6 score to a RioRecord and set its routing column.

    Reads scoring inputs from rio_record.normalized dict.
    Sets: routing, score, score_breakdown, score_version, processed_at.

    Note: session is accepted for future SELECT FOR UPDATE locking on state
    transitions (production use); unit tests pass None.

    Args:
        session:    SQLAlchemy Session (used for SELECT FOR UPDATE in production).
                    None is accepted for unit tests.
        rio_record: The RioRecord to score and route.
        config:     ScoreConfig with §7.6 weights and thresholds.

    Returns:
        The updated RioRecord (mutated in-place, also returned for chaining).
    """
    normalized = rio_record.normalized or {}

    # Build ScoreInput from the normalized dict (with safe defaults)
    score_input = ScoreInput(
        origem_value=float(normalized.get("origem_value", 0.0)),
        completude_value=float(normalized.get("completude_value", 0.0)),
        corroboracao_value=float(normalized.get("corroboracao_value", 0.0)),
        atualidade_value=float(normalized.get("atualidade_value", 0.0)),
        validacao_humana_value=float(normalized.get("validacao_humana_value", 0.0)),
    )

    result = compute_score(score_input, config)

    # Mutate RioRecord fields
    rio_record.score = result.score
    rio_record.routing = result.routing
    rio_record.score_version = result.score_version
    rio_record.score_breakdown = {
        "origem": result.breakdown.origem,
        "completude": result.breakdown.completude,
        "corroboracao": result.breakdown.corroboracao,
        "atualidade": result.breakdown.atualidade,
        "validacao_humana": result.breakdown.validacao_humana,
    }
    rio_record.processed_at = datetime.now(timezone.utc)

    # Set dlq_reason when routing to DLQ
    if result.routing == "dlq":
        rio_record.dlq_reason = (
            f"score={result.score:.2f} below threshold_mar={config.threshold_mar}"
        )
    else:
        rio_record.dlq_reason = None

    return rio_record


def process_nascente_record(
    session: Session,
    nascente: NascenteRecord,
    config: ScoreConfig,
    llm_client: Any = None,  # LLMClientProtocol | None
) -> RioRecord:
    """Full Rio pipeline: dedup → normalize → label → route.

    Idempotent: If a RioRecord already exists for this nascente_id (via canonical_key),
    returns the existing record without re-processing.

    Pipeline steps (D-07):
    1. Exact content_hash dedup
    2. Territorial-key-blocked pgvector fuzzy dedup
    3. Normalize names/coordinates/addresses
    4. Label with Norteia taxonomy (Phase 1 stub)
    5. Score via §7.6 pure function
    6. Route to mar/dlq/descarte

    Args:
        session:    SQLAlchemy synchronous Session.
        nascente:   NascenteRecord to process.
        config:     ScoreConfig with §7.6 weights.
        llm_client: LLMClientProtocol for future extraction (Phase 2). Unused in Phase 1.

    Returns:
        The RioRecord for this nascente (existing if already processed).
    """
    # Idempotency check: use canonical_key = source_ref from payload or nascente.source_ref
    canonical_key = nascente.source_ref

    existing = session.scalar(
        select(RioRecord).where(RioRecord.canonical_key == canonical_key)
    )
    if existing is not None:
        return existing

    # Build normalized record from nascente.payload
    payload = nascente.payload or {}

    # Normalize fields
    name = payload.get("name", "")
    if name:
        name = normalize_name(name)

    address = payload.get("address")
    if address:
        address = normalize_address(address)

    lat = payload.get("lat")
    lon = payload.get("lon")
    lat, lon = normalize_coordinates(lat, lon)

    # Build normalized dict — preserve score input fields from payload
    normalized: dict[str, Any] = {
        "name": name,
        "address": address,
        "lat": lat,
        "lon": lon,
        # Score criterion values (from payload or defaults)
        "origem_value": float(payload.get("origem_value", 0.0)),
        "completude_value": float(payload.get("completude_value", 0.0)),
        "corroboracao_value": float(payload.get("corroboracao_value", 0.0)),
        "atualidade_value": float(payload.get("atualidade_value", 0.0)),
        "validacao_humana_value": float(payload.get("validacao_humana_value", 0.0)),
    }

    # Attraction-specific: preserve place_id_cache so ContactFinderAgent and SignalAgent
    # can look up Place Details without repeating text_search (D-04, COMP-03).
    # This cache key is written by DiscoveryAgent into the nascente payload; copying it
    # to normalized ensures subsequent FSM tasks have it available.
    if nascente.entity_type == "attraction" and "place_id_cache" in payload:
        normalized["place_id_cache"] = payload["place_id_cache"]

    # Add taxonomy labels (Phase 1 stub)
    normalized = label_entity(nascente.entity_type, normalized)

    # Compute embedding (Phase 1 stub — zero vector)
    embed_text = name or canonical_key
    embedding = compute_embedding(embed_text)

    # Check for duplicate before creating a new RioRecord
    municipio_id = payload.get("municipio_id")
    duplicate = find_duplicate(
        session=session,
        uf=nascente.uf,
        municipio_id=municipio_id,
        entity_type=nascente.entity_type,
        content_hash=nascente.content_hash,
        embedding=embedding,
    )
    if duplicate is not None:
        return duplicate

    # Create RioRecord
    rio = RioRecord(
        id=uuid.uuid4(),
        nascente_id=nascente.id,
        entity_type=nascente.entity_type,
        uf=nascente.uf,
        municipio_id=municipio_id,
        routing="in_progress",
        normalized=normalized,
        embedding=embedding,
        canonical_key=canonical_key,
    )
    session.add(rio)
    session.flush()

    # Apply §7.6 scoring and routing
    route_by_score(session, rio, config)
    session.flush()

    return rio


def reprocess_record(
    session: Session,
    rio_id: uuid.UUID,
    config: ScoreConfig,
) -> RioRecord:
    """Re-score an existing RioRecord (reset → re-route).

    Idempotent: re-running with the same config produces the same result.
    Used for: config change (threshold tuning), new corroboration, human validation,
    or error report reopen.

    Args:
        session: SQLAlchemy synchronous Session.
        rio_id:  UUID of the RioRecord to reprocess.
        config:  ScoreConfig with §7.6 weights (may differ from original score).

    Returns:
        The updated RioRecord.

    Raises:
        ValueError: If no RioRecord with rio_id exists.
    """
    rio = session.get(RioRecord, rio_id)
    if rio is None:
        raise ValueError(f"RioRecord {rio_id} not found")

    # Reset routing to in_progress before re-scoring
    rio.routing = "in_progress"
    session.flush()

    # Re-apply §7.6 score
    route_by_score(session, rio, config)
    session.flush()

    return rio


def reprocess_record_inline(
    rio_record: RioRecord,
    config: ScoreConfig,
) -> RioRecord:
    """Re-score a RioRecord in-memory (no Session required).

    Same logic as reprocess_record but operates on a transient/detached object.
    Used in unit tests and Celery tasks where the session is managed externally.

    Args:
        rio_record: The RioRecord to reprocess (mutated in-place).
        config:     ScoreConfig with §7.6 weights.

    Returns:
        The updated RioRecord (same object, mutated).
    """
    rio_record.routing = "in_progress"
    route_by_score(None, rio_record, config)
    return rio_record
