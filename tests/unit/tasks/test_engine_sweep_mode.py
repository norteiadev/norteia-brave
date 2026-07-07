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


def test_sweep_finally_stays_syncing_while_producers_inflight(monkeypatch, running_engine):
    """Producer-completes model: the orchestrator finally does NOT turn the motor off
    while producers are still in flight.

    engine_sweep_run now incr_inflight()s before every .delay. The _FakeTask producers
    here only record the dispatch (they never run to completion), so the in-flight
    counter stays > 0 after the loop returns. dispatch_done is latched, but maybe_complete
    must return False → the motor stays ON: state RUNNING, mode LIGADO, sync_phase
    "syncing". Completion is the LAST producer's job (next test).
    """
    collection_engine.set_mode(running_engine, collection_engine.LIGADO)
    uf_calls, _disc, _ta = _patch_producers(monkeypatch)
    out = _run()
    assert out["dispatched"] == 3  # the run fanned out normally under LIGADO
    assert collection_engine.get_inflight(running_engine) > 0  # producers still running
    assert collection_engine.is_dispatch_done(running_engine) is True  # dispatch latched
    status = collection_engine.get_status(running_engine)
    assert status["mode"] == collection_engine.LIGADO  # motor NOT turned off yet
    assert status["state"] == collection_engine.RUNNING
    assert status["sync_phase"] == "syncing"


def test_last_producer_completion_turns_motor_off_and_marks_synced(monkeypatch, running_engine):
    """Draining the in-flight counter to 0 (each producer's finally) completes the run
    EXACTLY once: mode DESLIGADO, enabled False, state IDLE, sync_phase "synced".

    Simulates the producers' outermost-finally decrements after engine_sweep_run has
    dispatched them and latched dispatch_done. maybe_complete must return True for a
    single caller (the last decrement) and False for every other → single-winner.
    """
    collection_engine.set_mode(running_engine, collection_engine.LIGADO)
    _patch_producers(monkeypatch)
    _run()

    n = collection_engine.get_inflight(running_engine)
    assert n > 0, "precondition: producers were counted in-flight"

    completed = 0
    for _ in range(n):
        collection_engine.decr_inflight(running_engine)
        if collection_engine.maybe_complete(running_engine):
            completed += 1
    assert completed == 1, "exactly one decrement (the last) may complete the run"

    status = collection_engine.get_status(running_engine)
    assert status["mode"] == collection_engine.DESLIGADO
    assert status["enabled"] is False
    assert status["state"] == collection_engine.IDLE
    assert status["sync_phase"] == "synced"


def test_producer_lifecycle_skips_decrement_on_celery_retry(monkeypatch):
    """A Celery Retry unwinding through a producer's finally must NOT decrement the
    in-flight counter.

    incr_inflight fires ONCE per logical dispatch, but self.retry() raises Retry and
    Celery RE-RUNS the task (its finally runs again). Decrementing on the Retry path
    would count N retries as N+1 decrements → the counter drains early → premature
    "synced" while the retried producer is still running (the network-scraper common
    case). Only terminal outcomes may decrement.
    """
    from celery.exceptions import Retry

    from brave.tasks.pipeline import _producer_finally_lifecycle

    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    # inflight=2, dispatch NOT done → maybe_complete never fires, isolating the decrement.
    fake.set(collection_engine._INFLIGHT_KEY, "2")

    # Retry in flight → guard skips the decrement.
    try:
        raise Retry("scheduled for retry")
    except Retry:
        _producer_finally_lifecycle()
    assert collection_engine.get_inflight(fake) == 2, (
        "a Celery Retry unwinding through the finally must NOT decrement inflight"
    )

    # Terminal outcome (no exception in flight) → decrements exactly once.
    _producer_finally_lifecycle()
    assert collection_engine.get_inflight(fake) == 1
