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


def _promote(db, rio, config=None):
    rio.routing = "mar"


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
    with (
        patch(f"{MOD}._query_promote_bulk_candidates", return_value=rows),
        patch(f"{MOD}.validate_and_promote_rio") as vp,
        patch(f"{MOD}.write_audit") as audit,
        patch("brave.tasks.pipeline.push_attraction_task") as push,
    ):
        out = promote_bulk_atrativos(PromoteBulkBody(uf="ba", limit=1), db)
    assert out == {
        "candidates": 2,
        "excluded": {"below_score": 1, "no_description": 0, "recency": 0},
        "would_promote": 1,
    }
    vp.assert_not_called()
    audit.assert_not_called()
    push.delay.assert_not_called()
    db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# real run
# ---------------------------------------------------------------------------


def _run(rows, body, vp_side_effect=_promote, push_side_effect=None):
    db = _db_for(rows)
    with (
        patch(f"{MOD}._query_promote_bulk_candidates", return_value=rows),
        patch(f"{MOD}.load_effective_config") as cfg,
        patch(f"{MOD}.validate_and_promote_rio", side_effect=vp_side_effect) as vp,
        patch(f"{MOD}.write_audit") as audit,
        patch("brave.tasks.pipeline.push_attraction_task") as push,
    ):
        push.delay.side_effect = push_side_effect
        out = promote_bulk_atrativos(body, db)
    return out, db, vp, audit, push, cfg


def test_real_run_promotes_audits_commits_and_pushes():
    row = _atr()
    out, db, vp, audit, push, cfg = _run([row], PromoteBulkBody(dry_run=False))
    assert out["promoted"] == 1
    assert out["held"] == out["failed"] == out["push_failed"] == []
    assert out["remaining"] == 0
    cfg.assert_called_once()
    assert vp.call_args.kwargs["config"] is cfg.return_value.score
    kw = audit.call_args.kwargs
    assert kw["action"] == "transition_mar"
    assert kw["actor"] == "steward"
    assert kw["record_id"] == row.id
    assert kw["before_state"] == {"routing": "dlq"}
    assert kw["after_state"] == {"routing": "mar", "batch_id": out["batch_id"]}
    db.commit.assert_called_once()
    push.delay.assert_called_once_with(str(row.id))


def test_held_record_is_audited_not_pushed():
    row = _atr()

    def _hold(db, rio, config=None):
        rio.dlq_reason = "no_recent_reviews"

    out, db, _, audit, push, _ = _run([row], PromoteBulkBody(dry_run=False), _hold)
    assert out["promoted"] == 0
    assert out["held"] == [{"id": str(row.id), "reason": "no_recent_reviews"}]
    assert audit.call_args.kwargs["action"] == "promote_held"
    assert audit.call_args.kwargs["after_state"]["batch_id"] == out["batch_id"]
    db.commit.assert_called_once()
    push.delay.assert_not_called()


def test_one_failing_record_does_not_abort_the_batch():
    bad, good = _atr(score=72.0), _atr(score=71.0)

    def _vp(db, rio, config=None):
        if rio.id == bad.id:
            raise RuntimeError("boom")
        rio.routing = "mar"

    out, db, _, _, push, _ = _run([bad, good], PromoteBulkBody(dry_run=False), _vp)
    assert out["promoted"] == 1
    assert out["failed"] == [{"id": str(bad.id), "error": "boom"}]
    db.rollback.assert_called_once()
    db.commit.assert_called_once()
    push.delay.assert_called_once_with(str(good.id))


def test_limit_caps_the_run_and_reports_remaining():
    rows = [_atr() for _ in range(5)]
    out, _, vp, _, push, _ = _run(rows, PromoteBulkBody(dry_run=False, limit=2))
    assert out["promoted"] == 2
    assert out["remaining"] == 3
    assert vp.call_count == 2
    assert push.delay.call_count == 2


def test_push_dispatch_failure_never_raises():
    row = _atr()
    out, *_ = _run(
        [row], PromoteBulkBody(dry_run=False), push_side_effect=ConnectionError("broker down")
    )
    assert out["promoted"] == 1
    assert out["push_failed"] == [str(row.id)]


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
