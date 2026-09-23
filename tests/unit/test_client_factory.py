"""brave.clients.factory — the one seam that picks real vs offline adapters.

Offline: nothing here reaches the network. The "externals on" cases only construct real
adapters (no request is made) and patch the env the adapters' own self-guards read.
"""

from __future__ import annotations

import asyncio

import pytest

from brave.clients.factory import Clients, clients_for
from brave.clients.null_llm import NullLLMClient
from brave.clients.null_nominatim import NullGeocoderClient
from brave.clients.null_norteia_api import NullNorteiaApiClient
from brave.clients.null_places import NullPlacesClient
from brave.clients.null_search import NullSearchClient
from brave.clients.null_tripadvisor import NullTripAdvisorClient
from brave.clients.null_whatsapp import NullWhatsAppClient
from brave.config.settings import AppConfig, LLMConfig
from tests.fakes.fake_places import FakePlacesClient


def _cfg(**update) -> AppConfig:
    return AppConfig().model_copy(update=update)


@pytest.fixture
def real_env(monkeypatch):
    """Externals on for the real adapters' constructor self-guards."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost:6379/15")


def test_externals_off_builds_every_offline_adapter():
    c = clients_for(_cfg(run_real_externals=False, atrativo_description_cascade_enabled=True))

    assert isinstance(c.places, NullPlacesClient)
    assert isinstance(c.llm("atrativos"), NullLLMClient)
    assert isinstance(c.search(), NullSearchClient)
    assert isinstance(c.tripadvisor, NullTripAdvisorClient)
    assert isinstance(c.geocoder, NullGeocoderClient)
    assert isinstance(c.whatsapp, NullWhatsAppClient)
    assert isinstance(c.norteia_api, NullNorteiaApiClient)
    assert c.check_search() is None


def test_null_search_returns_no_results():
    found = asyncio.run(NullSearchClient().search(["q"], "obj"))
    assert found.results == [] and found.usd == 0.0 and found.fontes() == ""


def test_search_is_none_when_cascade_off():
    c = clients_for(_cfg(run_real_externals=False, atrativo_description_cascade_enabled=False))
    assert c.search() is None


def test_batch_with_externals_off_raises():
    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        _ = clients_for(_cfg(run_real_externals=False)).batch


def test_explicit_adapter_wins():
    fake = FakePlacesClient()
    c = Clients(_cfg(run_real_externals=True), places=fake)
    assert c.places is fake


def test_externals_on_missing_key_raises(real_env, monkeypatch):
    monkeypatch.delenv("BRAVE_PLACES_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="api_key is empty"):
        _ = clients_for(_cfg(run_real_externals=True)).places

    c = clients_for(
        _cfg(
            run_real_externals=True,
            atrativo_description_cascade_enabled=True,
            atrativo_cascade_model="deepseek/deepseek-chat",
            parallel_api_key="",
        )
    )
    assert c.check_search() == "PARALLEL_API_KEY is empty"
    with pytest.raises(RuntimeError, match="PARALLEL_API_KEY"):
        c.search()


def test_gemini_model_without_price_is_refused(real_env):
    c = clients_for(
        _cfg(
            run_real_externals=True,
            atrativo_description_cascade_enabled=True,
            atrativo_cascade_model="gemini-0-unpriced",
            parallel_api_key="k",
            llm=LLMConfig(openrouter_api_key="or", gemini_api_key="g"),
        )
    )
    assert c.check_search() == "cascade model 'gemini-0-unpriced' has no Gemini price"
    with pytest.raises(RuntimeError, match="no Gemini price"):
        c.search()


def test_real_llm_gets_redis_session_and_lane(real_env, monkeypatch):
    """The cost guard (redis) and the llm_generations rows (session, lane) are wired."""
    built = {}
    monkeypatch.setattr("brave.clients.llm.RealLLMClient", lambda **kw: built.update(kw) or kw)
    session = object()

    clients_for(_cfg(run_real_externals=True)).llm("atrativos", session=session)

    assert built["session"] is session and built["lane"] == "atrativos"
    assert built["redis_client"] is not None


def test_context_exit_closes_only_what_it_built(real_env):
    fake_places = FakePlacesClient()
    c = Clients(
        _cfg(
            run_real_externals=True,
            atrativo_description_cascade_enabled=True,
            atrativo_cascade_model="deepseek/deepseek-chat",
            parallel_api_key="k",
        ),
        places=fake_places,
    )
    ta = c.tripadvisor
    search = c.search()

    async def _run() -> None:
        async with c:
            assert ta._hc is not None  # persistent connection held for the loop
            assert c.places is fake_places

    asyncio.run(_run())

    assert ta._hc is None
    assert search._http.is_closed
