"""Mar → norteia-api sync health (brave/core/mar/sync.py + the push-task gate).

Offline: the health ping is mocked with respx, Redis is fakeredis, the DB a MagicMock.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import fakeredis
import httpx
import pytest
import respx

from brave.core.mar import sync

API = "http://norteia-api.test"
HEALTH = f"{API}/api/v1/health"


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_NORTEIA_API_URL", API)


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


def test_not_applicable_while_externals_off(monkeypatch, redis):
    monkeypatch.delenv("RUN_REAL_EXTERNALS", raising=False)
    monkeypatch.setenv("BRAVE_NORTEIA_API_URL", API)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(HEALTH)
        assert sync.norteia_api_up(redis) is None
        assert not route.called


@respx.mock
def test_up_is_cached_so_a_burst_shares_one_ping(real, redis):
    route = respx.get(HEALTH).mock(return_value=httpx.Response(200))
    assert sync.norteia_api_up(redis) is True
    assert sync.norteia_api_up(redis) is True
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize(
    "side_effect",
    [httpx.Response(503), httpx.ConnectError("refused"), httpx.ReadTimeout("slow")],
)
def test_down_on_503_or_transport_error(real, redis, side_effect):
    if isinstance(side_effect, httpx.Response):
        respx.get(HEALTH).mock(return_value=side_effect)
    else:
        respx.get(HEALTH).mock(side_effect=side_effect)
    assert sync.norteia_api_up(redis) is False
    assert redis.get("brave:norteia_api:up") == b"0"


@respx.mock
def test_redis_outage_degrades_to_uncached_ping(real):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    broken = MagicMock()
    broken.get.side_effect = ConnectionError("redis down")
    assert sync.norteia_api_up(broken) is True


def test_dispatch_routes_each_row_to_its_entity_task(monkeypatch):
    import brave.tasks.pipeline as pipeline

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pipeline.push_attraction_task, "delay", lambda rid: sent.append(("attraction", rid))
    )
    monkeypatch.setattr(
        pipeline.push_destination_task, "delay", lambda rid: sent.append(("destination", rid))
    )
    a, d = uuid.uuid4(), uuid.uuid4()
    session = MagicMock()
    session.execute.return_value.all.return_value = [(a, "attraction"), (d, "destination")]

    assert pipeline.dispatch_pending_pushes(session) == 2
    assert sent == [("attraction", str(a)), ("destination", str(d))]


def test_beat_task_noops_when_api_down(real, monkeypatch):
    import brave.tasks.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_norteia_api_down", lambda: True)
    get_session = MagicMock()
    monkeypatch.setattr(pipeline, "_get_session", get_session)

    assert pipeline.repush_pending_mar() == 0
    get_session.assert_not_called()


def test_beat_task_noops_while_externals_off(monkeypatch):
    import brave.tasks.pipeline as pipeline

    monkeypatch.delenv("RUN_REAL_EXTERNALS", raising=False)
    get_session = MagicMock()
    monkeypatch.setattr(pipeline, "_get_session", get_session)

    assert pipeline.repush_pending_mar() == 0
    get_session.assert_not_called()


def test_beat_schedule_carries_repush_entry_without_queue():
    from brave.tasks.beat_schedule import maintenance_beat_entries

    entry = maintenance_beat_entries()["repush-pending-mar-15min"]
    assert entry["task"] == "brave.repush_pending_mar"
    assert "options" not in entry
