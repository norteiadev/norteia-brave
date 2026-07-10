"""Offline write-path unit tests for the runs_history trail (UI-PAINEL-2).

Focused, fully-offline tests (fakeredis + MagicMock session — NO DB, NO broker,
NOT @pytest.mark.integration). They prove the two security/correctness invariants
of the runs_history write path:

  T-17.1-02-03 (no phantom rows): engine_start inserts NO RunHistory row when the
    start is rejected by the depth 422, the source 422, or the start_run() 409
    (already-running) guard. A row is inserted exactly once — only AFTER start_run()
    returns True — and run_id is persisted to Redis (brave:engine:run_id).

  T-17.1-02-02 (best-effort finalize): the finalize UPDATE in engine_sweep_run's
    finally block swallows any write failure and NEVER aborts the sweep.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import fakeredis
import pytest

from brave.api.routers.engine import engine_start
from brave.core import engine as collection_engine
from brave.core.models import RunHistory

RUN_ID_KEY = "brave:engine:run_id"


def _added_run_histories(db: MagicMock) -> list[RunHistory]:
    """All RunHistory instances passed to db.add() across the call."""
    return [
        call.args[0]
        for call in db.add.call_args_list
        if call.args and isinstance(call.args[0], RunHistory)
    ]


# ---------------------------------------------------------------------------
# (a) No phantom row on a rejected start
# ---------------------------------------------------------------------------


def test_no_row_on_invalid_depth(monkeypatch):
    """A missing/invalid depth → 422 before any RunHistory.add (no phantom row)."""
    fake = fakeredis.FakeStrictRedis()
    db = MagicMock()

    with pytest.raises(Exception) as exc:
        engine_start(redis=fake, body={"ufs": ["BA"]}, db=db)
    assert getattr(exc.value, "status_code", None) == 422

    assert _added_run_histories(db) == [], "rejected (bad depth) start must not add a row"
    assert fake.get(RUN_ID_KEY) is None
    assert collection_engine.get_state(fake) == collection_engine.IDLE


def test_no_row_on_invalid_source(monkeypatch):
    """An invalid source → 422 before any RunHistory.add (no phantom row)."""
    fake = fakeredis.FakeStrictRedis()
    db = MagicMock()

    with pytest.raises(Exception) as exc:
        engine_start(
            redis=fake,
            body={"ufs": ["BA"], "depth": "nascente", "source": "bogus"},
            db=db,
        )
    assert getattr(exc.value, "status_code", None) == 422

    assert _added_run_histories(db) == [], "rejected (bad source) start must not add a row"
    assert fake.get(RUN_ID_KEY) is None


def test_no_row_on_already_running_409(monkeypatch):
    """start_run() returning False (engine already running) → 409, no RunHistory.add."""
    fake = fakeredis.FakeStrictRedis()
    # Engine already running → start_run() will return False → 409. Use tripadvisor
    # (the live lane; 'default'/Places ships dormant) with a seeded session so the
    # 409 comes from the already-running check, not source validation.
    fake.set(collection_engine._STATE_KEY, collection_engine.RUNNING)
    fake.setex("brave:ta:session", 3600, '{"cookies":{}}')
    db = MagicMock()

    with pytest.raises(Exception) as exc:
        engine_start(
            redis=fake,
            body={"ufs": ["BA"], "depth": "nascente", "source": "tripadvisor"},
            db=db,
        )
    assert getattr(exc.value, "status_code", None) == 409

    assert _added_run_histories(db) == [], "409 already-running must not add a row"
    assert fake.get(RUN_ID_KEY) is None


# ---------------------------------------------------------------------------
# (b) Exactly one row after start_run() succeeds + run_id persisted to Redis
# ---------------------------------------------------------------------------


def test_one_row_after_successful_start(monkeypatch):
    """A valid start inserts exactly one RunHistory row and persists run_id to Redis."""
    import brave.tasks.pipeline as pipeline

    # Avoid touching a broker — stub the dispatch.
    monkeypatch.setattr(pipeline.engine_sweep_run, "delay", lambda *a, **k: None)

    fake = fakeredis.FakeStrictRedis()
    # 'default' (Places) ships dormant — tripadvisor is the live sweep lane. Seed a
    # valid TA session so the R2 gate passes.
    fake.setex("brave:ta:session", 3600, '{"cookies":{}}')
    db = MagicMock()

    result = engine_start(
        redis=fake,
        body={"ufs": ["BA", "SE"], "depth": "nascente_rio", "source": "tripadvisor", "lane": "both"},
        db=db,
    )
    assert result["status"] == "started"

    rows = _added_run_histories(db)
    assert len(rows) == 1, f"expected exactly one RunHistory row, got {len(rows)}"
    run = rows[0]
    assert run.status == "running"
    assert run.ufs == ["BA", "SE"]
    assert run.source == "tripadvisor"
    assert run.depth == "nascente_rio"
    assert run.lane == "both"
    assert run.ufs_total == 2
    db.commit.assert_called()

    # run_id persisted to Redis for the orchestrator/status to find.
    persisted = fake.get(RUN_ID_KEY)
    assert persisted is not None
    assert persisted.decode() == str(run.id)


def test_start_proceeds_when_runs_history_write_fails(monkeypatch):
    """A runs_history INSERT failure must NOT abort an otherwise-valid start (best-effort)."""
    import brave.tasks.pipeline as pipeline

    monkeypatch.setattr(pipeline.engine_sweep_run, "delay", lambda *a, **k: None)

    fake = fakeredis.FakeStrictRedis()
    # tripadvisor is the live sweep lane ('default'/Places ships dormant); seed its session.
    fake.setex("brave:ta:session", 3600, '{"cookies":{}}')
    db = MagicMock()
    db.commit.side_effect = RuntimeError("db down")

    # Must NOT raise — the write is best-effort.
    result = engine_start(
        redis=fake,
        body={"ufs": ["BA"], "depth": "nascente", "source": "tripadvisor"},
        db=db,
    )
    assert result["status"] == "started"
    # No run_id persisted because the write failed.
    assert fake.get(RUN_ID_KEY) is None


# ---------------------------------------------------------------------------
# (c) Finalize UPDATE swallows a write failure without aborting the sweep
# ---------------------------------------------------------------------------


@pytest.fixture
def running_engine(monkeypatch):
    """Fakeredis with engine state=RUNNING and no per-UF delay."""
    fake = fakeredis.FakeStrictRedis()
    fake.set(collection_engine._STATE_KEY, collection_engine.RUNNING)
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    monkeypatch.setenv("BRAVE_ENGINE_UF_DELAY_SECONDS", "0")
    return fake


class _FakeTask:
    """Stand-in for a dispatched producer under the producer-completes model.

    engine_sweep_run now incr_inflight()s before each .delay and the run only finalizes
    once the in-flight counter drains to 0. A real producer decrements in its outermost
    finally; this fake simulates an INSTANTLY-completing producer by decrementing on
    .delay, so engine_sweep_run's own finally observes inflight==0 and finalizes the run.
    """

    def __init__(self, rc=None):
        self._rc = rc

    def delay(self, *args, **kwargs):
        if self._rc is not None:
            collection_engine.decr_inflight(self._rc)
        return None


def test_finalize_swallows_write_error_and_completes(monkeypatch, running_engine):
    """A raised finalize-UPDATE error is swallowed; the sweep still returns normally."""
    from brave.tasks import pipeline

    # Faked producer tasks so the loop dispatches without real work. They decrement
    # inflight on .delay (instant completion) so the orchestrator finally finalizes.
    monkeypatch.setattr(pipeline, "discover_atrativo_task", _FakeTask(running_engine))
    monkeypatch.setattr(pipeline, "sweep_tripadvisor", _FakeTask(running_engine))

    # _get_session returns a session whose commit RAISES on the finalize UPDATE.
    raising_session = MagicMock()
    raising_session.get.return_value = MagicMock()  # a "row" to update
    raising_session.commit.side_effect = RuntimeError("finalize write boom")
    monkeypatch.setattr(pipeline, "_get_session", lambda: (raising_session, MagicMock()))

    # Must NOT propagate the finalize error.
    result = pipeline.engine_sweep_run.run(
        ufs=["BA"],
        lane="both",
        depth=collection_engine.NASCENTE_RIO,
        source="default",
        run_id=str(uuid.uuid4()),
    )
    assert result["dispatched"] == 1
    # Finalize was attempted (commit called) and the error was swallowed.
    raising_session.commit.assert_called_once()
    # Engine returned to idle despite the finalize failure.
    assert collection_engine.get_state(running_engine) == collection_engine.IDLE


def test_finalize_skipped_when_no_run_id(monkeypatch, running_engine):
    """When run_id is None (no DB trail), finalize is skipped entirely (no _get_session)."""
    from brave.tasks import pipeline

    monkeypatch.setattr(pipeline, "discover_atrativo_task", _FakeTask(running_engine))
    monkeypatch.setattr(pipeline, "sweep_tripadvisor", _FakeTask(running_engine))

    sentinel = MagicMock(side_effect=AssertionError("_get_session must not be called"))
    monkeypatch.setattr(pipeline, "_get_session", sentinel)

    result = pipeline.engine_sweep_run.run(
        ufs=["BA"],
        lane="both",
        depth=collection_engine.NASCENTE_RIO,
        source="default",
        run_id=None,
    )
    assert result["dispatched"] == 1
    sentinel.assert_not_called()
