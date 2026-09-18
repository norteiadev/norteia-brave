"""ProviderBalanceError + raise_if_balance_wall — the shared billing-wall classifier.

100% offline: respx mocks Tavily/Parallel HTTP, FakeLLMClient/FakePlacesClient stand in
for the copywriter/places_enrichment lane tests. No real network, no real provider keys.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
import respx

from brave.shared.exceptions import ProviderBalanceError, raise_if_balance_wall

# ---------------------------------------------------------------------------
# raise_if_balance_wall — pure classifier
# ---------------------------------------------------------------------------


def test_status_code_432_raises_provider_balance_error() -> None:
    with pytest.raises(ProviderBalanceError) as exc_info:
        raise_if_balance_wall("tavily", status_code=432)
    assert exc_info.value.provider == "tavily"


def test_credit_balance_message_raises_provider_balance_error() -> None:
    with pytest.raises(ProviderBalanceError) as exc_info:
        raise_if_balance_wall("anthropic", message="Your credit balance is too low")
    assert exc_info.value.provider == "anthropic"


def test_rate_limit_status_code_does_not_raise() -> None:
    raise_if_balance_wall("parallel", status_code=429)  # no exception — just a rate limit


# ---------------------------------------------------------------------------
# RealTavilyClient / RealParallelClient — respx-mocked 432/402 responses
# ---------------------------------------------------------------------------


@respx.mock
async def test_tavily_post_raises_provider_balance_error_on_432(monkeypatch: pytest.MonkeyPatch) -> None:
    from brave.clients.tavily import TAVILY_SEARCH_URL, RealTavilyClient

    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    respx.post(TAVILY_SEARCH_URL).mock(return_value=httpx.Response(432))
    client = RealTavilyClient("k-test")

    with pytest.raises(ProviderBalanceError) as exc_info:
        await client.search("query")
    assert exc_info.value.provider == "tavily"


@respx.mock
async def test_parallel_post_raises_provider_balance_error_on_402(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brave.clients.parallel import PARALLEL_SEARCH_URL, RealParallelClient

    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    respx.post(PARALLEL_SEARCH_URL).mock(return_value=httpx.Response(402))
    client = RealParallelClient("k-test")

    with pytest.raises(ProviderBalanceError) as exc_info:
        await client.search(["q"], "objetivo")
    assert exc_info.value.provider == "parallel"


# ---------------------------------------------------------------------------
# Lane propagation — copywriter + places_enrichment must NOT degrade this error
# ---------------------------------------------------------------------------


async def test_copywriter_write_reraises_provider_balance_error() -> None:
    from brave.lanes.atrativos.copywriter import TourismCopywriter
    from tests.fakes.fake_llm import FakeLLMClient

    fake = FakeLLMClient(raise_on_call=ProviderBalanceError("anthropic"))
    cw = TourismCopywriter(fake, model="claude-sonnet-4-5")

    with pytest.raises(ProviderBalanceError):
        await cw.write("Praia de Camburi", "Vitória", "ES", {})


async def test_places_enrichment_write_description_reraises_provider_balance_error() -> None:
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent
    from tests.fakes.fake_llm import FakeLLMClient
    from tests.fakes.fake_places import FakePlacesClient

    fake_llm = FakeLLMClient(raise_on_call=ProviderBalanceError("anthropic"))
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(),
        session=MagicMock(),
        llm_client=fake_llm,
    )

    with pytest.raises(ProviderBalanceError):
        await agent.write_description("Praia de Camburi", "Vitória", "ES", {})


if __name__ == "__main__":  # pragma: no cover — ponytail runnable check
    raise_if_balance_wall("parallel", status_code=429)  # no-op, must not raise
    try:
        raise_if_balance_wall("tavily", status_code=432)
        raise AssertionError("expected ProviderBalanceError")
    except ProviderBalanceError as exc:
        assert exc.provider == "tavily"
    print("ok")
