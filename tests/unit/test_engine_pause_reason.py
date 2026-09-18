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


def test_maybe_complete_returns_false_while_pause_reason_set(redis):
    engine.start_run(redis, ufs_total=1)
    engine.set_dispatch_done(redis, True)
    # inflight already 0 (start_run resets it) and dispatch is done — without a
    # pause_reason this would complete the run.
    engine.pause_with_reason(redis, "provider_balance", "openrouter", action="sweep")

    assert engine.maybe_complete(redis) is False
    # No DESLIGADO side effect must have fired — mode stays PAUSADO, not DESLIGADO.
    assert engine.get_mode(redis) == engine.PAUSADO


if __name__ == "__main__":  # pragma: no cover — ponytail runnable check
    _r = fakeredis.FakeRedis()
    engine.pause_with_reason(_r, "provider_balance", "tavily", action="sweep")
    assert engine.get_mode(_r) == engine.PAUSADO
    assert engine.get_status(_r)["pause_reason"]["provider"] == "tavily"
    engine.set_mode(_r, engine.LIGADO)
    assert engine.get_status(_r)["pause_reason"] is None
    print("ok")
