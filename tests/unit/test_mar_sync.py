"""Mar → norteia-api sync health (brave/core/mar/sync.py + the push-task gate).

Offline: the health ping is mocked with respx, Redis is fakeredis, the DB a MagicMock.
"""

from __future__ import annotations

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


INGEST = f"{API}/api/internal/territorial/attractions"


@respx.mock
def test_up_needs_health_200_and_ingest_422_and_is_cached(real, redis, monkeypatch):
    monkeypatch.setenv("BRAVE_NORTEIA_API_SERVICE_TOKEN", "9|secret")
    health = respx.get(HEALTH).mock(return_value=httpx.Response(200))
    ingest = respx.post(INGEST).mock(return_value=httpx.Response(422))
    assert sync.norteia_api_up(redis) is True
    assert sync.norteia_api_up(redis) is True
    assert health.call_count == 1 and ingest.call_count == 1
    sent = ingest.calls[0].request
    assert sent.headers["authorization"] == "Bearer 9|secret"
    assert sent.content == b"{}"


@respx.mock
@pytest.mark.parametrize(
    ("ingest_status", "reason"),
    [(401, "ingest:401"), (403, "ingest:403"), (404, "ingest:404"), (500, "ingest:500")],
)
def test_expired_token_or_missing_route_counts_as_down(real, redis, ingest_status, reason):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(INGEST).mock(return_value=httpx.Response(ingest_status))
    assert sync.norteia_api_health(redis) == reason
    assert sync.norteia_api_up(redis) is False


@respx.mock
def test_unhealthy_api_skips_the_ingest_probe(real, redis):
    respx.get(HEALTH).mock(return_value=httpx.Response(503))
    ingest = respx.post(INGEST).mock(return_value=httpx.Response(422))
    assert sync.norteia_api_health(redis) == "unhealthy:503"
    assert not ingest.called


@respx.mock
@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_transport_error_is_unreachable(real, redis, exc):
    respx.get(HEALTH).mock(side_effect=exc)
    assert sync.norteia_api_health(redis) == "unreachable"
    assert redis.get("brave:norteia_api:health") == b"unreachable"


@respx.mock
def test_redis_outage_degrades_to_uncached_probe(real):
    respx.get(HEALTH).mock(return_value=httpx.Response(200))
    respx.post(INGEST).mock(return_value=httpx.Response(422))
    broken = MagicMock()
    broken.get.side_effect = ConnectionError("redis down")
    assert sync.norteia_api_up(broken) is True


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
