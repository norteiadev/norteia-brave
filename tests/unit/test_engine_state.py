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
