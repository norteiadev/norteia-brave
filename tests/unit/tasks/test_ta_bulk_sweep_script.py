"""scripts/ta_bulk_sweep.py gives the bulk its own engine run (F55) and an honest exit code.

The sweep task itself is faked (its branch has its own suite); fakeredis, MagicMock
session, no broker."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import pytest

from brave.config.settings import AppConfig
from brave.core import engine as collection_engine
from brave.domains.tripadvisor import sweep_progress

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ta_bulk_sweep.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("ta_bulk_sweep", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(monkeypatch):
    from brave.tasks import pipeline

    rc = fakeredis.FakeRedis()
    monkeypatch.setattr("redis.from_url", lambda *a, **k: rc)
    monkeypatch.setattr(pipeline, "_get_session", lambda: (MagicMock(), None))
    monkeypatch.setattr(
        "brave.config.runtime.load_effective_config", lambda session, redis=None: AppConfig()
    )
    calls: list[dict] = []
    outcome = {"state": "done"}

    def _raw(task, uf, depth, **kw):
        calls.append({"uf": uf, "depth": depth, **kw})
        # What the real bulk sees: the motor must be LIGADO for produce_paginated to run.
        assert collection_engine.get_mode(rc) == collection_engine.LIGADO
        sweep_progress.start(rc, pages_total=334)
        getattr(sweep_progress, {"done": "mark_done", "stopped": "stop"}[outcome["state"]])(rc)
        pipeline._producer_done(kw["run_id"])

    fake_task = SimpleNamespace(
        __wrapped__=SimpleNamespace(__func__=_raw),
        delay=lambda *a, **kw: (calls.append({"enqueued": a, **kw}), SimpleNamespace(id="t1"))[1],
    )
    monkeypatch.setattr(pipeline, "sweep_tripadvisor", fake_task)
    return SimpleNamespace(rc=rc, calls=calls, outcome=outcome)


def test_inline_bulk_runs_inside_its_own_engine_run(env):
    """A seeded DESLIGADO no longer stops the bulk at page 1: the script's run turns the
    motor LIGADO, the task gets the run token, and the run completes after it."""
    collection_engine.set_mode(env.rc, collection_engine.DESLIGADO)

    assert _load_script().main(["--max-pages", "3", "--fresh"]) == 0

    (call,) = env.calls
    assert call["run_id"] and call["fresh"] is True and call["max_pages"] == 3
    status = collection_engine.get_status(env.rc)
    assert status["state"] == collection_engine.IDLE and status["sync_phase"] == "synced"
    assert status["ufs_total"] == 3  # pages, not one "UF"


def test_inline_bulk_that_did_not_finish_exits_1(env):
    env.outcome["state"] = "stopped"
    assert _load_script().main(["--max-pages", "3"]) == 1


def test_busy_motor_exits_2_without_dispatch(env):
    collection_engine.start(
        env.rc, None, action="sweep", depth="nascente_rio", source="tripadvisor",
        ufs=["SP"], lane="atrativos",
    )
    assert _load_script().main(["--max-pages", "3"]) == 2
    assert env.calls == []


def test_enqueue_passes_the_run_token(env):
    assert _load_script().main(["--max-pages", "3", "--enqueue"]) == 0
    (call,) = env.calls
    assert call["enqueued"] == ("BR", None) and call["run_id"]
    # Dispatch is done; the queued producer keeps the run open.
    assert collection_engine.get_status(env.rc)["state"] == collection_engine.RUNNING
