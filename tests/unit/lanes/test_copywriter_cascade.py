"""Cascade copywriter — Tavily search → mention gate → Haiku (no tool) → groundedness gate.

100% offline: respx mocks BOTH network boundaries (api.tavily.com and api.anthropic.com), so
the real RealTavilyClient and RealLLMClient run end to end without a key. fakeredis carries
the daily cost-guard counter.

The two gate cases the lane exists for (docs/poc/gemini-viability.md §23-§24):
  - search context that only talks about the município → the model is NEVER called;
  - search context that names the atrativo → Haiku writes, with no tool, and the prose lands.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import fakeredis
import httpx
import pytest
import respx

from brave.clients.tavily import TAVILY_SEARCH_URL, USD_PER_SEARCH, format_results
from brave.lanes.atrativos.copywriter import CASCADE_MODEL, TourismCopywriter, cascade_queries
from brave.lanes.atrativos.grounding import (
    MIN_GROUNDEDNESS,
    groundedness_ratio,
    menciona,
    termos_identificadores,
)
from brave.observability.cost_guard import _daily_key

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# What Tavily returns for a município-level query: real coastal text, no word of the atrativo.
_GENERICO = [
    {
        "title": "Vila Velha — turismo",
        "url": "https://turismo.es.gov.br/vila-velha",
        "content": "As praias de Vila Velha atraem visitantes o ano todo, com orla urbanizada.",
    }
]
# What Tavily returns when coverage exists: the atrativo is named, with facts.
_COM_MENCAO = [
    {
        "title": "Praia da Costa",
        "url": "https://pt.wikipedia.org/wiki/Praia_da_Costa",
        "content": "A Praia da Costa, em Vila Velha, tem calçadão de 3 km e fica junto ao "
        "Morro do Moreno.",
    }
]
_PROSA_FUNDAMENTADA = (
    "O calçadão da Praia da Costa acompanha a areia por 3 km em Vila Velha, com o Morro do "
    "Moreno ao fundo. Vá cedo, quando a luz ainda é suave."
)
_PROSA_DE_MEMORIA = (
    "Inaugurado em 1952, o Forte de Copacabana e o Hotel Copacabana Palace ficam a 300 metros "
    "da Praia da Costa."
)


def _anthropic_reply(text: str, *, input_tokens: int = 1000, output_tokens: int = 200) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": CASCADE_MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


@pytest.fixture
def real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real clients refuse to build offline; the network is mocked, not the guard."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_LLM_OPENROUTER_API_KEY", "test-openrouter")
    monkeypatch.setenv("BRAVE_LLM_ANTHROPIC_API_KEY", "test-anthropic")


def _clients(redis: fakeredis.FakeRedis) -> tuple:
    from brave.clients.llm import RealLLMClient
    from brave.clients.tavily import RealTavilyClient
    from brave.config.settings import LLMConfig

    cfg = LLMConfig()
    llm = RealLLMClient(config=cfg, redis_client=redis, session=MagicMock(), lane="t")
    search = RealTavilyClient("test-tavily", redis_client=redis, llm_config=cfg)
    return llm, search


def _spent(redis: fakeredis.FakeRedis) -> float:
    return float(redis.get(_daily_key()) or 0.0)


# ---------------------------------------------------------------------------
# The two gate cases, end to end through both real clients
# ---------------------------------------------------------------------------


@respx.mock
async def test_gate_blocks_generic_municipio_context(real_env: None) -> None:
    """Search returns município noise → no model call, no prose, verdict "sem_mencao"."""
    tavily = respx.post(TAVILY_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": _GENERICO})
    )
    anthropic = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(200, json=_anthropic_reply("nunca deveria ser lido"))
    )
    redis = fakeredis.FakeRedis()
    llm, search = _clients(redis)

    out = await TourismCopywriter(llm, CASCADE_MODEL, search_client=search).write_cascade(
        "Praia Da Costa", "Vila Velha", "ES"
    )

    assert out.prose is None and out.motivo == "sem_mencao"
    assert tavily.call_count == 2, "both cascade queries run before the gate decides"
    assert not anthropic.called, "the gate exists to keep the model away from this context"
    # The searches were paid even though nothing was written — the guard must see them.
    assert _spent(redis) == pytest.approx(2 * USD_PER_SEARCH)


@respx.mock
async def test_gate_passes_context_that_names_the_atrativo(real_env: None) -> None:
    """Search names the atrativo → Haiku writes with NO tool, over the injected sources."""
    tavily = respx.post(TAVILY_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": _COM_MENCAO})
    )
    anthropic = respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(200, json=_anthropic_reply(_PROSA_FUNDAMENTADA))
    )
    redis = fakeredis.FakeRedis()
    llm, search = _clients(redis)

    out = await TourismCopywriter(llm, CASCADE_MODEL, search_client=search).write_cascade(
        "Praia Da Costa", "Vila Velha", "ES"
    )

    assert out.prose == _PROSA_FUNDAMENTADA
    assert out.motivo is None and out.groundedness == 1.0

    body = json.loads(anthropic.calls.last.request.content)
    assert body["model"] == "claude-haiku-4-5"
    assert "tools" not in body, "cascade mode must not offer web_search"
    user = body["messages"][0]["content"]
    assert "FONTES ENCONTRADAS NA WEB" in user and "Morro do Moreno" in user
    assert "busque na web" not in user, "an impossible instruction invites 'conforme pesquisei'"

    sent = [json.loads(c.request.content)["query"] for c in tavily.calls]
    assert sent == cascade_queries("Praia Da Costa", "Vila Velha", "ES")
    assert tavily.calls.last.request.headers["Authorization"] == "Bearer test-tavily"
    # Haiku priced at $1/$5 (not Sonnet's $3/$15) + the two searches.
    assert _spent(redis) == pytest.approx((1000 * 1 + 200 * 5) / 1e6 + 2 * USD_PER_SEARCH)


@respx.mock
async def test_ungrounded_prose_is_a_draft_not_a_description(real_env: None) -> None:
    """Mention passes but the prose is memory, not sources → parked, never the description."""
    respx.post(TAVILY_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"results": _COM_MENCAO})
    )
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(200, json=_anthropic_reply(_PROSA_DE_MEMORIA))
    )
    llm, search = _clients(fakeredis.FakeRedis())

    out = await TourismCopywriter(llm, CASCADE_MODEL, search_client=search).write_cascade(
        "Praia Da Costa", "Vila Velha", "ES"
    )

    assert out.prose is None and out.motivo == "nao_fundamentada"
    assert out.rascunho == _PROSA_DE_MEMORIA
    assert out.groundedness is not None and out.groundedness < MIN_GROUNDEDNESS


@respx.mock
async def test_search_failure_keeps_the_floor(real_env: None) -> None:
    """A Tavily outage degrades to "no prose", like an LLM failure — never raises."""
    respx.post(TAVILY_SEARCH_URL).mock(return_value=httpx.Response(401))
    llm, search = _clients(fakeredis.FakeRedis())

    out = await TourismCopywriter(llm, CASCADE_MODEL, search_client=search).write_cascade(
        "Praia Da Costa", "Vila Velha", "ES"
    )
    assert out.prose is None and out.motivo is None


def test_tavily_client_refuses_offline() -> None:
    from brave.clients.tavily import RealTavilyClient

    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        RealTavilyClient("k")


def test_format_results_keeps_url_as_evidence() -> None:
    ctx = format_results(_COM_MENCAO)
    assert ctx.splitlines()[:2] == ["Praia da Costa", "https://pt.wikipedia.org/wiki/Praia_da_Costa"]


# ---------------------------------------------------------------------------
# The deterministic gates
# ---------------------------------------------------------------------------


def test_mention_gate_ignores_generic_words() -> None:
    assert termos_identificadores("Praia Da Costa") == ["costa"]
    assert not menciona("As praias de Vila Velha, com sua orla e seu parque.", "Praia Da Costa")
    assert menciona("CALÇADÃO DA PRAIA DA COSTA", "Praia Da Costa")  # accent/case-proof
    assert not menciona("a Pedra Azul fica na região", "Pedra do Elefante")  # all terms


def test_groundedness_threshold_and_sensory_prose() -> None:
    """A third of the claims loose is already too many (§25); prose with no claim passes."""
    assert 2 / 3 < MIN_GROUNDEDNESS <= 0.75
    assert groundedness_ratio("um lugar tranquilo para ver o mar", "x") == 1.0


def test_emoji_in_the_name_does_not_block_the_gate() -> None:
    """§25: "Figueira Da Esquina 🌳❤️" was blocked because the emoji became a required term."""
    assert menciona("A Figueira da Esquina, em Vitória", "Figueira Da Esquina 🌳❤️")


# ---------------------------------------------------------------------------
# The agent: where the verdicts land on the record
# ---------------------------------------------------------------------------


class _FakeSearch:
    def __init__(self, contexto: str) -> None:
        self.contexto = contexto
        self.queries: list[str] = []

    async def search(self, query: str) -> str:
        self.queries.append(query)
        return self.contexto


def _rio() -> MagicMock:
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.routing = "mar"
    rio.dlq_reason = None
    rio.descricao_batch_id = None
    rio.entity_type = "attraction"
    rio.uf = "ES"
    rio.canonical_key = "tripadvisor:attraction:1"
    rio.normalized = {"name": "Praia Da Costa", "municipio": "Vila Velha", "google_enriched": True}
    return rio


async def _run_agent(rio: MagicMock, contexto: str, prosa: str) -> tuple:
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent
    from tests.fakes.fake_llm import FakeLLMClient
    from tests.fakes.fake_places import FakePlacesClient

    llm = FakeLLMClient(generate_result=prosa)
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(),
        session=MagicMock(),
        llm_client=llm,
        search_client=_FakeSearch(contexto),
    )
    with patch("brave.lanes.atrativos.places_enrichment.write_audit"), \
         patch("brave.lanes.atrativos.places_enrichment.record_event"), \
         patch("brave.lanes.atrativos.places_enrichment.route_by_score"):
        await agent.run(rio)
    return llm


async def test_agent_ungrounded_goes_to_dlq_without_description() -> None:
    rio = _rio()
    llm = await _run_agent(rio, format_results(_COM_MENCAO), _PROSA_DE_MEMORIA)

    assert llm.generate_calls[-1]["model"] == CASCADE_MODEL
    assert "descricao_editorial" not in rio.normalized
    assert rio.normalized["descricao_rascunho"] == _PROSA_DE_MEMORIA
    assert rio.normalized["descricao_gate"] == "nao_fundamentada"
    assert rio.normalized["descricao_attempts"] == 1
    assert (rio.routing, rio.dlq_reason) == ("dlq", "descricao_nao_fundamentada")


async def test_agent_gate_block_keeps_record_moving_without_description() -> None:
    rio = _rio()
    llm = await _run_agent(rio, format_results(_GENERICO), "nunca")

    assert llm.generate_calls == []
    assert "descricao_editorial" not in rio.normalized
    assert rio.normalized["descricao_gate"] == "sem_mencao"
    assert rio.routing == "mar", "no description is not a defect of the record"


async def test_agent_grounded_prose_is_written() -> None:
    rio = _rio()
    await _run_agent(rio, format_results(_COM_MENCAO), _PROSA_FUNDAMENTADA)

    assert rio.normalized["descricao_editorial"] == _PROSA_FUNDAMENTADA
    assert rio.normalized["descricao_gate"] is None
    assert rio.normalized["descricao_groundedness"] == 1.0


@respx.mock
async def test_tavily_rate_limit_waits_instead_of_failing(real_env: None) -> None:
    """429 + retry-after is backpressure, not failure (§25: 84 of 150 failed without this)."""
    route = respx.post(TAVILY_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(200, json={"results": _COM_MENCAO}),
        ]
    )
    _, search = _clients(fakeredis.FakeRedis())
    assert "Praia da Costa" in await search.search("q")
    assert route.call_count == 3


def test_tavily_wait_honours_retry_after_capped() -> None:
    from types import SimpleNamespace

    from brave.clients.tavily import _MAX_RETRY_AFTER_S, _wait

    def state(headers: dict) -> SimpleNamespace:
        resp = httpx.Response(429, headers=headers, request=httpx.Request("POST", "http://x"))
        exc = httpx.HTTPStatusError("429", request=resp.request, response=resp)
        outcome = SimpleNamespace(exception=lambda: exc)
        return SimpleNamespace(outcome=outcome, attempt_number=1)

    assert _wait(state({"retry-after": "60"})) == 60.0
    assert _wait(state({"retry-after": "900"})) == _MAX_RETRY_AFTER_S
    assert _wait(state({})) == 2  # no header → exponential floor
