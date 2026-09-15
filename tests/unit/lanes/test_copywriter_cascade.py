"""Cascade copywriter — Parallel search → mention + município gates → Gemini (no tool) → groundedness.

100% offline: respx mocks BOTH network boundaries (api.parallel.ai and openrouter.ai), so the
real RealParallelClient and RealLLMClient run end to end without a key. fakeredis carries the
daily cost-guard counter.

The gate cases the lane exists for (docs/poc/gemini-viability.md §23-§24, §29):
  - search context that only talks about the município → the model is NEVER called;
  - search context that never names the record's município → the model is NEVER called;
  - search context that names the atrativo → Gemini writes, with no tool, and the prose lands.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import httpx
import pytest
import respx

from brave.clients.parallel import PARALLEL_SEARCH_URL, ParallelSearch
from brave.clients.tavily import TAVILY_SEARCH_URL, format_results
from brave.core.models import AtrativoBusca
from brave.lanes.atrativos.copywriter import (
    CASCADE_MODEL,
    TourismCopywriter,
    cascade_objective,
    cascade_queries,
)
from brave.lanes.atrativos.grounding import (
    MIN_GROUNDEDNESS,
    groundedness_ratio,
    menciona,
    menciona_municipio,
    termos_identificadores,
)
from brave.observability.cost_guard import _daily_key

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# What Parallel returns for a município-level query: real coastal text, no word of the atrativo.
_GENERICO = [
    {
        "title": "Vila Velha — turismo",
        "url": "https://turismo.es.gov.br/vila-velha",
        "excerpts": ["As praias de Vila Velha atraem visitantes o ano todo, com orla urbanizada."],
    }
]
# What Parallel returns when coverage exists: the atrativo is named, with facts.
_COM_MENCAO = [
    {
        "title": "Praia da Costa",
        "url": "https://pt.wikipedia.org/wiki/Praia_da_Costa",
        "publish_date": "2024-01-15",
        "excerpts": [
            "A Praia da Costa, em Vila Velha, tem calçadão de 3 km e fica junto ao "
            "Morro do Moreno."
        ],
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


def _parallel_reply(results: list[dict]) -> dict:
    return {
        "search_id": "search_test",
        "results": results,
        "usage": [{"name": "sku_search", "count": 1}],
        "warnings": None,
    }


def _openrouter_reply(text: str, *, finish: str = "stop", cost: float = 0.0021) -> dict:
    return {
        "id": "gen-test",
        "object": "chat.completion",
        "created": 0,
        "model": CASCADE_MODEL,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 4000, "completion_tokens": 400, "total_tokens": 4400, "cost": cost},
    }


@pytest.fixture
def real_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real clients refuse to build offline; the network is mocked, not the guard."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setenv("BRAVE_LLM_OPENROUTER_API_KEY", "test-openrouter")
    monkeypatch.setenv("BRAVE_LLM_ANTHROPIC_API_KEY", "test-anthropic")


def _clients(redis: fakeredis.FakeRedis) -> tuple:
    from brave.clients.llm import RealLLMClient
    from brave.clients.parallel import RealParallelClient
    from brave.config.settings import LLMConfig

    cfg = LLMConfig()
    llm = RealLLMClient(config=cfg, redis_client=redis, session=MagicMock(), lane="t")
    search = RealParallelClient("test-parallel", redis_client=redis, llm_config=cfg)
    return llm, search


def _spent(redis: fakeredis.FakeRedis) -> float:
    return float(redis.get(_daily_key()) or 0.0)


async def _write(search_results: list[dict], text: str = "nunca deveria ser lido", **kw: Any) -> tuple:
    """Run write_cascade through both real clients; returns (out, parallel_route, llm_route, redis)."""
    parallel = respx.post(PARALLEL_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_parallel_reply(search_results))
    )
    llm_route = respx.post(OPENROUTER_URL).mock(
        return_value=httpx.Response(200, json=_openrouter_reply(text, **kw))
    )
    redis = fakeredis.FakeRedis()
    llm, search = _clients(redis)
    out = await TourismCopywriter(llm, CASCADE_MODEL, search_client=search).write_cascade(
        "Praia Da Costa", "Vila Velha", "ES"
    )
    return out, parallel, llm_route, redis


# ---------------------------------------------------------------------------
# The gate cases, end to end through both real clients
# ---------------------------------------------------------------------------


@respx.mock
async def test_gate_blocks_generic_municipio_context(real_env: None) -> None:
    """Search returns município noise → no model call, no prose, verdict "sem_mencao"."""
    out, parallel, llm_route, redis = await _write(_GENERICO)

    assert out.prose is None and out.motivo == "sem_mencao"
    assert parallel.call_count == 1, "both cascade queries go in ONE request"
    assert not llm_route.called, "the gate exists to keep the model away from this context"
    # The search was paid even though nothing was written — the guard sees it, and the
    # result travels back so the caller can persist it.
    assert _spent(redis) == pytest.approx(0.001)
    assert out.busca is not None and out.busca.results == _GENERICO


@respx.mock
async def test_gate_blocks_context_that_never_names_the_municipio(real_env: None) -> None:
    """The atrativo is named but the record's município never is → likely a wrong record."""
    em_outra_cidade = [
        {
            "title": "Praia da Costa",
            "url": "https://example.com/praia-da-costa",
            "excerpts": ["A Praia da Costa, em Guarapari, tem calçadão e quiosques."],
        }
    ]
    out, _, llm_route, _ = await _write(em_outra_cidade)

    assert out.prose is None and out.motivo == "municipio_nao_confirmado"
    assert not llm_route.called
    assert out.busca is not None


@respx.mock
async def test_gate_passes_context_that_names_the_atrativo(real_env: None) -> None:
    """Search names the atrativo → Gemini writes with NO tool, over the injected sources."""
    out, parallel, llm_route, redis = await _write(_COM_MENCAO, _PROSA_FUNDAMENTADA)

    assert out.prose == _PROSA_FUNDAMENTADA
    assert out.motivo is None and out.groundedness == 1.0
    assert out.busca is not None and out.busca.search_id == "search_test"

    body = json.loads(llm_route.calls.last.request.content)
    assert body["model"] == "google/gemini-2.5-flash"
    assert "tools" not in body, "cascade mode must not offer web_search"
    assert body["provider"] == {"data_collection": "deny"}
    assert "reasoning" not in body, "thinking stays at OpenRouter's default (off) — §26"
    assert body["messages"][0]["role"] == "system"
    user = body["messages"][1]["content"]
    assert "FONTES ENCONTRADAS NA WEB" in user and "Morro do Moreno" in user
    assert "busque na web" not in user, "an impossible instruction invites 'conforme pesquisei'"

    sent = json.loads(parallel.calls.last.request.content)
    assert sent == {
        "objective": cascade_objective("Praia Da Costa", "Vila Velha", "ES"),
        "search_queries": cascade_queries("Praia Da Costa", "Vila Velha", "ES"),
        "mode": "turbo",
    }
    assert parallel.calls.last.request.headers["x-api-key"] == "test-parallel"
    # OpenRouter's billed cost + one turbo search.
    assert _spent(redis) == pytest.approx(0.0021 + 0.001)


@respx.mock
async def test_ungrounded_prose_is_a_draft_not_a_description(real_env: None) -> None:
    """Mention passes but the prose is memory, not sources → parked, never the description."""
    out, *_ = await _write(_COM_MENCAO, _PROSA_DE_MEMORIA)

    assert out.prose is None and out.motivo == "nao_fundamentada"
    assert out.rascunho == _PROSA_DE_MEMORIA
    assert out.groundedness is not None and out.groundedness < MIN_GROUNDEDNESS
    assert out.busca is not None


@respx.mock
async def test_cut_reply_never_becomes_a_description(real_env: None) -> None:
    """§26.4: a truncated Gemini text has no claims, so groundedness would pass it."""
    out, _, _, redis = await _write(_COM_MENCAO, "Em Vila Velha,", finish="length")

    assert out.prose is None and out.motivo is None
    assert out.busca is not None, "the search was paid — still persisted"
    assert _spent(redis) == pytest.approx(0.0021 + 0.001), "the cut call was billed too"


@respx.mock
async def test_search_failure_keeps_the_floor(real_env: None) -> None:
    """A Parallel outage degrades to "no prose", like an LLM failure — never raises."""
    respx.post(PARALLEL_SEARCH_URL).mock(return_value=httpx.Response(401))
    llm, search = _clients(fakeredis.FakeRedis())

    out = await TourismCopywriter(llm, CASCADE_MODEL, search_client=search).write_cascade(
        "Praia Da Costa", "Vila Velha", "ES"
    )
    assert out.prose is None and out.motivo is None and out.busca is None


# ---------------------------------------------------------------------------
# The deterministic gates
# ---------------------------------------------------------------------------


def test_mention_gate_ignores_generic_words() -> None:
    assert termos_identificadores("Praia Da Costa") == ["costa"]
    assert not menciona("As praias de Vila Velha, com sua orla e seu parque.", "Praia Da Costa")
    assert menciona("CALÇADÃO DA PRAIA DA COSTA", "Praia Da Costa")  # accent/case-proof
    assert not menciona("a Pedra Azul fica na região", "Pedra do Elefante")  # all terms


def test_municipio_gate_whole_words_accent_folded() -> None:
    assert menciona_municipio("O Cristo de UBA, Minas Gerais", "Ubá")
    assert not menciona_municipio("Viagem para Cuba e Aruba", "Ubá"), "substring is not a mention"
    assert menciona_municipio("em Armação dos Búzios, RJ", "Armação dos Búzios")
    assert menciona_municipio("qualquer texto", ""), "no município → not judged"


def test_groundedness_threshold_and_sensory_prose() -> None:
    """A third of the claims loose is already too many (§25); prose with no claim passes."""
    assert 2 / 3 < MIN_GROUNDEDNESS <= 0.75
    assert groundedness_ratio("um lugar tranquilo para ver o mar", "x") == 1.0


def test_emoji_in_the_name_does_not_block_the_gate() -> None:
    """§25: "Figueira Da Esquina 🌳❤️" was blocked because the emoji became a required term."""
    assert menciona("A Figueira da Esquina, em Vitória", "Figueira Da Esquina 🌳❤️")


# ---------------------------------------------------------------------------
# The agent: where the verdicts land on the record, and where the search is stored
# ---------------------------------------------------------------------------


def _busca(results: list[dict]) -> ParallelSearch:
    return ParallelSearch(
        search_id="s1",
        mode="turbo",
        objective="obj",
        queries=["q1", "q2"],
        results=results,
        usage=[{"name": "sku_search", "count": 1}],
        warnings=None,
        usd=0.001,
        latency_ms=560,
    )


class _FakeSearch:
    def __init__(self, results: list[dict]) -> None:
        self.results = results
        self.calls: list[tuple[list[str], str]] = []

    async def search(self, queries: list[str], objective: str) -> ParallelSearch:
        self.calls.append((queries, objective))
        return _busca(self.results)


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


async def _run_agent(rio: MagicMock, results: list[dict], prosa: str) -> tuple:
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent
    from tests.fakes.fake_llm import FakeLLMClient
    from tests.fakes.fake_places import FakePlacesClient

    llm = FakeLLMClient(generate_result=prosa)
    session = MagicMock()
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(),
        session=session,
        llm_client=llm,
        search_client=_FakeSearch(results),
    )
    with patch("brave.lanes.atrativos.places_enrichment.write_audit"), \
         patch("brave.lanes.atrativos.places_enrichment.record_event"), \
         patch("brave.lanes.atrativos.places_enrichment.route_by_score"):
        await agent.run(rio)
    buscas = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], AtrativoBusca)]
    return llm, buscas


async def test_agent_ungrounded_goes_to_dlq_without_description() -> None:
    rio = _rio()
    llm, buscas = await _run_agent(rio, _COM_MENCAO, _PROSA_DE_MEMORIA)

    assert llm.generate_calls[-1]["model"] == CASCADE_MODEL
    assert "descricao_editorial" not in rio.normalized
    assert rio.normalized["descricao_rascunho"] == _PROSA_DE_MEMORIA
    assert rio.normalized["descricao_gate"] == "nao_fundamentada"
    assert rio.normalized["descricao_attempts"] == 1
    assert (rio.routing, rio.dlq_reason) == ("dlq", "descricao_nao_fundamentada")
    assert len(buscas) == 1


async def test_agent_gate_block_keeps_record_moving_and_stores_the_search() -> None:
    rio = _rio()
    llm, buscas = await _run_agent(rio, _GENERICO, "nunca")

    assert llm.generate_calls == []
    assert "descricao_editorial" not in rio.normalized
    assert rio.normalized["descricao_gate"] == "sem_mencao"
    assert rio.routing == "mar", "no description is not a defect of the record"
    # The paid search is kept whole even though nothing was written.
    (b,) = buscas
    assert b.canonical_key == "tripadvisor:attraction:1"
    assert (b.nome, b.municipio, b.uf) == ("Praia Da Costa", "Vila Velha", "ES")
    assert b.provider == "parallel" and b.mode == "turbo" and b.results == _GENERICO
    assert b.queries == ["q1", "q2"] and b.usd_cost == 0.001 and b.latency_ms == 560


async def test_agent_municipio_not_confirmed_goes_to_dlq() -> None:
    rio = _rio()
    em_outra_cidade = [{"title": "Praia da Costa", "url": "u", "excerpts": ["A Praia da Costa, em Guarapari."]}]
    llm, buscas = await _run_agent(rio, em_outra_cidade, "nunca")

    assert llm.generate_calls == []
    assert rio.normalized["descricao_gate"] == "municipio_nao_confirmado"
    assert (rio.routing, rio.dlq_reason) == ("dlq", "municipio_nao_confirmado")
    assert len(buscas) == 1


async def test_agent_grounded_prose_is_written() -> None:
    rio = _rio()
    _, buscas = await _run_agent(rio, _COM_MENCAO, _PROSA_FUNDAMENTADA)

    assert rio.normalized["descricao_editorial"] == _PROSA_FUNDAMENTADA
    assert rio.normalized["descricao_gate"] is None
    assert rio.normalized["descricao_groundedness"] == 1.0
    assert len(buscas) == 1


# ---------------------------------------------------------------------------
# Tavily — no longer wired into the cascade, but its retry policy is the one Parallel reuses
# ---------------------------------------------------------------------------


@respx.mock
async def test_tavily_rate_limit_waits_instead_of_failing(real_env: None) -> None:
    """429 + retry-after is backpressure, not failure (§25: 84 of 150 failed without this)."""
    from brave.clients.tavily import RealTavilyClient

    route = respx.post(TAVILY_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(200, json={"results": [{"title": "Praia da Costa", "url": "u", "content": "c"}]}),
        ]
    )
    assert "Praia da Costa" in await RealTavilyClient("k").search("q")
    assert route.call_count == 3


def test_tavily_client_refuses_offline() -> None:
    from brave.clients.tavily import RealTavilyClient

    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        RealTavilyClient("k")


def test_format_results_keeps_url_as_evidence() -> None:
    ctx = format_results([{"title": "Praia da Costa", "url": "https://x/y", "content": "c"}])
    assert ctx.splitlines()[:2] == ["Praia da Costa", "https://x/y"]


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
