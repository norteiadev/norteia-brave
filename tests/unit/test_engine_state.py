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
