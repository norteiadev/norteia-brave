"""Offline unit tests for the Duplicados resolve endpoint (UI-PAINEL-2).

LOCKED CONTEXT A2 (OVERRIDES RESEARCH Pitfall 4):
  - merge unions the candidate's source_ref into the EXISTING Mar's provenance
    (provenance["merged_source_refs"]) and routes the candidate Rio → descarte.
    NO new MarRecord, NO promote_to_mar, NO 409 on differing sources.
  - keep: no row change; the "dedup_kept" audit row IS the suppression marker.
  - discard: candidate Rio → descarte, dlq_reason="dedup_discarded".
  - every action writes an audit row.

All tests run fully offline — MagicMock session, no DB.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from brave.api.routers.dedup import ResolveBody, resolve_pair
from brave.core.models import MarRecord, RioRecord


def _rio(**kw):
    base = dict(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="destination",
        uf="BA",
        routing="dlq",
        canonical_key="cand-src-1",
    )
    base.update(kw)
    return RioRecord(**base)


def _mar(**kw):
    base = dict(
        id=uuid.uuid4(),
        rio_id=uuid.uuid4(),
        entity_type="destination",
        source_ref="mar-src-existing",
        canonical={},
        provenance={},
        reliability_score=90,
        score_version="v1.0",
    )
    base.update(kw)
    return MarRecord(**base)


def _db_for(rio, mar=None):
    db = MagicMock()

    def _get(model, _id):
        if model is RioRecord:
            return rio
        if model is MarRecord:
            return mar
        return None

    db.get.side_effect = _get
    return db


# ---------------------------------------------------------------------------
# merge (LOCKED A2)
# ---------------------------------------------------------------------------


def test_merge_unions_candidate_source_ref_into_existing_mar():
    """merge appends the candidate source_ref into the existing Mar provenance."""
    rio = _rio(canonical_key="cand-src-1")
    mar = _mar(source_ref="mar-src-existing", provenance={"score_breakdown": {"x": 1}})
    db = _db_for(rio, mar)

    with patch("brave.api.routers.dedup.write_audit") as audit:
        result = resolve_pair(
            candidate_rio_id=rio.id,
            body=ResolveBody(action="merge", mar_id=mar.id),
            db=db,
        )

    # candidate source_ref unioned into the EXISTING Mar provenance
    assert mar.provenance["merged_source_refs"] == ["cand-src-1"]
    # existing Mar's own source_ref is NEVER touched (uq_mar_active_source_ref holds)
    assert mar.source_ref == "mar-src-existing"
    # prior provenance keys preserved
    assert mar.provenance["score_breakdown"] == {"x": 1}
    # candidate Rio is discarded from the pending dedup pool
    assert rio.routing == "descarte"
    # audited as dedup_merged
    assert audit.call_args.kwargs["action"] == "dedup_merged"
    # NO new MarRecord created (no promote_to_mar, no session.add of a Mar)
    db.add.assert_not_called()
    db.commit.assert_called_once()
    assert result == {"status": "ok", "action": "merge"}


def test_merge_appends_to_existing_merged_source_refs():
    """A second merge appends without clobbering prior merged_source_refs."""
    rio = _rio(canonical_key="cand-src-2")
    mar = _mar(provenance={"merged_source_refs": ["cand-src-1"]})
    db = _db_for(rio, mar)

    with patch("brave.api.routers.dedup.write_audit"):
        resolve_pair(
            candidate_rio_id=rio.id,
            body=ResolveBody(action="merge", mar_id=mar.id),
            db=db,
        )

    assert mar.provenance["merged_source_refs"] == ["cand-src-1", "cand-src-2"]


def test_merge_404_when_target_mar_missing():
    """merge with a missing target Mar → 404 (no mutation)."""
    rio = _rio()
    db = _db_for(rio, mar=None)

    with patch("brave.api.routers.dedup.write_audit"):
        with pytest.raises(HTTPException) as exc:
            resolve_pair(
                candidate_rio_id=rio.id,
                body=ResolveBody(action="merge", mar_id=uuid.uuid4()),
                db=db,
            )
    assert exc.value.status_code == 404
    assert rio.routing == "dlq"  # unchanged


# ---------------------------------------------------------------------------
# discard
# ---------------------------------------------------------------------------


def test_discard_routes_candidate_to_descarte_and_audits():
    rio = _rio(routing="dlq")
    db = _db_for(rio)

    with patch("brave.api.routers.dedup.write_audit") as audit:
        result = resolve_pair(
            candidate_rio_id=rio.id,
            body=ResolveBody(action="discard", mar_id=uuid.uuid4()),
            db=db,
        )

    assert rio.routing == "descarte"
    assert rio.dlq_reason == "dedup_discarded"
    assert audit.call_args.kwargs["action"] == "dedup_discarded"
    db.commit.assert_called_once()
    assert result == {"status": "ok", "action": "discard"}


# ---------------------------------------------------------------------------
# keep
# ---------------------------------------------------------------------------


def test_keep_writes_suppression_audit_and_mutates_no_routing():
    rio = _rio(routing="dlq")
    db = _db_for(rio)

    with patch("brave.api.routers.dedup.write_audit") as audit:
        result = resolve_pair(
            candidate_rio_id=rio.id,
            body=ResolveBody(action="keep", mar_id=uuid.uuid4()),
            db=db,
        )

    assert rio.routing == "dlq"  # untouched
    assert audit.call_args.kwargs["action"] == "dedup_kept"
    db.commit.assert_called_once()
    assert result == {"status": "ok", "action": "keep"}


# ---------------------------------------------------------------------------
# 404 candidate missing
# ---------------------------------------------------------------------------


def test_resolve_404_when_candidate_rio_missing():
    db = _db_for(rio=None)

    with patch("brave.api.routers.dedup.write_audit"):
        with pytest.raises(HTTPException) as exc:
            resolve_pair(
                candidate_rio_id=uuid.uuid4(),
                body=ResolveBody(action="discard", mar_id=uuid.uuid4()),
                db=db,
            )
    assert exc.value.status_code == 404
