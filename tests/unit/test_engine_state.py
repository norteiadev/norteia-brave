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
