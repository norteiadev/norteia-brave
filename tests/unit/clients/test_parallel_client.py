"""RealParallelClient — one request per atrativo, whole result back, cost guard fed.

Offline: respx mocks api.parallel.ai, fakeredis carries the daily cost counter.
"""

from __future__ import annotations

import json

import fakeredis
import httpx
import pytest
import respx

from brave.clients.parallel import PARALLEL_SEARCH_URL, ParallelSearch, search_cost
from brave.observability.cost_guard import _daily_key
from brave.shared.exceptions import CostGuardError

_RESULTS = [
    {"title": "Lago Negro", "url": "https://a/lago", "publish_date": None, "excerpts": ["e1", "e2"]},
    {"title": None, "url": "https://b", "excerpts": []},
]


@pytest.fixture
def real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")


def _client(redis: fakeredis.FakeRedis | None = None, **kw):  # noqa: ANN003, ANN202
    from brave.clients.parallel import RealParallelClient
    from brave.config.settings import LLMConfig

    return RealParallelClient("k-test", redis_client=redis, llm_config=LLMConfig() if redis else None, **kw)


@respx.mock
async def test_one_request_carries_every_query_and_returns_the_whole_result(real_env: None) -> None:
    route = respx.post(PARALLEL_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"search_id": "s1", "results": _RESULTS, "usage": [{"name": "sku_search", "count": 1}]}
        )
    )
    redis = fakeredis.FakeRedis()

    busca = await _client(redis).search(["q1", "q2"], "objetivo")

    assert route.call_count == 1
    req = route.calls.last.request
    assert req.headers["x-api-key"] == "k-test"
    assert json.loads(req.content) == {"objective": "objetivo", "search_queries": ["q1", "q2"], "mode": "turbo"}
    assert busca.results == _RESULTS and busca.search_id == "s1"
    assert busca.queries == ["q1", "q2"] and busca.mode == "turbo"
    assert busca.usd == pytest.approx(0.001)
    assert float(redis.get(_daily_key())) == pytest.approx(0.001)


def test_fontes_renders_title_url_then_excerpts() -> None:
    busca = ParallelSearch("s", "turbo", "o", [], _RESULTS, None, None, 0.001, 1)
    assert busca.fontes() == "[Lago Negro] https://a/lago\ne1\ne2\n\n[] https://b\n"


def test_cost_per_mode_and_extra_results() -> None:
    assert search_cost("turbo", 10) == pytest.approx(0.001)
    assert search_cost("basic", 10) == pytest.approx(0.005)
    assert search_cost("fast", 11) == pytest.approx(0.002)


@respx.mock
async def test_rate_limit_honours_retry_after(real_env: None) -> None:
    route = respx.post(PARALLEL_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(200, json={"results": _RESULTS}),
        ]
    )
    busca = await _client().search(["q"], "o")
    assert route.call_count == 2 and len(busca.results) == 2


@respx.mock
async def test_cost_guard_blocks_before_dispatch(real_env: None) -> None:
    route = respx.post(PARALLEL_SEARCH_URL)
    redis = fakeredis.FakeRedis()
    redis.set(_daily_key(), 1_000_000)

    with pytest.raises(CostGuardError):
        await _client(redis).search(["q"], "o")
    assert not route.called


def test_refuses_offline_empty_key_and_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from brave.clients.parallel import RealParallelClient

    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        RealParallelClient("k")
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    with pytest.raises(RuntimeError, match="PARALLEL_API_KEY"):
        RealParallelClient("")
    with pytest.raises(ValueError, match="mode"):
        RealParallelClient("k", mode="ultra")
