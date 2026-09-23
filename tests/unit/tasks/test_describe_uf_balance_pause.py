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


def test_describe_uf_halts_and_pauses_on_provider_balance_error(monkeypatch):
    """A ProviderBalanceError raised mid-chunk halts describe_uf: no self-chain, no
    exception escapes the task, and the motor is paused with a reason."""
    fake = fakeredis.FakeStrictRedis()
    fake.set(collection_engine._STATE_KEY, collection_engine.RUNNING)
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
        lambda a, e=None, **k: Clients(a, e, places=FakePlacesClient(), llm=FakeLLMClient()),
    )

    class Agent:
        def wants_description(self, rio):
            return True

        async def write_description(self, nome, municipio, uf, details, local=""):
            raise ProviderBalanceError("tavily")

        async def run(self, rio, description=None):
            raise AssertionError("run() must never be reached — the search failed first")

    monkeypatch.setattr(
        "brave.lanes.atrativos.places_enrichment.PlacesEnrichmentAgent", lambda **k: Agent()
    )
    lifecycle = MagicMock()
    monkeypatch.setattr(pipeline, "_producer_finally_lifecycle", lifecycle)

    run = pipeline.describe_uf.run  # the real body, captured before the name is swapped
    chain = MagicMock()
    monkeypatch.setattr(pipeline, "describe_uf", chain)

    ids = sorted(uuid.uuid4() for _ in range(3))
    session.scalars.return_value.all.return_value = ids

    run("SP")  # must not raise

    chain.delay.assert_not_called()  # no self-chain
    lifecycle.assert_called_once()  # terminal run still decrements inflight
    status = collection_engine.get_status(fake)
    assert status["mode"] == collection_engine.PAUSADO
    assert status["pause_reason"]["reason"] == "provider_balance"
    assert status["pause_reason"]["provider"] == "tavily"
    assert status["pause_reason"]["action"] == "describe"


def test_enrich_places_task_pauses_on_provider_balance_error_no_retry_no_quarantine(
    monkeypatch,
):
    """enrich_places_task calls pause_with_reason (no retry, no PoisonQuarantine row)
    when the agent raises ProviderBalanceError."""
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
    assert status["pause_reason"]["action"] == "describe"


if __name__ == "__main__":  # pragma: no cover — ponytail runnable check
    pass
