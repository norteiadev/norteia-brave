"""Offline unit tests for the Duplicados list endpoint (UI-PAINEL-2).

All tests run fully offline — MagicMock session, no DB, no pgvector operator.
Covers the compute-on-read field similarity (RESEARCH A1: embeddings are a zero
stub, so similarity/matched/diverged are computed in Python from normalized vs
canonical) plus the territorial-key blocking invariant (CR-02 — never across UF).
"""

import uuid
from unittest.mock import MagicMock

from brave.api.routers.dedup import (
    DedupPairItem,
    DedupPairsResponse,
    _compute_field_diff,
    _find_active_mar_for,
    _token_similarity,
    list_dedup_pairs,
)
from brave.core.models import MarRecord, RioRecord


def _rio(**kw):
    base = dict(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="destination",
        uf="BA",
        routing="dlq",
    )
    base.update(kw)
    return RioRecord(**base)


def _mar(**kw):
    base = dict(
        id=uuid.uuid4(),
        rio_id=uuid.uuid4(),
        entity_type="destination",
        source_ref="src-existing",
        canonical={},
        provenance={},
        reliability_score=90,
        score_version="v1.0",
    )
    base.update(kw)
    return MarRecord(**base)


# ---------------------------------------------------------------------------
# Field diff (compute-on-read, pure)
# ---------------------------------------------------------------------------


def test_compute_field_diff_labels_matched_and_diverged():
    """Equal canonical-allow-list keys → matched; both-present-and-unequal → diverged."""
    normalized = {
        "name": "Trancoso",
        "municipio": "Porto Seguro",
        "categoria": "praia",
        # scoring keys must be excluded from the diff (canonical allow-list)
        "origem_value": 9,
        "atualidade_value": 7,
    }
    canonical = {
        "name": "Trancoso",
        "municipio": "Porto Seguro",
        "categoria": "vila",
    }
    matched, diverged = _compute_field_diff(normalized, canonical)

    assert "name" in matched
    assert "municipio" in matched
    # scoring keys never surface as matched or diverged
    assert "origem_value" not in matched
    assert all(d["field"] != "origem_value" for d in diverged)
    # categoria differs → diverged with both values labeled
    div = next(d for d in diverged if d["field"] == "categoria")
    assert div["candidate"] == "praia"
    assert div["mar"] == "vila"


def test_token_similarity_is_a_float_between_0_and_1():
    """Name/municipio/UF token overlap → labeled similarity (no embedding call)."""
    sim = _token_similarity(
        {"name": "Praia do Forte"}, {"name": "Praia do Forte"}
    )
    assert isinstance(sim, float)
    assert sim == 1.0
    none = _token_similarity({"name": "Trancoso"}, {"name": "Itacaré"})
    assert 0.0 <= none < 1.0


# ---------------------------------------------------------------------------
# Territorial-key blocking (CR-02 — never compare across UF)
# ---------------------------------------------------------------------------


def test_find_active_mar_for_is_territorial_blocked():
    """The pairing query blocks on uf + municipio_id + entity_type and active-only."""
    db = MagicMock()
    db.scalars.return_value.first.return_value = None

    cand = _rio(uf="BA", municipio_id="123", entity_type="destination")
    result = _find_active_mar_for(db, cand)

    assert result is None
    stmt = db.scalars.call_args[0][0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "uf" in sql
    assert "municipio_id" in sql
    assert "entity_type" in sql
    assert "superseded_by_id" in sql
    # the candidate's own UF is bound — proof we never widen across UF
    assert "BA" in sql


# ---------------------------------------------------------------------------
# Response envelope (exact shape)
# ---------------------------------------------------------------------------


def test_list_dedup_pairs_envelope_shape_exact():
    """Envelope is {items,total,offset,limit}; each item carries the A5 schema."""
    db = MagicMock()
    db.scalar.return_value = 1  # total count

    cand = _rio(
        uf="BA",
        municipio_id="123",
        entity_type="destination",
        normalized={"name": "Trancoso", "categoria": "praia"},
    )
    mar = _mar(canonical={"name": "Trancoso", "categoria": "vila"})

    db.scalars.return_value.all.return_value = [cand]
    db.scalars.return_value.first.return_value = mar

    resp = list_dedup_pairs(uf=None, offset=0, limit=50, db=db)

    assert set(resp.keys()) == {"items", "total", "offset", "limit"}
    assert resp["total"] == 1
    assert resp["offset"] == 0
    assert resp["limit"] == 50
    assert len(resp["items"]) == 1

    item = resp["items"][0]
    assert set(item.keys()) == {
        "candidate_id",
        "mar_id",
        "candidate_rio_id",
        "mar_rio_id",
        "uf",
        "municipio",
        "entity_type",
        "similarity",
        "similarity_source",
        "matched_fields",
        "diverged_fields",
    }
    assert item["candidate_id"] == str(cand.id)
    assert item["candidate_rio_id"] == str(cand.id)
    assert item["mar_id"] == str(mar.id)
    assert item["mar_rio_id"] == str(mar.rio_id)
    assert item["uf"] == "BA"
    assert item["municipio"] == "123"
    assert item["entity_type"] == "destination"
    assert item["similarity_source"] == "embedding_stub"
    assert "name" in item["matched_fields"]
    assert any(d["field"] == "categoria" for d in item["diverged_fields"])


def test_list_dedup_pairs_skips_candidates_without_a_mar_pair():
    """A candidate with no active Mar on its territorial key is never emitted."""
    db = MagicMock()
    db.scalar.return_value = 1
    cand = _rio(uf="BA", municipio_id="999", entity_type="destination",
                normalized={"name": "Solo"})
    db.scalars.return_value.all.return_value = [cand]
    db.scalars.return_value.first.return_value = None  # no Mar on this key

    resp = list_dedup_pairs(uf=None, offset=0, limit=50, db=db)
    assert resp["items"] == []
    # total still reflects the candidate pool count (envelope is not array length)
    assert resp["total"] == 1


def test_dedup_pair_models_forbid_extra_fields():
    """extra='forbid' on both response models (typed-contract discipline, A5)."""
    assert DedupPairsResponse.model_config.get("extra") == "forbid"
    assert DedupPairItem.model_config.get("extra") == "forbid"
