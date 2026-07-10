"""Offline unit tests for the Varreduras runs list + reprocess (UI-PAINEL-2).

Fully offline (MagicMock session, no DB, no broker, RUN_REAL_EXTERNALS unset).
Covers:
  - on-read window aggregation (_window_counts): synced from Mar, failed from
    Rio dlq/descarte + PoisonQuarantine.
  - list_runs envelope shape is exact + uf is filtered over the JSON ufs array.
  - reprocess_run re-dispatches the scope via the offline inline fallback (broker
    down → task.run inline) without raising, audits, and 404s on an unknown run.
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
    """synced = Mar count; failed = rio(dlq/descarte) + poison counts."""
    db = MagicMock()
    # order of db.scalar calls: synced, failed_rio, failed_poison
    db.scalar.side_effect = [7, 3, 2]

    synced, failed = runs_router._window_counts(
        db,
        datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 28, 11, 0, tzinfo=timezone.utc),
    )
    assert synced == 7
    assert failed == 5  # 3 rio + 2 poison


def test_window_counts_uses_now_when_ended_at_none():
    """A still-running run (ended_at=None) aggregates up to now() without error."""
    db = MagicMock()
    db.scalar.side_effect = [1, 0, 0]
    synced, failed = runs_router._window_counts(
        db, datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc), None
    )
    assert synced == 1
    assert failed == 0


# ---------------------------------------------------------------------------
# list_runs envelope + uf filtering
# ---------------------------------------------------------------------------


def test_list_runs_envelope_shape_and_on_read_counts():
    """list_runs returns the exact paginated envelope with on-read synced/failed/total."""
    db = MagicMock()
    run = _run()
    db.scalars.return_value.all.return_value = [run]
    # window counts for the single run: synced, failed_rio, failed_poison
    db.scalar.side_effect = [5, 2, 1]

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
    # only the BA run survives the filter → one window-count triple
    db.scalar.side_effect = [0, 0, 0]

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
# reprocess_run — offline inline fallback + audit + 404
# ---------------------------------------------------------------------------


class _BrokerDownTask:
    """A producer task whose .delay() fails (no broker) so _dispatch runs inline."""

    def __init__(self):
        self.ran: list[str] = []

    def delay(self, uf):
        raise RuntimeError("broker unavailable")

    def run(self, uf):
        self.ran.append(uf)


def test_reprocess_default_source_runs_inline(monkeypatch):
    """reprocess for a default-source run dispatches discover inline (no raise).

    The Mtur destino seed is retired — the destinos lane has no producer, so only
    discover_atrativo_task re-dispatches for the default (Places) source.
    """
    from brave.tasks import pipeline

    discover = _BrokerDownTask()
    monkeypatch.setattr(pipeline, "discover_atrativo_task", discover)

    db = MagicMock()
    run = _run(ufs=["BA", "SE"], source="default", lane="both")
    db.get.return_value = run

    result = runs_router.reprocess_run(run_id=run.id, db=db)

    assert result["status"] == "accepted"
    assert result["ufs"] == ["BA", "SE"]
    # the atrativos producer ran inline for each UF
    assert discover.ran == ["BA", "SE"]
    # audited + committed
    db.commit.assert_called()


def test_reprocess_tripadvisor_source_runs_inline(monkeypatch):
    """reprocess for a tripadvisor run dispatches sweep_tripadvisor inline only."""
    from brave.tasks import pipeline

    ta = _BrokerDownTask()
    discover = _BrokerDownTask()
    monkeypatch.setattr(pipeline, "sweep_tripadvisor", ta)
    monkeypatch.setattr(pipeline, "discover_atrativo_task", discover)

    db = MagicMock()
    run = _run(ufs=["BA"], source="tripadvisor", lane="both")
    db.get.return_value = run

    result = runs_router.reprocess_run(run_id=run.id, db=db)
    assert result["status"] == "accepted"
    assert ta.ran == ["BA"]
    assert discover.ran == []  # default-lane producers not used for TA source


def test_reprocess_unknown_run_404():
    """reprocess of a missing run returns 404."""
    db = MagicMock()
    db.get.return_value = None
    with pytest.raises(Exception) as exc:
        runs_router.reprocess_run(run_id=uuid.uuid4(), db=db)
    assert getattr(exc.value, "status_code", None) == 404
