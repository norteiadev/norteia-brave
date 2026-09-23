"""Offline tests for POST /api/v1/atrativos/promote-bulk (quick-260918-rh7).

Handler is called DIRECTLY with a MagicMock session (same style as
test_transitions.py); the 423/401/422 cases go through TestClient with the
edit-lock suite's stub session + fakeredis. No DB, no Celery, no network.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

from brave.api.routers.atrativos import (  # noqa: E402
    PromoteBulkBody,
    _bucket_promote_bulk_candidates,
    promote_bulk_atrativos,
)
from brave.core.mar.publication import Promotion  # noqa: E402
from brave.core.models import RioRecord  # noqa: E402
from tests.unit.api.test_editing_lock import (  # noqa: E402, F401
    STEWARD_HEADERS,
    _env,
    _rc,
    client,
)

MOD = "brave.api.routers.atrativos"
URL = "/api/v1/atrativos/promote-bulk"


def _recent() -> str:
    return (datetime.now(UTC) - timedelta(days=5)).isoformat()


def _atr(score=70.0, **normalized):
    norm = {"descricao_editorial": "Texto.", "most_recent_review_at": _recent(), "review_count": 3}
    norm.update(normalized)
    return RioRecord(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="attraction",
        uf="BA",
        routing="dlq",
        canonical_key="cand-1",
        score=score,
        normalized=norm,
    )


def _db_for(rows):
    by_id = {r.id: r for r in rows}
    db = MagicMock()
    db.get.side_effect = lambda model, rid, *a, **k: by_id.get(rid)
    return db


_IN_MAR = Promotion(routing="mar", mar_id=uuid.uuid4(), held_reason=None, push_queued=True)


def _promote(db, rio, **kw):
    return _IN_MAR


# ---------------------------------------------------------------------------
# _bucket_promote_bulk_candidates — pure
# ---------------------------------------------------------------------------


def test_bucket_each_row_lands_in_exactly_one_bucket():
    ok = _atr()
    rows = [
        ok,
        _atr(score=50.0, descricao_editorial=""),  # below_score wins over no_description
        _atr(descricao_editorial=""),
        _atr(most_recent_review_at=None),
        _atr(most_recent_review_at=(datetime.now(UTC) - timedelta(days=400)).isoformat()),
    ]
    candidates, excluded = _bucket_promote_bulk_candidates(rows, 65.0, True)
    assert candidates == [ok]
    assert excluded == {"below_score": 1, "no_description": 1, "recency": 2}


def test_bucket_require_description_false_keeps_undescribed():
    row = _atr(descricao_editorial=None)
    candidates, excluded = _bucket_promote_bulk_candidates([row], 65.0, False)
    assert candidates == [row]
    assert excluded["no_description"] == 0


def test_bucket_null_score_is_below_score():
    _, excluded = _bucket_promote_bulk_candidates([_atr(score=None)], 65.0, True)
    assert excluded["below_score"] == 1


# ---------------------------------------------------------------------------
# Body validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [0, 201, 10_000])
def test_body_rejects_out_of_range_limit(limit):
    with pytest.raises(ValidationError):
        PromoteBulkBody(limit=limit)


def test_body_forbids_extra_and_defaults_to_dry_run():
    with pytest.raises(ValidationError):
        PromoteBulkBody(threshold=1)
    assert PromoteBulkBody().dry_run is True


# ---------------------------------------------------------------------------
# dry_run — never mutates
# ---------------------------------------------------------------------------


def test_dry_run_reports_counts_and_never_mutates():
    rows = [_atr(), _atr(), _atr(score=10.0)]
    db = _db_for(rows)
    queued: list[str] = []
    with (
        patch(f"{MOD}._query_promote_bulk_candidates", return_value=rows),
        patch(f"{MOD}.promote") as promote,
    ):
        out = promote_bulk_atrativos(PromoteBulkBody(uf="ba", limit=1), db, queued.append)
    assert out == {
        "candidates": 2,
        "excluded": {"below_score": 1, "no_description": 0, "recency": 0},
        "would_promote": 1,
    }
    promote.assert_not_called()
    assert queued == []
    db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# real run — audit/commit/enqueue are owned by publication.promote (tested there)
# ---------------------------------------------------------------------------


def _run(rows, body, promote_side_effect=_promote):
    db = _db_for(rows)
    queued: list[str] = []
    with (
        patch(f"{MOD}._query_promote_bulk_candidates", return_value=rows),
        patch(f"{MOD}.load_effective_config") as cfg,
        patch(f"{MOD}.promote", side_effect=promote_side_effect) as promote,
    ):
        out = promote_bulk_atrativos(body, db, queued.append)
    return out, db, promote, cfg, queued


def test_real_run_promotes_each_candidate_through_publication():
    row = _atr()
    out, _db, promote, cfg, queued = _run([row], PromoteBulkBody(dry_run=False))
    assert out["promoted"] == 1
    assert out["held"] == out["failed"] == out["push_failed"] == []
    assert out["remaining"] == 0
    cfg.assert_called_once()
    args, kw = promote.call_args
    assert args[1] is row
    assert kw["actor"] == "steward"
    assert kw["action"] == "transition_mar"
    assert kw["held_action"] == "promote_held"
    assert kw["extra"] == {"batch_id": out["batch_id"]}
    assert kw["config"] is cfg.return_value.score
    assert kw["enqueue"] == queued.append


def test_held_record_is_reported_with_reason():
    row = _atr()
    held = Promotion(routing="dlq", mar_id=None, held_reason="no_recent_reviews", push_queued=False)

    out, *_ = _run([row], PromoteBulkBody(dry_run=False), lambda *a, **k: held)
    assert out["promoted"] == 0
    assert out["held"] == [{"id": str(row.id), "reason": "no_recent_reviews"}]
    assert out["push_failed"] == []


def test_one_failing_record_does_not_abort_the_batch():
    bad, good = _atr(score=72.0), _atr(score=71.0)

    def _p(db, rio, **kw):
        if rio.id == bad.id:
            raise RuntimeError("boom")
        return _IN_MAR

    out, db, promote, _, _ = _run([bad, good], PromoteBulkBody(dry_run=False), _p)
    assert out["promoted"] == 1
    assert out["failed"] == [{"id": str(bad.id), "error": "boom"}]
    db.rollback.assert_called_once()
    assert promote.call_count == 2


def test_limit_caps_the_run_and_reports_remaining():
    rows = [_atr() for _ in range(5)]
    out, _, promote, _, _ = _run(rows, PromoteBulkBody(dry_run=False, limit=2))
    assert out["promoted"] == 2
    assert out["remaining"] == 3
    assert promote.call_count == 2


def test_push_failed_lists_exactly_the_unqueued_ids():
    queued_row, unqueued_row = _atr(score=72.0), _atr(score=71.0)
    not_queued = Promotion(routing="mar", mar_id=uuid.uuid4(), held_reason=None, push_queued=False)

    def _p(db, rio, **kw):
        return _IN_MAR if rio.id == queued_row.id else not_queued

    out, *_ = _run([queued_row, unqueued_row], PromoteBulkBody(dry_run=False), _p)
    assert out["promoted"] == 2
    assert out["push_failed"] == [str(unqueued_row.id)]


def test_vanished_record_is_reported_as_failed():
    row = _atr()
    db = MagicMock()
    db.get.return_value = None
    with (
        patch(f"{MOD}._query_promote_bulk_candidates", return_value=[row]),
        patch(f"{MOD}.load_effective_config"),
    ):
        out = promote_bulk_atrativos(PromoteBulkBody(dry_run=False), db)
    assert out["failed"] == [{"id": str(row.id), "error": "not found"}]


# ---------------------------------------------------------------------------
# HTTP layer — auth before lock, lock before handler, 422 on limit
# ---------------------------------------------------------------------------


def test_promote_bulk_423_when_ligado(client):  # noqa: F811
    r = client.post(URL, headers=STEWARD_HEADERS, json={"dry_run": True})
    assert r.status_code == 423, r.text


def test_promote_bulk_401_unauthenticated(client):  # noqa: F811
    r = client.post(URL, json={"dry_run": True})
    assert r.status_code == 401, r.text


def test_promote_bulk_422_when_limit_over_200(client):  # noqa: F811
    from brave.core import engine as collection_engine

    collection_engine.set_mode(_rc(), collection_engine.PAUSADO)
    r = client.post(URL, headers=STEWARD_HEADERS, json={"limit": 201})
    assert r.status_code == 422, r.text
