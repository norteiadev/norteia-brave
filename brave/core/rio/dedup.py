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

from brave.core.models import MarRecord, RioRecord
from brave.core.repositories import (
    SqlAlchemyNascenteRepository,
    SqlAlchemyRioRepository,
)
from brave.observability.audit import write_audit

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
    # ponytail: compute_embedding is a zero-vector stub, whose cosine similarity is
    # always 0.0 — the pgvector query can never match. The `not any(...)` guard
    # stops skipping by itself once compute_embedding returns real vectors.
    if municipio_id is None or embedding is None or not any(embedding):
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


def merge_into_mar(rio: RioRecord, mar: MarRecord) -> None:
    """The merge of a dedup pair, no audit and no commit (scripts/territorial_dedup_fixes.py)."""
    # UNION the candidate's source_ref into the existing Mar provenance.
    # RioRecord has no source_ref column, so derive it the same way the Mar
    # promotion service does (mar/service.py:41): canonical_key or the row id.
    candidate_source_ref = rio.canonical_key or str(rio.id)
    provenance = dict(mar.provenance or {})
    merged = list(provenance.get("merged_source_refs", []))
    if candidate_source_ref not in merged:
        merged.append(candidate_source_ref)
    # Reassign a fresh dict so SQLAlchemy detects the JSON change.
    mar.provenance = {**provenance, "merged_source_refs": merged}
    # Discard the candidate Rio from the pending dedup pool — do NOT create a
    # 2nd active Mar, do NOT touch mar.source_ref, do NOT run the Mar
    # promotion/supersession path.
    rio.routing = "descarte"


PLACE_TWIN_REASON = "dedup_same_place_id"


def resolve_place_twins(session: Session, rio: RioRecord, actor: str) -> list[RioRecord]:
    """Keep one atrativo per (município, google_place_id); discard the rest. Caller commits.

    TripAdvisor lists one place under several locationIds (twin cards, tours named after
    the place), and Places matches them all to the same place_id. The keeper is the one
    already on norteia-api, then in Mar, then the best scored/reviewed; a loser with an
    active Mar is merged into the keeper's (publish then hides it on the API). Scoped to
    the município: one place_id across municípios is a Places mismatch, not a twin.
    """
    place_id = (rio.normalized or {}).get("google_place_id")
    if rio.entity_type != "attraction" or not place_id or rio.municipio_id is None:
        return []
    group = list(
        session.scalars(
            select(RioRecord).where(
                RioRecord.entity_type == "attraction",
                RioRecord.routing != "descarte",
                RioRecord.municipio_id == rio.municipio_id,
                RioRecord.normalized["google_place_id"].as_string() == place_id,
            )
        ).all()
    )
    if len(group) < 2:
        return []
    mars = {
        m.rio_id: m
        for m in session.scalars(
            select(MarRecord).where(
                MarRecord.rio_id.in_([r.id for r in group]),
                MarRecord.superseded_by_id.is_(None),
            )
        ).all()
    }

    def rank(r: RioRecord) -> tuple:
        mar = mars.get(r.id)
        on_api = mar is not None and mar.pushed_at is not None and mar.retracted_at is None
        reviews = (r.normalized or {}).get("review_count") or 0
        return (on_api, r.routing == "mar", float(r.score or 0), reviews, r.canonical_key or "")

    keeper = max(group, key=rank)
    keeper_mar = mars.get(keeper.id)
    losers = [r for r in group if r is not keeper]
    for loser in losers:
        before = {"routing": loser.routing, "dlq_reason": loser.dlq_reason}
        if keeper_mar is not None and loser.id in mars:
            merge_into_mar(loser, keeper_mar)
        loser.routing, loser.dlq_reason = "descarte", PLACE_TWIN_REASON
        write_audit(
            session,
            action=PLACE_TWIN_REASON,
            entity_type="attraction",
            record_id=loser.id,
            before_state=before,
            after_state={"routing": "descarte", "keeper": keeper.canonical_key,
                         "google_place_id": place_id},
            actor=actor,
        )
    session.flush()
    return losers
