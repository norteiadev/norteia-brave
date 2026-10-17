"""Offline unit tests for the Varreduras runs list + reprocess (UI-PAINEL-2).

Fully offline (MagicMock session, no DB, no broker, RUN_REAL_EXTERNALS unset).
Covers:
  - on-read window aggregation (_window_counts): synced from Mar, failed from
    Rio dlq/descarte + PoisonQuarantine.
  - list_runs envelope shape is exact + uf is filtered over the JSON ufs array.
  - reprocess_run starts a NEW engine run with the run's depth/lane/source/cap (409
    while a run is active), audits, and 404s on an unknown run.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from brave.api.routers import runs as runs_router
from brave.core.models import RunHistory


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # Force the offline broker fallback branch (never the 503 path).
    monkeypatch.delenv("RUN_REAL_EXTERNALS", raising=False)


def _run(**overrides) -> RunHistory:
    base = dict(
        id=uuid.uuid4(),
        started_at=datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 6, 28, 11, 0, tzinfo=timezone.utc),
        ufs=["BA"],
        source="default",
        depth="nascente_rio",
        lane="both",
        status="concluido",
    )
    base.update(overrides)
    return RunHistory(**base)


# ---------------------------------------------------------------------------
# On-read window aggregation
# ---------------------------------------------------------------------------


def test_window_counts_sums_rio_and_poison_failures():
    """synced = Mar count; failed = rio(dlq/descarte) + poison counts, per run."""
    db = MagicMock()
    # order of db.execute calls: synced, failed_rio, failed_poison → (run idx, count)
    db.execute.return_value.all.side_effect = [[(0, 7)], [(0, 3)], [(0, 2)]]
    run = _run()

    assert runs_router._window_counts(db, [run]) == {run.id: (7, 5)}  # 3 rio + 2 poison


def test_window_counts_uses_now_when_ended_at_none():
    """A still-running run (ended_at=None) aggregates up to now() without error."""
    db = MagicMock()
    db.execute.return_value.all.side_effect = [[(0, 1)], [], []]
    run = _run(ended_at=None)

    assert runs_router._window_counts(db, [run]) == {run.id: (1, 0)}


# ---------------------------------------------------------------------------
# list_runs envelope + uf filtering
# ---------------------------------------------------------------------------


def test_list_runs_envelope_shape_and_on_read_counts():
    """list_runs returns the exact paginated envelope with on-read synced/failed/total."""
    db = MagicMock()
    run = _run()
    db.scalars.return_value.all.return_value = [run]
    db.scalar.return_value = 1  # SQL-side total (no uf filter)
    # window counts for the single run: synced, failed_rio, failed_poison
    db.execute.return_value.all.side_effect = [[(0, 5)], [(0, 2)], [(0, 1)]]

    result = runs_router.list_runs(
        uf=None, source=None, depth=None, offset=0, limit=50, db=db
    )

    assert set(result.keys()) == {"items", "total", "offset", "limit"}
    assert result["total"] == 1
    assert result["offset"] == 0
    assert result["limit"] == 50
    assert len(result["items"]) == 1

    item = result["items"][0]
    assert set(item.keys()) == {
        "id", "started_at", "ended_at", "ufs", "source", "depth",
        "total", "synced", "failed", "status",
    }
    assert item["id"] == str(run.id)
    assert item["ufs"] == ["BA"]
    assert item["source"] == "default"
    assert item["depth"] == "nascente_rio"
    assert item["synced"] == 5
    assert item["failed"] == 3  # 2 rio + 1 poison
    assert item["total"] == 8  # synced + failed
    assert item["status"] == "concluido"


def test_list_runs_filters_uf_over_json_array():
    """uf filter selects only runs whose JSON ufs array contains the uf."""
    db = MagicMock()
    ba = _run(ufs=["BA"])
    se = _run(ufs=["SE"])
    db.scalars.return_value.all.return_value = [ba, se]
    # only the BA run survives the filter → no window rows → zero counts
    db.execute.return_value.all.return_value = []

    result = runs_router.list_runs(
        uf="BA", source=None, depth=None, offset=0, limit=50, db=db
    )
    assert result["total"] == 1
    assert result["items"][0]["id"] == str(ba.id)
    assert result["items"][0]["ufs"] == ["BA"]


def test_list_runs_paginates_after_uf_filter():
    """offset/limit slice the filtered set (total reflects the full filtered count)."""
    db = MagicMock()
    runs = [_run(ufs=["BA"]) for _ in range(3)]
    db.scalars.return_value.all.return_value = list(runs)
    db.scalar.side_effect = [0, 0, 0]  # one survivor on the page

    result = runs_router.list_runs(
        uf="BA", source=None, depth=None, offset=2, limit=1, db=db
    )
    assert result["total"] == 3
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == str(runs[2].id)


# ---------------------------------------------------------------------------
# reprocess_run — a NEW engine run with the original scope (F14) + audit + 404
# ---------------------------------------------------------------------------


@pytest.fixture
def engine_calls(monkeypatch):
    """Capture engine_sweep_run.delay; enabled sources = both lanes."""
    from brave.config import runtime
    from brave.config.settings import AppConfig
    from brave.tasks import pipeline

    calls: list[dict] = []
    monkeypatch.setattr(pipeline.engine_sweep_run, "delay", lambda **kw: calls.append(kw))
    cfg = AppConfig(sources={"default": True, "tripadvisor": True})
    monkeypatch.setattr(runtime, "load_effective_config", lambda session, redis=None: cfg)
    return calls


def test_reprocess_keeps_depth_cap_and_gets_a_run_id(engine_calls):
    """A capped nascente TA test run is reprocessed as exactly that — not a full sweep
    with paid Places per card (the old loose .delay(uf) dropped depth/cap/run_id)."""
    import fakeredis

    from brave.core import engine as collection_engine

    rc = fakeredis.FakeRedis()
    db = MagicMock()
    run = _run(ufs=["BA", "SE"], source="tripadvisor", depth="nascente", lane="atrativos")
    run.max_per_uf = 5
    db.get.return_value = run

    result = runs_router.reprocess_run(run_id=run.id, db=db, redis=rc)

    assert result["status"] == "accepted"
    assert engine_calls == [{
        "ufs": ["BA", "SE"], "lane": "atrativos", "run_id": result["new_run_id"],
        "max_per_uf": 5, "depth": "nascente", "source": "tripadvisor",
    }]
    assert collection_engine.get_status(rc)["state"] == "running"
    db.commit.assert_called()  # audited


def test_reprocess_while_a_run_is_active_is_409(engine_calls):
    import fakeredis

    from brave.core import engine as collection_engine

    rc = fakeredis.FakeRedis()
    collection_engine.start(
        rc, None, action="sweep", depth="nascente_rio", source="tripadvisor",
        ufs=["SP"], lane="atrativos",
    )
    db = MagicMock()
    db.get.return_value = _run(source="tripadvisor")

    with pytest.raises(Exception) as exc:
        runs_router.reprocess_run(run_id=uuid.uuid4(), db=db, redis=rc)
    assert getattr(exc.value, "status_code", None) == 409
    assert engine_calls == []


def test_reprocess_unknown_run_404():
    """reprocess of a missing run returns 404."""
    db = MagicMock()
    db.get.return_value = None
    with pytest.raises(Exception) as exc:
        runs_router.reprocess_run(run_id=uuid.uuid4(), db=db, redis=MagicMock())
    assert getattr(exc.value, "status_code", None) == 404


def test_reprocess_descricao_source_redescribes(engine_calls):
    """A describe run re-describes (action=describe), never the Places discovery lane."""
    import fakeredis

    db = MagicMock()
    run = _run(ufs=["SP"], source="descricao", depth="descricao", lane="atrativos")
    run.max_per_uf = None
    db.get.return_value = run

    result = runs_router.reprocess_run(run_id=run.id, db=db, redis=fakeredis.FakeRedis())
    assert engine_calls == [{
        "ufs": ["SP"], "lane": "atrativos", "run_id": result["new_run_id"],
        "max_per_uf": None, "action": "describe",
    }]
