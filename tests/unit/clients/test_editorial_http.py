"""RealEditorialFetcher over respx (no network) + the factory's real/null choice."""

from __future__ import annotations

import httpx
import pytest
import respx

from brave.clients.editorial_http import TIMEOUT_S, RealEditorialFetcher
from brave.clients.factory import Clients
from brave.clients.null_editorial_http import NullEditorialFetcher
from brave.config.settings import AppConfig

URL = "https://www.viagensecaminhos.com/anchieta-es/"


async def test_returns_the_text_and_sends_a_user_agent() -> None:
    f = RealEditorialFetcher(backoff_s=0)
    with respx.mock:
        route = respx.get(URL).mock(return_value=httpx.Response(200, text="<p>oi</p>"))
        assert await f.get_text(URL) == "<p>oi</p>"
    ua = route.calls.last.request.headers["User-Agent"]
    assert ua and "norteia" not in ua.lower()  # no company name in crawl calls
    await f.aclose()


async def test_404_and_410_mean_the_page_does_not_exist() -> None:
    f = RealEditorialFetcher(backoff_s=0)
    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(404))
        assert await f.get_text(URL) is None
        respx.get(URL).mock(return_value=httpx.Response(410))
        assert await f.get_text(URL) is None
    await f.aclose()


async def test_5xx_is_retried_then_succeeds() -> None:
    f = RealEditorialFetcher(backoff_s=0)
    with respx.mock:
        route = respx.get(URL).mock(
            side_effect=[
                httpx.Response(503),
                httpx.ConnectError("x"),
                httpx.Response(200, text="ok"),
            ]
        )
        assert await f.get_text(URL) == "ok"
        assert route.call_count == 3
    await f.aclose()


async def test_gives_up_after_the_attempts_and_raises() -> None:
    f = RealEditorialFetcher(attempts=2, backoff_s=0)
    with respx.mock:
        route = respx.get(URL).mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            await f.get_text(URL)
        assert route.call_count == 2
    await f.aclose()


async def test_other_4xx_is_not_retried() -> None:
    f = RealEditorialFetcher(backoff_s=0)
    with respx.mock:
        route = respx.get(URL).mock(return_value=httpx.Response(403))
        with pytest.raises(httpx.HTTPStatusError):
            await f.get_text(URL)
        assert route.call_count == 1
    await f.aclose()


async def test_timeout_is_180_seconds() -> None:
    f = RealEditorialFetcher(backoff_s=0)
    assert TIMEOUT_S == 180.0
    assert f._client.timeout == httpx.Timeout(180.0)
    await f.aclose()


async def test_null_fetcher_finds_nothing_and_factory_picks_by_externals_flag() -> None:
    assert await NullEditorialFetcher().get_text(URL) is None
    offline = Clients(AppConfig().model_copy(update={"run_real_externals": False}))
    assert isinstance(offline.editorial, NullEditorialFetcher)
    real = Clients(AppConfig().model_copy(update={"run_real_externals": True}))
    assert isinstance(real.editorial, RealEditorialFetcher)
    fake = object()
    assert Clients(editorial=fake).editorial is fake
