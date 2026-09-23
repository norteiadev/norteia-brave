"""Unit tests for the engine's reasoned-pause API (pause_with_reason / pause_reason).

100% offline: fakeredis, no session (no DB).
"""

import fakeredis
import pytest

from brave.core import engine


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


def test_pause_with_reason_sets_mode_paused_and_reason(redis):
    engine.pause_with_reason(redis, "provider_balance", "tavily", action="describe")

    assert engine.get_mode(redis) == engine.PAUSADO
    reason = engine.get_status(redis)["pause_reason"]
    assert reason["reason"] == "provider_balance"
    assert reason["provider"] == "tavily"
    assert reason["action"] == "describe"
    assert isinstance(reason["at"], str) and reason["at"]  # ISO datetime string


def test_set_mode_ligado_clears_pause_reason(redis):
    engine.pause_with_reason(redis, "daily_budget", action="describe")
    assert engine.get_status(redis)["pause_reason"] is not None

    engine.set_mode(redis, engine.LIGADO)

    assert engine.get_status(redis)["pause_reason"] is None
    assert engine.get_mode(redis) == engine.LIGADO


def _start(redis):
    return engine.start(
        redis, None, action="sweep", depth=engine.NASCENTE_RIO, source="tripadvisor",
        ufs=["SP"], lane="atrativos",
    )


def test_completion_ends_run_but_keeps_reasoned_pause(redis):
    run_id = _start(redis)
    engine.pause_with_reason(redis, "provider_balance", "openrouter", action="sweep")

    # The run ends (idle, so Continuar's /engine/start does not 409) but the motor stays
    # PAUSADO with its reason — never DESLIGADO/"synced".
    assert engine.dispatch_finished(redis, None, run_id) is True
    assert engine.get_state(redis) == engine.IDLE
    assert engine.get_mode(redis) == engine.PAUSADO
    assert engine.get_status(redis)["pause_reason"]["provider"] == "openrouter"
    assert _start(redis) is not None


if __name__ == "__main__":  # pragma: no cover — ponytail runnable check
    _r = fakeredis.FakeRedis()
    engine.pause_with_reason(_r, "provider_balance", "tavily", action="sweep")
    assert engine.get_mode(_r) == engine.PAUSADO
    assert engine.get_status(_r)["pause_reason"]["provider"] == "tavily"
    engine.set_mode(_r, engine.LIGADO)
    assert engine.get_status(_r)["pause_reason"] is None
    print("ok")
