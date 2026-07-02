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

from sqlalchemy.orm import Session

from brave.core.models import RioRecord
from brave.core.repositories import (
    SqlAlchemyNascenteRepository,
    SqlAlchemyRioRepository,
)

# Cosine similarity threshold for dedup candidate acceptance
DEDUP_THRESHOLD = 0.95

# Stateless data-access seam (Phase A). Session passed per call; caller commits.
_nascente_repo = SqlAlchemyNascenteRepository()
_rio_repo = SqlAlchemyRioRepository()


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
    # Stage 1: Exact content-hash check, BLOCKED by the same territorial key as
    # Stage 2 (UF + entity_type). content_hash is indexed but not unique, so an
    # unscoped lookup could return a record from a different UF/entity and return
    # an arbitrary linked RioRecord — violating the never-compare-across-UF
    # invariant (CR-02). Scoping the hash match keeps homonym municípios in
    # different states (e.g. São Domingos/BA vs São Domingos/SE) from colliding.
    existing_nascente = _nascente_repo.find_by_hash_scoped(
        session, content_hash, uf, entity_type
    )
    if existing_nascente is not None:
        # Check if there's a RioRecord for this nascente
        rio = _rio_repo.get_by_nascente_id(session, existing_nascente.id)
        if rio is not None:
            return rio

    # Stage 2: Territorial-key-blocked pgvector fuzzy search
    # Skip if no municipio_id (no territorial block) or no embedding
    if municipio_id is None or embedding is None:
        return None

    # Query RioRecord with territorial-key block (UF + municipio_id + entity_type)
    # This ENSURES we never compare across UF boundaries. The pgvector
    # cosine-distance ORDER BY + LIMIT 10 is preserved in the repository; the
    # exact-similarity post-filter stays here.
    candidates = _rio_repo.find_dedup_candidates(
        session,
        uf=uf,
        municipio_id=municipio_id,
        entity_type=entity_type,
        embedding=embedding,
        limit=10,
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
