"""Two-stage deduplication with territorial-key blocking (D-07, D-08).

Stage 1: Exact content_hash check against NascenteRecord.
  - Fast, zero false positives.
  - Blocks on same source+source_ref+hash.

Stage 2: Territorial-key block + pgvector HNSW fuzzy search.
  - ALWAYS filter by (uf, municipio_id, entity_type) BEFORE vector comparison.
  - NEVER compare vectors across UF boundaries (homonym municipio bug, see PITFALLS §2).
  - If municipio_id is None, skip vector search — no territorial block defined.

Phase 1 note: compute_embedding returns a zero stub. Real embeddings via LLMClient in Phase 2.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.core.models import NascenteRecord, RioRecord

# Cosine similarity threshold for dedup candidate acceptance
DEDUP_THRESHOLD = 0.95


def find_duplicate(
    session: Session,
    uf: str,
    municipio_id: str | None,
    entity_type: str,
    content_hash: str,
    embedding: list[float] | None = None,
) -> RioRecord | None:
    """Find a duplicate RioRecord for the given content.

    Two-stage dedup:
    1. Exact content_hash match — fast, no false positives.
    2. Territorial-key-blocked pgvector fuzzy search (UF + municipio_id + entity_type).
       Only runs if embedding is provided AND municipio_id is not None.

    NEVER compares vectors across UF boundaries (homonym municipio protection, D-07).

    Args:
        session:      SQLAlchemy synchronous Session.
        uf:           Two-letter state code (territorial block key).
        municipio_id: Municipality ID for territorial blocking. None skips vector search.
        entity_type:  "destination" or "attraction".
        content_hash: SHA-256 hash of the source payload.
        embedding:    1536-dimensional vector embedding. None skips vector search.

    Returns:
        Matching RioRecord if a duplicate is found, None otherwise.
    """
    # Stage 1: Exact content hash check
    # We check NascenteRecord.content_hash → then find the linked RioRecord
    existing_nascente = session.scalar(
        select(NascenteRecord).where(
            NascenteRecord.content_hash == content_hash,
        )
    )
    if existing_nascente is not None:
        # Check if there's a RioRecord for this nascente
        rio = session.scalar(
            select(RioRecord).where(
                RioRecord.nascente_id == existing_nascente.id,
            )
        )
        if rio is not None:
            return rio

    # Stage 2: Territorial-key-blocked pgvector fuzzy search
    # Skip if no municipio_id (no territorial block) or no embedding
    if municipio_id is None or embedding is None:
        return None

    # Query RioRecord with territorial-key block (UF + municipio_id + entity_type)
    # This ENSURES we never compare across UF boundaries
    candidates = session.scalars(
        select(RioRecord).where(
            RioRecord.uf == uf,
            RioRecord.municipio_id == municipio_id,
            RioRecord.entity_type == entity_type,
            RioRecord.embedding.isnot(None),
        )
        .order_by(RioRecord.embedding.cosine_distance(embedding))
        .limit(10)
    )

    for candidate in candidates:
        if candidate.embedding is not None:
            similarity = _cosine_similarity(candidate.embedding, embedding)
            if similarity > DEDUP_THRESHOLD:
                return candidate

    return None


def compute_embedding(text: str) -> list[float]:
    """Compute an embedding vector for a text string.

    Phase 1 stub: returns a zero vector of dimension 1536.
    Real embedding via LLMClient deferred to Phase 2 when lane data arrives.
    This is intentional — Phase 1 doesn't call real embedding APIs (D-11).

    Args:
        text: Input text to embed.

    Returns:
        1536-dimensional zero vector (Phase 1 stub).
    """
    # Phase 1 intentional stub — deterministic zero vector
    # The embedding column exists for HNSW readiness; real embeddings in Phase 2
    return [0.0] * 1536


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Phase 1 stub vectors (all zeros) always return 0.0 similarity.
    Real embeddings in Phase 2 will produce meaningful similarity scores.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Cosine similarity in [0, 1]. Returns 0.0 for zero vectors.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
