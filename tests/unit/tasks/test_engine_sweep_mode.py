"""Unit tests: engine_sweep_run honors the operator mode (Motor Pausado, phase C).

The orchestrator loop already drains on the runtime-state axis (STOPPING/idle). Phase
C adds an ORTHOGONAL guard: it also breaks when the operator mode is no longer LIGADO
(PAUSADO or DESLIGADO) — no new UFs, no auto-push — while the state-drain contract
stays intact. Mode is read per-UF from Redis, so a mid-run pause takes effect on the
next iteration and the finally block still idles + finalizes the run.

Mirrors tests/unit/api/test_engine_source.py: fakeredis with a started run, monkeypatched
redis.from_url + producer tasks, zero per-UF delay. 100% offline.
"""

from __future__ import annotations

import fakeredis
import pytest

from brave.core import engine as collection_engine


@pytest.fixture
def running_engine(monkeypatch):
    """Fakeredis with a started run (state RUNNING, mode LIGADO) and no per-UF delay."""
    fake = fakeredis.FakeStrictRedis()
    collection_engine.start(
        fake,
        None,
        action="sweep",
        depth=collection_engine.NASCENTE_RIO,
        source="default",
        ufs=["BA", "RJ", "SP"],
        lane="both",
    )
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

    discover_calls: list = []
    ta_calls: list = []
    monkeypatch.setattr(pipeline, "discover_atrativo_task", _FakeTask(discover_calls))
    monkeypatch.setattr(pipeline, "sweep_tripadvisor", _FakeTask(ta_calls))
    # The default (Places) lane dispatches discover_atrativo_task per UF; the retired
    # Mtur sweep_uf producer is gone. Returned first so existing call-sites read the
    # default lane's producer as the primary spy.
    return discover_calls, discover_calls, ta_calls


def _run(ufs=("BA", "RJ", "SP")):
    from brave.tasks import pipeline

    return pipeline.engine_sweep_run.run(
        ufs=list(ufs),
        lane="both",
        depth=collection_engine.NASCENTE_RIO,
        source="default",
    )


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
    collection_engine.request_stop(running_engine)
    uf_calls, _disc, _ta = _patch_producers(monkeypatch)
    out = _run(ufs=("BA", "RJ"))
    assert out["dispatched"] == 0
    assert len(uf_calls) == 0


def test_sweep_finally_stays_syncing_while_producers_inflight(monkeypatch, running_engine):
    """Producer-completes model: the orchestrator finally does NOT turn the motor off
    while producers are still in flight.

    engine_sweep_run claims every producer before its .delay. The _FakeTask producers
    here only record the dispatch (they never run to completion), so the run cannot
    complete when the loop returns → the motor stays ON: state RUNNING, mode LIGADO,
    sync_phase "syncing". Completion is the LAST producer's job (next test).
    """
    collection_engine.set_mode(running_engine, collection_engine.LIGADO)
    uf_calls, _disc, _ta = _patch_producers(monkeypatch)
    out = _run()
    assert out["dispatched"] == 3  # the run fanned out normally under LIGADO
    assert collection_engine.get_status(running_engine)["ufs_done"] == 3
    status = collection_engine.get_status(running_engine)
    assert status["mode"] == collection_engine.LIGADO  # motor NOT turned off yet
    assert status["state"] == collection_engine.RUNNING
    assert status["sync_phase"] == "syncing"


def test_last_producer_completion_turns_motor_off_and_marks_synced(monkeypatch, running_engine):
    """Draining the in-flight counter to 0 (each producer's finally) completes the run
    EXACTLY once: mode DESLIGADO, enabled False, state IDLE, sync_phase "synced".

    Simulates the producers' outermost-finally producer_done after engine_sweep_run has
    dispatched them (each with the run_id it was claimed against). Only the last one
    completes the run → single-winner.
    """
    collection_engine.set_mode(running_engine, collection_engine.LIGADO)
    uf_calls, _disc, _ta = _patch_producers(monkeypatch)
    _run()
    assert uf_calls, "precondition: producers were dispatched"

    completed = [
        collection_engine.producer_done(running_engine, None, kwargs["run_id"])
        for _args, kwargs in uf_calls
    ]
    assert completed.count(True) == 1 and completed[-1] is True

    status = collection_engine.get_status(running_engine)
    assert status["mode"] == collection_engine.DESLIGADO
    assert status["enabled"] is False
    assert status["state"] == collection_engine.IDLE
    assert status["sync_phase"] == "synced"


def test_producer_lifecycle_skips_decrement_on_celery_retry(monkeypatch):
    """A Celery Retry unwinding through a producer's finally must NOT count as done.

    claim_producer fires ONCE per logical dispatch, but self.retry() raises Retry and
    Celery RE-RUNS the task (its finally runs again). Decrementing on the Retry path
    would count N retries as N+1 decrements → the counter drains early → premature
    "synced" while the retried producer is still running (the network-scraper common
    case). Only terminal outcomes may decrement.
    """
    from celery.exceptions import Retry

    from brave.tasks.pipeline import _producer_done

    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    run_id = collection_engine.start(
        fake, None, action="sweep", depth=collection_engine.NASCENTE_RIO,
        source="default", ufs=["BA"], lane="both",
    )
    collection_engine.claim_producer(fake, run_id)
    collection_engine.dispatch_finished(fake, None, run_id)  # only the producer is left

    # Retry in flight → guard skips producer_done: the run is still running.
    try:
        raise Retry("scheduled for retry")
    except Retry:
        _producer_done(run_id)
    assert collection_engine.get_state(fake) == collection_engine.RUNNING, (
        "a Celery Retry unwinding through the finally must NOT count the producer done"
    )

    # Terminal outcome (no exception in flight) → the producer completes the run.
    _producer_done(run_id)
    assert collection_engine.get_state(fake) == collection_engine.IDLE
