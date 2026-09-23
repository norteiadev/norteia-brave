"""Unit tests for the Redis-backed collection-engine state machine."""

import uuid

import fakeredis
import pytest

from brave.core import engine
from brave.core.models import RunHistory


class FakeSession:
    """Just enough Session for the engine's config_settings + runs_history writes."""

    def __init__(self):
        self.rows = {}

    def get(self, model, key):
        return self.rows.get((model, key))

    def add(self, row):
        key = row.id if isinstance(row, RunHistory) else row.key
        self.rows[(type(row), key)] = row

    def flush(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def run(self, run_id):
        return self.rows[(RunHistory, uuid.UUID(run_id))]


def _start(redis, session=None, ufs=("BA", "RJ", "SP"), action="sweep"):
    return engine.start(
        redis,
        session,
        action=action,
        depth=engine.NASCENTE_RIO,
        source="tripadvisor",
        ufs=list(ufs),
        lane="atrativos",
    )


def _run_to_completion(redis, session=None):
    run_id = _start(redis, session)
    engine.dispatch_finished(redis, session, run_id)
    return run_id


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


def test_default_state_is_idle(redis):
    assert engine.get_state(redis) == engine.IDLE
    assert engine.is_running(redis) is False


def test_start_transitions_to_running_and_sets_totals(redis):
    run_id = _start(redis, ufs=["BA"] * 27)
    assert run_id
    assert engine.get_state(redis) == engine.RUNNING
    assert engine.is_running(redis) is True
    status = engine.get_status(redis)
    assert status["ufs_total"] == 27
    assert status["ufs_done"] == 0
    assert status["current_uf"] is None
    assert status["depth"] == engine.NASCENTE_RIO
    assert status["source"] == "tripadvisor"
    assert status["mode"] == engine.LIGADO


def test_start_is_idempotent_while_active(redis):
    assert _start(redis) is not None
    # A second start while running is a no-op (never stacks orchestrators).
    assert _start(redis) is None
    engine.request_stop(redis)
    assert _start(redis) is None  # stopping is also active


def test_start_describe_leaves_depth_and_source_unset(redis):
    session = FakeSession()
    run_id = engine.start(
        redis, session, action="describe", depth="descricao", source="descricao",
        ufs=["BA"], lane="atrativos",
    )
    assert engine.get_depth(redis) is None
    assert engine.get_source(redis) is None
    row = session.run(run_id)
    assert (row.depth, row.source, row.status) == ("descricao", "descricao", "running")


def test_start_inserts_running_row_and_sets_ligado_durably(redis):
    session = FakeSession()
    engine.set_mode(redis, engine.DESLIGADO)
    run_id = _start(redis, session)
    row = session.run(run_id)
    assert row.status == "running"
    assert row.ufs == ["BA", "RJ", "SP"] and row.ufs_total == 3 and row.lane == "atrativos"
    assert engine.get_mode(redis) == engine.LIGADO
    assert engine._read_persisted_mode(session) == engine.LIGADO


def test_start_survives_runs_history_write_failure(redis):
    class Broken(FakeSession):
        def commit(self):
            raise RuntimeError("db down")

    assert _start(redis, Broken()) is not None
    assert engine.get_state(redis) == engine.RUNNING


def test_progress_tracking(redis):
    run_id = _start(redis)
    engine.progress(redis, run_id, uf="BA")
    engine.progress(redis, run_id, uf="RJ")
    status = engine.get_status(redis)
    assert status["current_uf"] == "RJ"
    assert status["ufs_done"] == 2
    assert status["ufs_total"] == 3


def test_request_stop_only_from_running(redis):
    assert engine.request_stop(redis) is False  # idle → no-op
    _start(redis)
    assert engine.request_stop(redis) is True
    assert engine.get_state(redis) == engine.STOPPING


def test_abort_reverts_the_start_and_marks_falha(redis):
    session = FakeSession()
    run_id = _start(redis, session)
    engine.abort(redis, session, run_id)
    assert engine.get_state(redis) == engine.IDLE
    assert engine.is_enabled(redis) is False
    assert session.run(run_id).status == "falha"
    # run_id cleared: nothing can be counted against the aborted run any more.
    assert engine.claim_producer(redis, run_id) is None
    assert engine.claim_producer(redis) is None
    # And the next start is not blocked.
    assert _start(redis, session) is not None


# --- Depth (pipeline reach / cost checkpoint) -------------------------------


def test_get_depth_is_none_on_fresh_redis(redis):
    assert engine.get_depth(redis) is None


@pytest.mark.parametrize(
    "depth",
    [engine.NASCENTE, engine.NASCENTE_RIO, engine.NASCENTE_RIO_MAR],
)
def test_set_depth_then_get_depth_round_trips(redis, depth):
    engine.set_depth(redis, depth)
    assert engine.get_depth(redis) == depth


def test_depth_constant_values_are_the_fixed_contract():
    assert engine.NASCENTE == "nascente"
    assert engine.NASCENTE_RIO == "nascente_rio"
    assert engine.NASCENTE_RIO_MAR == "nascente_rio_mar"
    assert engine.VALID_DEPTHS == frozenset(
        {"nascente", "nascente_rio", "nascente_rio_mar"}
    )


def test_set_depth_rejects_invalid_value(redis):
    with pytest.raises(ValueError):
        engine.set_depth(redis, "bogus")
    # Nothing was persisted on the invalid write.
    assert engine.get_depth(redis) is None


def test_get_status_carries_depth(redis):
    status = engine.get_status(redis)
    assert status["depth"] is None
    engine.set_depth(redis, engine.NASCENTE_RIO)
    assert engine.get_status(redis)["depth"] == engine.NASCENTE_RIO


# --- Enabled latch (operator intent) ---


def test_is_enabled_returns_false_on_fresh_redis(redis):
    assert engine.is_enabled(redis) is False


def test_set_enabled_true_then_is_enabled_returns_true(redis):
    engine.set_enabled(redis, True)
    assert engine.is_enabled(redis) is True


def test_set_enabled_false_then_is_enabled_returns_false(redis):
    engine.set_enabled(redis, True)
    engine.set_enabled(redis, False)
    assert engine.is_enabled(redis) is False


def test_start_sets_enabled(redis):
    _start(redis)
    assert engine.is_enabled(redis) is True


def test_get_status_includes_enabled_field(redis):
    status = engine.get_status(redis)
    assert "enabled" in status
    assert status["enabled"] is False


def test_get_status_enabled_true_after_start(redis):
    _start(redis)
    status = engine.get_status(redis)
    assert status["enabled"] is True


# --- Operator mode (Motor Pausado, phase C) ---------------------------------


def test_default_mode_is_ligado(redis):
    """Absent key → LIGADO (opposite convention from depth/source which default None)."""
    assert engine.get_mode(redis) == engine.LIGADO
    assert engine.is_editing_unlocked(redis) is False


def test_mode_constant_values_are_the_fixed_contract():
    assert engine.LIGADO == "LIGADO"
    assert engine.PAUSADO == "PAUSADO"
    assert engine.DESLIGADO == "DESLIGADO"
    assert engine.VALID_MODES == frozenset({"LIGADO", "PAUSADO", "DESLIGADO"})


@pytest.mark.parametrize("mode", [engine.LIGADO, engine.PAUSADO, engine.DESLIGADO])
def test_set_mode_then_get_mode_round_trips(redis, mode):
    engine.set_mode(redis, mode)
    assert engine.get_mode(redis) == mode


def test_set_mode_rejects_invalid_value(redis):
    with pytest.raises(ValueError):
        engine.set_mode(redis, "bogus")
    # Nothing persisted on the invalid write → still the LIGADO default.
    assert engine.get_mode(redis) == engine.LIGADO


def test_is_editing_unlocked_only_when_paused_or_off(redis):
    engine.set_mode(redis, engine.LIGADO)
    assert engine.is_editing_unlocked(redis) is False
    engine.set_mode(redis, engine.PAUSADO)
    assert engine.is_editing_unlocked(redis) is True
    engine.set_mode(redis, engine.DESLIGADO)
    assert engine.is_editing_unlocked(redis) is True


def test_set_mode_pausado_leaves_runtime_and_enabled(redis):
    """PAUSADO drains via the orchestrator but must NOT flip state nor clear enabled."""
    _start(redis)
    assert engine.get_state(redis) == engine.RUNNING
    assert engine.is_enabled(redis) is True

    engine.set_mode(redis, engine.PAUSADO)
    assert engine.get_state(redis) == engine.RUNNING  # runtime left as-is (drain)
    assert engine.is_enabled(redis) is True  # latch untouched


def test_set_mode_desligado_marks_idle_and_clears_enabled(redis):
    """DESLIGADO is a hard off: idle + set_enabled(False)."""
    run_id = _start(redis)
    engine.progress(redis, run_id, uf="BA")
    assert engine.get_state(redis) == engine.RUNNING
    assert engine.is_enabled(redis) is True

    engine.set_mode(redis, engine.DESLIGADO)
    assert engine.get_state(redis) == engine.IDLE
    assert engine.is_enabled(redis) is False
    assert engine.get_status(redis)["current_uf"] is None  # idle cleared it


def test_set_mode_desligado_zeroes_inflight_and_clears_syncing(redis):
    """Hard off must reset the producer inflight counter so the badge leaves 'syncing'.

    Regression (motor-off stuck on Sincronizando): a leaked/draining inflight count kept
    get_status().sync_phase pinned to 'syncing' after DESLIGADO because OFF never reset it.
    """
    _start(redis)
    engine.claim_producer(redis)
    engine.claim_producer(redis)
    assert engine.get_status(redis)["sync_phase"] == "syncing"

    engine.set_mode(redis, engine.DESLIGADO)
    assert engine.get_status(redis)["sync_phase"] != "syncing"


def test_get_status_includes_mode_and_editing_unlocked(redis):
    status = engine.get_status(redis)
    assert status["mode"] == engine.LIGADO  # default
    assert status["editing_unlocked"] is False

    engine.set_mode(redis, engine.PAUSADO)
    status = engine.get_status(redis)
    assert status["mode"] == engine.PAUSADO
    assert status["editing_unlocked"] is True


# --- sync_phase (BUG 6/7) ---------------------------------------------------


def test_sync_phase_idle_on_fresh_redis(redis):
    """Fresh fakeredis: never run, latch off, no marker → idle."""
    assert engine.get_status(redis)["sync_phase"] == "idle"


def test_sync_phase_syncing_during_run(redis):
    """start sets state RUNNING + enabled latch → syncing (and clears the marker)."""
    _start(redis)
    assert engine.get_status(redis)["sync_phase"] == "syncing"


def test_sync_phase_synced_after_run_end(redis):
    """A completed run turns the motor OFF → synced."""
    _run_to_completion(redis)
    status = engine.get_status(redis)
    assert status["sync_phase"] == "synced"
    assert status["state"] == engine.IDLE
    assert status["enabled"] is False
    assert status["mode"] == engine.DESLIGADO


def test_start_clears_synced_marker(redis):
    """A synced base that starts a fresh run flips back to syncing (marker cleared)."""
    _run_to_completion(redis)
    assert engine.get_status(redis)["sync_phase"] == "synced"
    _start(redis)
    assert engine.get_status(redis)["sync_phase"] == "syncing"


# --- Producer-completes lifecycle (live-kanban fix) -------------------------


def test_no_completion_while_producers_inflight(redis):
    run_id = _start(redis)
    engine.claim_producer(redis, run_id)
    assert engine.dispatch_finished(redis, None, run_id) is False
    assert engine.get_state(redis) == engine.RUNNING
    assert engine.get_status(redis)["sync_phase"] == "syncing"


def test_no_completion_until_dispatch_finished(redis):
    run_id = _start(redis)
    engine.claim_producer(redis, run_id)
    # The only producer finished, but the orchestrator is still dispatching.
    assert engine.producer_done(redis, None, run_id) is False
    assert engine.get_state(redis) == engine.RUNNING


def test_last_producer_completes_exactly_once(redis):
    """RACE: two producers in flight; only the one that drains the counter completes.

    The winning completion turns the motor OFF: mode DESLIGADO, enabled False, state
    IDLE, sync_phase 'synced'. A late extra call never re-completes.
    """
    run_id = _start(redis)
    engine.claim_producer(redis, run_id)
    engine.claim_producer(redis, run_id)
    assert engine.dispatch_finished(redis, None, run_id) is False
    a_won = engine.producer_done(redis, None, run_id)
    b_won = engine.producer_done(redis, None, run_id)
    c_won = engine.producer_done(redis, None, run_id)

    assert [a_won, b_won, c_won] == [False, True, False]
    status = engine.get_status(redis)
    assert status["mode"] == engine.DESLIGADO
    assert status["enabled"] is False
    assert status["state"] == engine.IDLE
    assert status["sync_phase"] == "synced"


def test_simultaneous_zero_readers_still_single_winner(redis):
    """Both callers eligible (inflight 0, dispatch done): the GETSET claim admits ONE."""
    run_id = _start(redis)
    assert [
        engine.dispatch_finished(redis, None, run_id),
        engine.dispatch_finished(redis, None, run_id),
    ] == [True, False]


def test_producer_done_clamps_at_zero(redis):
    """DESLIGADO zeroes inflight with a producer still running; its late producer_done
    must not underflow (the badge leaves 'syncing')."""
    run_id = _start(redis)
    engine.claim_producer(redis, run_id)
    engine.set_mode(redis, engine.DESLIGADO)
    assert engine.producer_done(redis, None, run_id) is False  # dispatch not finished
    assert engine.get_status(redis)["sync_phase"] != "syncing"


def test_sync_phase_syncing_while_inflight_even_at_state_idle(redis):
    """inflight > 0 keeps the badge 'syncing' even when state is idle and latch off.

    This is the live-kanban guarantee: after engine_sweep_run's dispatch loop returns,
    producers are still landing rows; the badge must not read 'idle'/'synced' yet.
    """
    run_id = _start(redis)
    engine.claim_producer(redis, run_id)
    engine.request_stop(redis)
    engine.set_enabled(redis, False)
    status = engine.get_status(redis)
    assert status["enabled"] is False
    assert status["sync_phase"] == "syncing"


def test_start_resets_inflight_and_dispatch_done(redis):
    """A fresh run is not completed by a previous run's leftover counters."""
    old = _run_to_completion(redis)
    run_id = _start(redis)
    assert run_id != old
    engine.claim_producer(redis, run_id)
    # The previous run's dispatch_done does not leak: only this run's own
    # dispatch_finished (plus the producer) can complete it.
    assert engine.producer_done(redis, None, run_id) is False
    assert engine.dispatch_finished(redis, None, run_id) is True


def test_stale_generation_producer_done_does_not_drain_new_run(redis):
    """DESLIGADO with producers in flight, then a new start: the old run's stragglers
    must not decrement the new run's counter nor complete it."""
    old = _start(redis)
    engine.claim_producer(redis, old)
    engine.set_mode(redis, engine.DESLIGADO)  # forced idle, old producer still running

    new = _start(redis)
    engine.claim_producer(redis, new)
    engine.dispatch_finished(redis, None, new)

    assert engine.producer_done(redis, None, old) is False
    engine.progress(redis, old, uf="XX")
    status = engine.get_status(redis)
    assert status["state"] == engine.RUNNING
    assert status["ufs_done"] == 0
    assert status["sync_phase"] == "syncing"
    # The new run's own producer completes it.
    assert engine.producer_done(redis, None, new) is True


def test_producer_done_without_run_id_acts_on_current_run(redis):
    """In-flight messages from before the deploy carry no run_id → the current run."""
    run_id = _start(redis)
    assert engine.claim_producer(redis) == run_id
    engine.dispatch_finished(redis, None)
    assert engine.producer_done(redis, None) is True


def test_full_run_is_concluido(redis):
    session = FakeSession()
    run_id = _start(redis, session, ufs=["BA", "RJ"])
    for uf in ("BA", "RJ"):
        engine.claim_producer(redis, run_id)
        engine.progress(redis, run_id, uf=uf)
    engine.dispatch_finished(redis, session, run_id)
    engine.producer_done(redis, session, run_id)
    assert engine.producer_done(redis, session, run_id) is True
    row = session.run(run_id)
    assert (row.status, row.ufs_dispatched) == ("concluido", 2)
    assert row.ended_at is not None


@pytest.mark.parametrize(
    "interrupt",
    [
        lambda r: engine.request_stop(r),
        lambda r: engine.set_mode(r, engine.PAUSADO),
        lambda r: engine.pause_with_reason(r, "provider_balance", "parallel", action="sweep"),
        lambda r: engine.set_mode(r, engine.DESLIGADO),
    ],
    ids=["stop", "pause", "reasoned_pause", "desligado"],
)
def test_interrupted_run_is_parcial(redis, interrupt):
    session = FakeSession()
    run_id = _start(redis, session, ufs=["BA", "RJ"])
    engine.claim_producer(redis, run_id)
    engine.progress(redis, run_id, uf="BA")
    interrupt(redis)
    # DESLIGADO zeroes inflight, so the run may already complete at dispatch_finished.
    completed = [
        engine.dispatch_finished(redis, session, run_id),
        engine.producer_done(redis, session, run_id),
    ]
    assert completed.count(True) == 1
    assert session.run(run_id).status == "parcial"


def test_all_dispatched_but_stopped_while_draining_is_parcial(redis):
    session = FakeSession()
    run_id = _start(redis, session, ufs=["BA"])
    engine.claim_producer(redis, run_id)
    engine.progress(redis, run_id, uf="BA")
    engine.dispatch_finished(redis, session, run_id)
    engine.request_stop(redis)  # producers halt mid-sweep
    assert engine.producer_done(redis, session, run_id) is True
    assert session.run(run_id).status == "parcial"


def test_start_closes_a_forced_off_run_left_running(redis):
    """DESLIGADO with producers in flight leaves the old row 'running'; the next start
    closes it as parcial (its stragglers are a stale generation now)."""
    session = FakeSession()
    old = _start(redis, session)
    engine.claim_producer(redis, old)
    engine.set_mode(redis, engine.DESLIGADO)
    _start(redis, session)
    assert session.run(old).status == "parcial"


def test_claim_after_run_ended_is_not_counted(redis):
    """A standalone bulk sweep started after the run ended is counted against nothing."""
    _run_to_completion(redis)
    assert engine.claim_producer(redis) is None
    assert engine.get_status(redis)["sync_phase"] == "synced"


def test_should_halt_producer_truth_table(redis):
    """Producer halt gate: mode != LIGADO OR state == STOPPING. IDLE state alone
    (standalone bulk, never started) must NOT halt."""
    # Fresh redis: mode defaults LIGADO, state IDLE → standalone bulk keeps running.
    assert engine.should_halt_producer(redis) is False

    # Engine running normally → keep running.
    _start(redis)
    assert engine.should_halt_producer(redis) is False

    # Pause → halt.
    engine.set_mode(redis, engine.PAUSADO)
    assert engine.should_halt_producer(redis) is True

    # Back to LIGADO then Stop → halt via STOPPING state.
    engine.set_mode(redis, engine.LIGADO)
    engine.request_stop(redis)
    assert engine.should_halt_producer(redis) is True

    # Off (DESLIGADO) → halt.
    fresh = fakeredis.FakeRedis()
    _start(fresh)
    engine.set_mode(fresh, engine.DESLIGADO)
    assert engine.should_halt_producer(fresh) is True
