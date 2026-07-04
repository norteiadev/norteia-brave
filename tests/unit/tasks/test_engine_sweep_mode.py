"""Unit tests: engine_sweep_run honors the operator mode (Motor Pausado, phase C).

The orchestrator loop already drains on the runtime-state axis (STOPPING/idle). Phase
C adds an ORTHOGONAL guard: it also breaks when the operator mode is no longer LIGADO
(PAUSADO or DESLIGADO) — no new UFs, no auto-push — while the state-drain contract
stays intact. Mode is read per-UF from Redis, so a mid-run pause takes effect on the
next iteration and the finally block still idles + finalizes the run.

Mirrors tests/unit/api/test_engine_source.py: fakeredis with state=RUNNING, monkeypatched
redis.from_url + producer tasks, zero per-UF delay. 100% offline.
"""

from __future__ import annotations

import fakeredis
import pytest

from brave.core import engine as collection_engine


@pytest.fixture
def running_engine(monkeypatch):
    """Fakeredis with engine state=RUNNING (mode absent → LIGADO) and no per-UF delay."""
    fake = fakeredis.FakeStrictRedis()
    fake.set(collection_engine._STATE_KEY, collection_engine.RUNNING)
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    monkeypatch.setenv("BRAVE_ENGINE_UF_DELAY_SECONDS", "0")
    return fake


class _FakeTask:
    def __init__(self, sink):
        self._sink = sink

    def delay(self, *args, **kwargs):
        self._sink.append((args, kwargs))


def _patch_producers(monkeypatch):
    from brave.tasks import pipeline

    uf_calls: list = []
    discover_calls: list = []
    ta_calls: list = []
    monkeypatch.setattr(pipeline, "sweep_uf", _FakeTask(uf_calls))
    monkeypatch.setattr(pipeline, "discover_atrativo_task", _FakeTask(discover_calls))
    monkeypatch.setattr(pipeline, "sweep_tripadvisor", _FakeTask(ta_calls))
    return uf_calls, discover_calls, ta_calls


def _run(ufs=("BA", "RJ", "SP")):
    from brave.tasks import pipeline

    return pipeline.engine_sweep_run.run(
        ufs=list(ufs),
        lane="both",
        depth=collection_engine.NASCENTE_RIO,
        source="default",
    )


def test_sweep_dispatches_when_mode_absent_defaults_ligado(monkeypatch, running_engine):
    """No mode key → get_mode defaults LIGADO → the sweep fans out (no regression)."""
    uf_calls, _disc, _ta = _patch_producers(monkeypatch)
    out = _run()
    assert out["dispatched"] == 3
    assert len(uf_calls) == 3


def test_sweep_dispatches_when_mode_ligado(monkeypatch, running_engine):
    collection_engine.set_mode(running_engine, collection_engine.LIGADO)
    uf_calls, _disc, _ta = _patch_producers(monkeypatch)
    out = _run()
    assert out["dispatched"] == 3
    assert len(uf_calls) == 3


def test_sweep_breaks_immediately_when_mode_pausado(monkeypatch, running_engine):
    """PAUSADO breaks the loop before the first dispatch — runtime state stays RUNNING."""
    collection_engine.set_mode(running_engine, collection_engine.PAUSADO)
    uf_calls, disc_calls, ta_calls = _patch_producers(monkeypatch)
    out = _run()
    assert out["dispatched"] == 0
    assert len(uf_calls) == 0
    assert len(disc_calls) == 0
    assert len(ta_calls) == 0
    # finally block still idled the engine (graceful finalize).
    assert collection_engine.get_state(running_engine) == collection_engine.IDLE


def test_sweep_breaks_when_mode_desligado(monkeypatch, running_engine):
    """DESLIGADO breaks too (set_mode also idled the engine → 0 dispatched)."""
    collection_engine.set_mode(running_engine, collection_engine.DESLIGADO)
    uf_calls, _disc, _ta = _patch_producers(monkeypatch)
    out = _run(ufs=("BA", "RJ"))
    assert out["dispatched"] == 0
    assert len(uf_calls) == 0


def test_runtime_state_drain_still_breaks_independent_of_mode(monkeypatch, running_engine):
    """The pre-existing state-drain contract is intact: STOPPING breaks even with mode LIGADO."""
    collection_engine.set_mode(running_engine, collection_engine.LIGADO)
    running_engine.set(collection_engine._STATE_KEY, collection_engine.STOPPING)
    uf_calls, _disc, _ta = _patch_producers(monkeypatch)
    out = _run(ufs=("BA", "RJ"))
    assert out["dispatched"] == 0
    assert len(uf_calls) == 0


def test_sweep_finally_turns_motor_off_and_marks_synced(monkeypatch, running_engine):
    """BUG 2: at sweep end the finally block turns the motor OFF and flips to synced.

    After a normal LIGADO run drains, the finally block runs set_mode(DESLIGADO)
    (mark_idle + enabled False + mode off, redis-only) then mark_run_ended, so the
    engine lands: mode DESLIGADO, enabled False, state IDLE, sync_phase "synced".
    """
    collection_engine.set_mode(running_engine, collection_engine.LIGADO)
    uf_calls, _disc, _ta = _patch_producers(monkeypatch)
    out = _run()
    assert out["dispatched"] == 3  # the run fanned out normally under LIGADO
    status = collection_engine.get_status(running_engine)
    assert status["mode"] == collection_engine.DESLIGADO
    assert status["enabled"] is False
    assert status["state"] == collection_engine.IDLE
    assert status["sync_phase"] == "synced"
