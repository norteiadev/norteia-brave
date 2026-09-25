"""ProviderBalanceError halts describe_uf / enrich_places_task and pauses the motor.

100% offline: fakeredis, a MagicMock DB session, fake adapters via clients_for and a stub
agent. No DB, no external API.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import fakeredis

from brave.clients.factory import Clients
from brave.config.settings import LLMConfig
from brave.core import engine as collection_engine
from brave.shared.exceptions import ProviderBalanceError
from brave.tasks import pipeline
from tests.fakes.fake_llm import FakeLLMClient
from tests.fakes.fake_places import FakePlacesClient

_ON = MagicMock(
    run_real_externals=True,
    description_enrichment_enabled=True,
    atrativo_description_batch_enabled=False,
    atrativo_description_cascade_enabled=False,
    llm=LLMConfig(openrouter_api_key="or", usd_daily_budget=10.0),
)


def _run_describe(monkeypatch, agent):
    """Run describe_uf("SP") over 3 atrativos with ``agent``; returns what to assert on."""
    fake = fakeredis.FakeStrictRedis()
    collection_engine.start(
        fake, None, action="describe", depth="descricao", source="descricao",
        ufs=["SP"], lane="atrativos",
    )
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)

    session = MagicMock()
    session.get.side_effect = lambda model, rio_id: MagicMock(
        id=rio_id, uf="SP", normalized={"name": str(rio_id)}
    )
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, MagicMock()))
    monkeypatch.setattr(pipeline, "AppConfig", lambda: _ON)
    monkeypatch.setattr(pipeline, "load_effective_config", lambda s, r=None: _ON)
    monkeypatch.setattr("brave.shared.ibge_distritos.load_distritos", lambda s: [])
    monkeypatch.setattr(
        pipeline,
        "clients_for",
        lambda c, **k: Clients(c, places=FakePlacesClient(), llm=FakeLLMClient()),
    )

    monkeypatch.setattr(
        "brave.domains.places.places_enrichment.PlacesEnrichmentAgent", lambda **k: agent
    )
    lifecycle = MagicMock()
    monkeypatch.setattr(pipeline, "_producer_done", lifecycle)

    run = pipeline.describe_uf.run  # the real body, captured before the name is swapped
    chain = MagicMock()
    monkeypatch.setattr(pipeline, "describe_uf", chain)

    ids = sorted(uuid.uuid4() for _ in range(3))
    session.scalars.return_value.all.return_value = ids

    run("SP")  # must not raise

    return fake, session, chain, lifecycle, ids


class _Agent:
    def __init__(self, fail_from=0):
        self.fail_from = fail_from  # the search hits the billing wall from this call on
        self.calls = 0
        self.rows = None  # the describe_uf _RowBuffer, set by the test
        self.ran: list = []

    def wants_description(self, rio):
        return True

    async def write_description(self, nome, municipio, uf, details, local=""):
        self.calls += 1
        if self.calls > self.fail_from:
            raise ProviderBalanceError("tavily")
        if self.rows is not None:
            self.rows.add(f"spend:{nome}")
        return f"desc:{nome}"

    async def run(self, rio, description=None):
        self.ran.append((str(rio.id), description))


def test_describe_uf_halts_and_pauses_on_provider_balance_error(monkeypatch):
    """A ProviderBalanceError raised mid-chunk halts describe_uf: no self-chain, no
    exception escapes the task, and the motor is paused with a reason."""
    agent = _Agent()
    fake, _session, chain, lifecycle, _ids = _run_describe(monkeypatch, agent)

    assert agent.ran == []  # every search failed first
    chain.delay.assert_not_called()  # no self-chain
    lifecycle.assert_called_once()  # terminal run still decrements inflight
    status = collection_engine.get_status(fake)
    assert status["mode"] == collection_engine.PAUSADO
    assert status["pause_reason"]["reason"] == "provider_balance"
    assert status["pause_reason"]["provider"] == "tavily"
    assert status["pause_reason"]["action"] == "describe"


def test_describe_uf_balance_mid_chunk_keeps_fetched_descriptions_and_spend(monkeypatch):
    """The wall on the 3rd search: the 2 descriptions already fetched are still written,
    their spend rows committed, and only then does the motor pause (Q6)."""
    agent = _Agent(fail_from=2)

    class _Buf(pipeline._RowBuffer):
        def __init__(self):
            super().__init__()
            agent.rows = self

    monkeypatch.setattr(pipeline, "_RowBuffer", _Buf)
    fake, session, chain, lifecycle, ids = _run_describe(monkeypatch, agent)

    fetched = [str(i) for i in ids[:2]]
    assert agent.ran == [(n, f"desc:{n}") for n in fetched]
    session.add_all.assert_called_once_with([f"spend:{n}" for n in fetched])
    chain.delay.assert_not_called()
    lifecycle.assert_called_once()
    reason = collection_engine.get_status(fake)["pause_reason"]
    assert (reason["reason"], reason["action"]) == ("provider_balance", "describe")


class _InFlightAgent(_Agent):
    """1st search is slow and succeeds; the 2nd hits the wall while the 1st is in flight."""

    async def write_description(self, nome, municipio, uf, details, local=""):
        import asyncio  # noqa: PLC0415

        self.calls += 1
        if self.calls > 1:
            raise ProviderBalanceError("tavily")
        await asyncio.sleep(0.05)
        if self.rows is not None:
            self.rows.add(f"spend:{nome}")
        return f"desc:{nome}"


def test_describe_uf_balance_waits_for_searches_already_in_flight(monkeypatch):
    """A wall hit while another search is in flight: that paid search still finishes and
    its description + spend row are written (not cancelled with the gather)."""
    agent = _InFlightAgent()

    class _Buf(pipeline._RowBuffer):
        def __init__(self):
            super().__init__()
            agent.rows = self

    monkeypatch.setattr(pipeline, "_RowBuffer", _Buf)
    fake, session, _chain, _lifecycle, ids = _run_describe(monkeypatch, agent)

    first = str(ids[0])
    assert agent.ran == [(first, f"desc:{first}")]
    session.add_all.assert_called_once_with([f"spend:{first}"])
    assert collection_engine.get_status(fake)["pause_reason"]["action"] == "describe"


def test_enrich_places_task_pauses_on_provider_balance_error_no_retry_no_quarantine(
    monkeypatch,
):
    """enrich_places_task calls pause_with_reason (no retry, no PoisonQuarantine row)
    when the agent raises ProviderBalanceError. A chain task pauses with action None:
    Continuar only lifts the pause, it starts no run."""
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)

    session = MagicMock()
    rio_uuid = uuid.uuid4()
    session.get.return_value = MagicMock(id=rio_uuid)
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, MagicMock()))

    def _boom(session, rio, ctx=None):
        raise ProviderBalanceError("google_places")

    monkeypatch.setattr(pipeline, "_enrich_one", _boom)
    quarantine = MagicMock()
    monkeypatch.setattr("brave.core.quarantine.quarantine_poison", quarantine)

    pipeline.enrich_places_task.run(str(rio_uuid))  # must not raise, must not retry

    quarantine.assert_not_called()
    status = collection_engine.get_status(fake)
    assert status["mode"] == collection_engine.PAUSADO
    assert status["pause_reason"]["reason"] == "provider_balance"
    assert status["pause_reason"]["provider"] == "google_places"
    assert status["pause_reason"]["action"] is None


if __name__ == "__main__":  # pragma: no cover — ponytail runnable check
    pass
