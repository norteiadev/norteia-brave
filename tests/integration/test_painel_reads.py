"""Painel read paths against real Postgres: the batched queries must count/pair
exactly like the per-row queries they replaced (runs window counts, dedup pairs)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from brave.api.routers.dedup import list_dedup_pairs
from brave.api.routers.runs import _window_counts
from brave.core.models import (
    MarRecord,
    NascenteRecord,
    PoisonQuarantine,
    RioRecord,
    RunHistory,
)

pytestmark = pytest.mark.integration

# A window no other test writes into, so leftover rows never leak into the counts.
T0 = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _rio(db, *, uf="ZZ", municipio_id=None, routing="dlq", processed_at=None, name="X"):
    ref = f"test:{uuid.uuid4().hex}"
    nas = NascenteRecord(
        id=uuid.uuid4(), source="test", source_ref=ref, entity_type="attraction",
        uf=uf, payload={}, content_hash=ref, version=1,
    )
    db.add(nas)
    db.flush()
    rio = RioRecord(
        id=uuid.uuid4(), nascente_id=nas.id, entity_type="attraction", uf=uf,
        municipio_id=municipio_id, routing=routing, processed_at=processed_at,
        normalized={"name": name},
    )
    db.add(rio)
    db.flush()
    return rio


def _mar(db, rio, *, published_at=None, superseded_by_id=None):
    mar = MarRecord(
        id=uuid.uuid4(), rio_id=rio.id, entity_type=rio.entity_type,
        source_ref=f"test:{uuid.uuid4().hex}", canonical={"name": "X"}, provenance={},
        reliability_score=90, score_version="v1", superseded_by_id=superseded_by_id,
    )
    if published_at is not None:
        mar.published_at = published_at
    db.add(mar)
    db.flush()
    return mar


def _run(started_at, ended_at):
    return RunHistory(
        id=uuid.uuid4(), started_at=started_at, ended_at=ended_at, ufs=["ZZ"],
        source="default", depth="nascente_rio", lane="both", status="concluido",
    )


def test_window_counts_groups_per_run(db_session):
    hour = timedelta(hours=1)
    run_a = _run(T0, T0 + hour)
    run_b = _run(T0 + 2 * hour, T0 + 3 * hour)
    run_empty = _run(T0 + 10 * hour, T0 + 11 * hour)

    # run_a: 1 synced, 1 dlq + 1 poison failed; an in_progress row must not count.
    _mar(db_session, _rio(db_session, routing="mar"), published_at=T0 + hour / 2)
    _rio(db_session, routing="dlq", processed_at=T0 + hour / 2)
    _rio(db_session, routing="in_progress", processed_at=T0 + hour / 2)
    db_session.add(
        PoisonQuarantine(task_name="t", error_message="e", quarantined_at=T0 + hour / 2)
    )
    # run_b: 2 synced, 1 descarte.
    for _ in range(2):
        _mar(db_session, _rio(db_session, routing="mar"), published_at=T0 + 2.5 * hour)
    _rio(db_session, routing="descarte", processed_at=T0 + 2.5 * hour)
    db_session.flush()

    counts = _window_counts(db_session, [run_a, run_b, run_empty])

    assert counts == {run_a.id: (1, 2), run_b.id: (2, 1), run_empty.id: (0, 0)}


def test_dedup_pairs_one_active_mar_per_territorial_key(db_session):
    mun = f"mun-{uuid.uuid4().hex[:8]}"
    # Territorial key with TWO active Mar rows + one superseded → exactly one pair
    # per candidate, never the superseded row, never another município's Mar.
    active = [_mar(db_session, _rio(db_session, municipio_id=mun, routing="mar")) for _ in range(2)]
    superseded = _mar(
        db_session, _rio(db_session, municipio_id=mun, routing="mar"),
        superseded_by_id=active[0].id,
    )
    _mar(db_session, _rio(db_session, municipio_id=f"{mun}-other", routing="mar"))
    cands = [_rio(db_session, municipio_id=mun, routing="dlq") for _ in range(2)]
    unpaired = _rio(db_session, municipio_id=f"{mun}-none", routing="dlq")

    resp = list_dedup_pairs(uf="ZZ", offset=0, limit=500, db=db_session)

    ours = [i for i in resp["items"] if i["municipio"] == mun]
    assert {i["candidate_rio_id"] for i in ours} == {str(c.id) for c in cands}
    assert {i["mar_id"] for i in ours} <= {str(m.id) for m in active}
    assert str(superseded.id) not in {i["mar_id"] for i in resp["items"]}
    assert str(unpaired.id) not in {i["candidate_id"] for i in resp["items"]}
