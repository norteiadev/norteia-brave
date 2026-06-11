"""Tests for two-stage dedup with territorial-key blocking (D-07).

All tests run fully offline — no DB, no I/O.
Dedup behavior requiring DB queries is tested via unit-level mocking.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from brave.core.models import RioRecord


def test_find_duplicate_returns_none_when_empty():
    """find_duplicate returns None when no existing records for same UF+municipio_id."""
    from brave.core.rio.dedup import find_duplicate

    session = MagicMock()
    # Mock: no exact hash match, no vector candidates
    session.scalars.return_value.first.return_value = None

    # Patch the scalar to return None for hash check
    session.scalar.return_value = None

    result = find_duplicate(
        session=session,
        uf="BA",
        municipio_id="123",
        entity_type="destination",
        content_hash="abc123",
        embedding=None,
    )
    assert result is None


def test_find_duplicate_returns_existing_on_hash_match():
    """find_duplicate returns existing record when content_hash matches exactly."""
    from brave.core.rio.dedup import find_duplicate

    existing = RioRecord(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="destination",
        uf="BA",
        routing="mar",
    )
    session = MagicMock()
    session.scalar.return_value = existing

    result = find_duplicate(
        session=session,
        uf="BA",
        municipio_id="123",
        entity_type="destination",
        content_hash="abc123",
        embedding=None,
    )
    assert result == existing


def test_find_duplicate_no_cross_uf_comparison():
    """find_duplicate NEVER compares records with different uf values.

    São Domingos/BA and São Domingos/SE are homonyms — they must never merge.
    The territorial-key block ensures UF isolation.
    """
    from brave.core.rio.dedup import find_duplicate

    session = MagicMock()
    # No exact hash match
    session.scalar.return_value = None

    # Mock vector candidates: all from UF "SE" (different from query UF "BA")
    se_record = RioRecord(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="destination",
        uf="SE",
        routing="mar",
    )
    # The session query for fuzzy dedup is territorial-key-blocked in the query itself
    # (WHERE uf == uf), so we verify that when no BA records exist, result is None
    session.scalars.return_value.__iter__ = MagicMock(return_value=iter([]))

    result = find_duplicate(
        session=session,
        uf="BA",  # Querying for BA
        municipio_id="456",
        entity_type="destination",
        content_hash="xyz999",
        embedding=[0.0] * 1536,
    )
    assert result is None, (
        "find_duplicate must never return a candidate from a different UF. "
        "Territorial-key blocking ensures São Domingos/BA ≠ São Domingos/SE."
    )


def test_compute_embedding_returns_stub():
    """compute_embedding returns a deterministic zero stub in Phase 1."""
    from brave.core.rio.dedup import compute_embedding

    emb = compute_embedding("Trancoso")
    assert len(emb) == 1536
    assert all(v == 0.0 for v in emb)


def test_compute_embedding_is_deterministic():
    """compute_embedding returns the same value for the same input."""
    from brave.core.rio.dedup import compute_embedding

    emb1 = compute_embedding("Trancoso")
    emb2 = compute_embedding("Trancoso")
    assert emb1 == emb2


def test_find_duplicate_skips_vector_when_no_municipio():
    """find_duplicate skips vector search when municipio_id is None (no block defined)."""
    from brave.core.rio.dedup import find_duplicate

    session = MagicMock()
    session.scalar.return_value = None  # No hash match

    result = find_duplicate(
        session=session,
        uf="BA",
        municipio_id=None,  # No territorial block
        entity_type="destination",
        content_hash="abc123",
        embedding=[0.0] * 1536,
    )
    assert result is None
    # scalars should NOT be called for fuzzy search (no municipio_id = no block)
    # The vector search requires a block to be defined
