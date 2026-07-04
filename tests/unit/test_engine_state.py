"""Unit tests for the Redis-backed collection-engine state machine."""

import fakeredis
import pytest

from brave.core import engine


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


def test_default_state_is_idle(redis):
    assert engine.get_state(redis) == engine.IDLE
    assert engine.is_running(redis) is False


def test_start_run_transitions_to_running_and_sets_totals(redis):
    assert engine.start_run(redis, ufs_total=27) is True
    assert engine.get_state(redis) == engine.RUNNING
    assert engine.is_running(redis) is True
    status = engine.get_status(redis)
    assert status["ufs_total"] == 27
    assert status["ufs_done"] == 0
    assert status["current_uf"] is None


def test_start_run_is_idempotent_while_active(redis):
    assert engine.start_run(redis, 27) is True
    # A second start while running is a no-op (never stacks orchestrators).
    assert engine.start_run(redis, 27) is False
    engine.request_stop(redis)
    assert engine.start_run(redis, 27) is False  # stopping is also active


def test_progress_tracking(redis):
    engine.start_run(redis, 3)
    engine.mark_uf_dispatched(redis, "BA")
    engine.mark_uf_dispatched(redis, "RJ")
    status = engine.get_status(redis)
    assert status["current_uf"] == "RJ"
    assert status["ufs_done"] == 2
    assert status["ufs_total"] == 3


def test_request_stop_only_from_running(redis):
    assert engine.request_stop(redis) is False  # idle → no-op
    engine.start_run(redis, 27)
    assert engine.request_stop(redis) is True
    assert engine.get_state(redis) == engine.STOPPING


def test_mark_idle_resets(redis):
    engine.start_run(redis, 27)
    engine.mark_uf_dispatched(redis, "BA")
    engine.mark_idle(redis)
    status = engine.get_status(redis)
    assert status["state"] == engine.IDLE
    assert status["current_uf"] is None


def test_graceful_stop_lifecycle(redis):
    """running → request_stop → stopping → orchestrator drains → mark_idle."""
    engine.start_run(redis, 2)
    engine.mark_uf_dispatched(redis, "BA")
    assert engine.request_stop(redis) is True
    # Orchestrator loop sees state != RUNNING and stops fanning out new UFs.
    assert engine.is_running(redis) is False
    engine.mark_idle(redis)
    assert engine.get_state(redis) == engine.IDLE


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
    assert engine._VALID_DEPTHS == frozenset(
        {"nascente", "nascente_rio", "nascente_rio_mar"}
    )


def test_set_depth_rejects_invalid_value(redis):
    with pytest.raises(ValueError):
        engine.set_depth(redis, "bogus")
    # Nothing was persisted on the invalid write.
    assert engine.get_depth(redis) is None


def test_get_depth_ignores_a_corrupt_persisted_value(redis):
    redis.set(engine._DEPTH_KEY, "rio")  # bypass the setter
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


def test_start_run_sets_enabled(redis):
    engine.start_run(redis, ufs_total=1)
    assert engine.is_enabled(redis) is True


def test_mark_idle_does_not_clear_enabled(redis):
    engine.start_run(redis, ufs_total=1)
    assert engine.is_enabled(redis) is True
    engine.mark_idle(redis)
    assert engine.is_enabled(redis) is True  # latch survives idle transition


def test_get_status_includes_enabled_field(redis):
    status = engine.get_status(redis)
    assert "enabled" in status
    assert status["enabled"] is False


def test_get_status_enabled_true_after_start_run(redis):
    engine.start_run(redis, ufs_total=1)
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
    assert engine._VALID_MODES == frozenset({"LIGADO", "PAUSADO", "DESLIGADO"})


@pytest.mark.parametrize("mode", [engine.LIGADO, engine.PAUSADO, engine.DESLIGADO])
def test_set_mode_then_get_mode_round_trips(redis, mode):
    engine.set_mode(redis, mode)
    assert engine.get_mode(redis) == mode


def test_set_mode_rejects_invalid_value(redis):
    with pytest.raises(ValueError):
        engine.set_mode(redis, "bogus")
    # Nothing persisted on the invalid write → still the LIGADO default.
    assert engine.get_mode(redis) == engine.LIGADO


def test_get_mode_defaults_ligado_on_corrupt_value(redis):
    redis.set(engine._MODE_KEY, "on")  # bypass the setter
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
    engine.start_run(redis, ufs_total=3)
    assert engine.get_state(redis) == engine.RUNNING
    assert engine.is_enabled(redis) is True

    engine.set_mode(redis, engine.PAUSADO)
    assert engine.get_state(redis) == engine.RUNNING  # runtime left as-is (drain)
    assert engine.is_enabled(redis) is True  # latch untouched


def test_set_mode_desligado_marks_idle_and_clears_enabled(redis):
    """DESLIGADO is a hard off: mark_idle + set_enabled(False)."""
    engine.start_run(redis, ufs_total=3)
    engine.mark_uf_dispatched(redis, "BA")
    assert engine.get_state(redis) == engine.RUNNING
    assert engine.is_enabled(redis) is True

    engine.set_mode(redis, engine.DESLIGADO)
    assert engine.get_state(redis) == engine.IDLE
    assert engine.is_enabled(redis) is False
    assert engine.get_status(redis)["current_uf"] is None  # mark_idle cleared it


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
    """start_run sets state RUNNING + enabled latch → syncing (and clears the marker)."""
    engine.start_run(redis, ufs_total=3)
    assert engine.get_status(redis)["sync_phase"] == "syncing"


def test_sync_phase_synced_after_run_end(redis):
    """Simulated run END (motor OFF via DESLIGADO + mark_run_ended) → synced."""
    engine.start_run(redis, ufs_total=3)
    # Run end, exactly as engine_sweep_run's finally does it:
    engine.set_mode(redis, engine.DESLIGADO)  # mark_idle + enabled False + mode off
    engine.mark_run_ended(redis)
    status = engine.get_status(redis)
    assert status["sync_phase"] == "synced"
    assert status["state"] == engine.IDLE
    assert status["enabled"] is False


def test_start_run_clears_synced_marker(redis):
    """A synced base that starts a fresh run flips back to syncing (marker cleared)."""
    engine.mark_run_ended(redis)
    assert engine.get_status(redis)["sync_phase"] == "synced"
    engine.start_run(redis, ufs_total=2)
    assert engine.get_status(redis)["sync_phase"] == "syncing"


# --- Producer-completes lifecycle (live-kanban fix) -------------------------


def test_inflight_counter_incr_decr_and_get(redis):
    """incr/decr round-trip; get_inflight reads the current value; fresh → 0."""
    assert engine.get_inflight(redis) == 0
    assert engine.incr_inflight(redis) == 1
    assert engine.incr_inflight(redis) == 2
    assert engine.get_inflight(redis) == 2
    assert engine.decr_inflight(redis) == 1
    assert engine.get_inflight(redis) == 1


def test_decr_inflight_clamps_at_zero(redis):
    """A decrement past zero is normalized to 0 (never underflows below 0)."""
    assert engine.decr_inflight(redis) == 0  # from absent → -1 → clamped 0
    assert engine.get_inflight(redis) == 0
    engine.incr_inflight(redis)
    engine.decr_inflight(redis)
    assert engine.decr_inflight(redis) == 0  # already 0 → stays 0
    assert engine.get_inflight(redis) == 0


def test_dispatch_done_flag_round_trips(redis):
    """set_dispatch_done True latches '1'; False clears it; default is False."""
    assert engine.is_dispatch_done(redis) is False
    engine.set_dispatch_done(redis, True)
    assert engine.is_dispatch_done(redis) is True
    engine.set_dispatch_done(redis, False)
    assert engine.is_dispatch_done(redis) is False


def test_maybe_complete_false_when_producers_inflight(redis):
    """No completion while any producer is still in flight, even with dispatch done."""
    engine.set_dispatch_done(redis, True)
    engine.incr_inflight(redis)
    assert engine.maybe_complete(redis) is False
    # Nothing was flipped off.
    assert engine.get_state(redis) == engine.IDLE  # unchanged (was never running here)
    assert engine.get_status(redis)["sync_phase"] != "synced"


def test_maybe_complete_false_when_dispatch_not_done(redis):
    """No completion until the orchestrator has latched dispatch_done."""
    engine.set_dispatch_done(redis, False)
    # inflight already drained to 0, but dispatch is not done → cannot complete.
    assert engine.maybe_complete(redis) is False


def test_maybe_complete_single_winner_under_two_producer_race(redis):
    """RACE: inflight 2→1→0 with dispatch_done; maybe_complete returns True EXACTLY once.

    Models two producers finishing: each decrements then calls maybe_complete. Only the
    caller that drains the counter to 0 AND wins the atomic GETSET claim completes the
    run; the other returns False (single-winner). The winning completion turns the motor
    OFF: mode DESLIGADO, enabled False, state IDLE, sync_phase 'synced'.
    """
    engine.start_run(redis, ufs_total=1)  # state RUNNING, enabled True, marker cleared
    engine.set_dispatch_done(redis, True)
    engine.incr_inflight(redis)
    engine.incr_inflight(redis)  # two producers in flight

    # Producer A finishes first: decr 2→1, still in flight → no completion.
    engine.decr_inflight(redis)
    a_won = engine.maybe_complete(redis)
    # Producer B finishes: decr 1→0 → eligible.
    engine.decr_inflight(redis)
    b_won = engine.maybe_complete(redis)
    # A late extra call must never re-complete.
    c_won = engine.maybe_complete(redis)

    assert [a_won, b_won, c_won] == [False, True, False]
    status = engine.get_status(redis)
    assert status["mode"] == engine.DESLIGADO
    assert status["enabled"] is False
    assert status["state"] == engine.IDLE
    assert status["sync_phase"] == "synced"


def test_maybe_complete_simultaneous_zero_readers_still_single_winner(redis):
    """Two callers both observing inflight==0 concurrently: the GETSET claim admits ONE.

    Simulates the interleave where both producers already decremented to 0 and BOTH read
    get_inflight()==0 before either claims — the atomic last_run_ended GETSET is what
    guarantees exactly one winner.
    """
    engine.set_dispatch_done(redis, True)
    # inflight is 0 (absent) and dispatch_done True → both callers are eligible.
    first = engine.maybe_complete(redis)
    second = engine.maybe_complete(redis)
    assert [first, second] == [True, False]


def test_sync_phase_syncing_while_inflight_even_at_state_idle(redis):
    """inflight > 0 keeps the badge 'syncing' even when state is idle and latch off.

    This is the live-kanban guarantee: after engine_sweep_run's dispatch loop returns,
    producers are still landing rows; the badge must not read 'idle'/'synced' yet.
    """
    engine.incr_inflight(redis)
    assert engine.get_state(redis) == engine.IDLE
    assert engine.is_enabled(redis) is False
    assert engine.get_status(redis)["sync_phase"] == "syncing"


def test_start_run_resets_inflight_and_dispatch_done(redis):
    """A fresh run zeroes the in-flight counter and clears dispatch_done (no stale keys)."""
    # Dirty the keys as if a prior run left residue.
    engine.incr_inflight(redis)
    engine.incr_inflight(redis)
    engine.set_dispatch_done(redis, True)

    assert engine.start_run(redis, ufs_total=3) is True
    assert engine.get_inflight(redis) == 0
    assert engine.is_dispatch_done(redis) is False
    # And a fresh run cannot be spuriously completed by a leftover marker.
    assert engine.maybe_complete(redis) is False
