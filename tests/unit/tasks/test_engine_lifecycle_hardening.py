"""Engine run lifecycle hardening: dispatch idempotency + claim compensation (F63), failed
producers end the run "parcial" (F67), a run's own unit count (units_total). Fakeredis,
no broker, no DB."""

from __future__ import annotations

import fakeredis
import pytest

from brave.core import engine as collection_engine
from brave.tasks import pipeline


@pytest.fixture
def rc(monkeypatch):
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    monkeypatch.setenv("BRAVE_ENGINE_UF_DELAY_SECONDS", "0")
    return fake


def _start(rc, ufs):
    return collection_engine.start(
        rc, None, action="sweep", depth=collection_engine.NASCENTE_RIO,
        source="default", ufs=list(ufs), lane="both",
    )


class _Producer:
    def __init__(self, fail_on: str | None = None):
        self.sent: list[str] = []
        self.fail_on = fail_on

    def delay(self, uf, *args, **kwargs):
        if uf == self.fail_on:
            raise ConnectionError("broker down")
        self.sent.append(uf)


def _sweep(run_id, ufs):
    return pipeline.engine_sweep_run.run(
        ufs=list(ufs), lane="both", depth=collection_engine.NASCENTE_RIO,
        source="default", run_id=run_id,
    )


def test_redelivered_orchestrator_does_not_dispatch_twice(rc, monkeypatch):
    """acks_late redelivery re-runs the loop from the first UF: already-dispatched UFs
    are skipped (no second concurrent producer, no double Places spend)."""
    producer = _Producer()
    monkeypatch.setattr(pipeline, "discover_atrativo_task", producer)
    run_id = _start(rc, ["BA", "RJ"])

    _sweep(run_id, ["BA", "RJ"])
    rc.set("brave:engine:state", collection_engine.RUNNING)  # as if the worker died mid-run
    rc.delete("brave:engine:last_run_ended")
    _sweep(run_id, ["BA", "RJ"])

    assert producer.sent == ["BA", "RJ"]
    assert int(rc.get("brave:engine:producers_inflight")) == 2  # not 4


def test_broker_failure_gives_the_claim_back(rc, monkeypatch):
    """A failed .delay used to leak its +1: the run sat RUNNING forever (409 on start)."""
    monkeypatch.setattr(pipeline, "discover_atrativo_task", _Producer(fail_on="RJ"))
    run_id = _start(rc, ["BA", "RJ"])

    with pytest.raises(ConnectionError):
        _sweep(run_id, ["BA", "RJ"])

    assert int(rc.get("brave:engine:producers_inflight")) == 1  # only BA's
    pipeline._producer_done(run_id)  # BA finishes
    assert collection_engine.get_state(rc) == collection_engine.IDLE


def test_failed_producer_ends_the_run_parcial(rc, monkeypatch):
    """F67: every UF dispatched, but one producer ended without doing its work (exhausted
    retries / swallowed PermanentError) → not "concluido"."""
    finalized: list[str] = []
    monkeypatch.setattr(
        collection_engine, "_finalize", lambda session, run_id, status, n: finalized.append(status)
    )
    run_id = _start(rc, ["BA", "RJ"])
    for _ in range(2):
        collection_engine.claim_producer(rc, run_id)
        collection_engine.progress(rc, run_id)
    collection_engine.dispatch_finished(rc, None, run_id)

    collection_engine.producer_done(rc, None, run_id)
    collection_engine.producer_done(rc, None, run_id, failed=True)

    assert finalized == ["parcial"]


def test_clean_run_still_concluido(rc, monkeypatch):
    finalized: list[str] = []
    monkeypatch.setattr(
        collection_engine, "_finalize", lambda session, run_id, status, n: finalized.append(status)
    )
    run_id = _start(rc, ["BA"])
    collection_engine.claim_producer(rc, run_id)
    collection_engine.progress(rc, run_id)
    collection_engine.dispatch_finished(rc, None, run_id)
    collection_engine.producer_done(rc, None, run_id)
    assert finalized == ["concluido"]

    # The failure counter is per run: a new run starts clean.
    run2 = _start(rc, ["BA"])
    assert int(rc.get("brave:engine:producers_failed")) == 0
    assert run2


def test_policy_swallowed_failure_reports_failed(rc, monkeypatch):
    """A producer whose body raised PermanentError (swallowed → SUCCESS) still reports
    failed: its ok flag never reached True."""
    from unittest.mock import MagicMock

    from brave.shared.exceptions import PermanentError

    run_id = _start(rc, ["BA"])
    claimed = collection_engine.claim_producer(rc, run_id)
    collection_engine.progress(rc, run_id)
    collection_engine.dispatch_finished(rc, None, run_id)
    session = MagicMock()
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, None))

    def _boom(_s):
        raise PermanentError("bad config")

    monkeypatch.setattr(pipeline, "_load_config", _boom)
    monkeypatch.setattr("brave.tasks.failure_policy._quarantine", lambda *a, **k: None)

    pipeline.discover_atrativo_task.__wrapped__.__func__(MagicMock(), "BA", run_id=claimed)

    assert int(rc.get("brave:engine:producers_failed")) == 1


def test_units_total_overrides_the_uf_count(rc):
    _ = collection_engine.start(
        rc, None, action="sweep", depth=collection_engine.NASCENTE_RIO,
        source="tripadvisor", ufs=["BR"], lane="tripadvisor_bulk", units_total=334,
    )
    assert collection_engine.get_status(rc)["ufs_total"] == 334
