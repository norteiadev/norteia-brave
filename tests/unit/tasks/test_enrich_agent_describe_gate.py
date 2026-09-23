"""Rule: a description is ONLY written by brave.describe_uf (Painel "describe" action).

enrich_places_task shares the agent build with describe_uf; with every description flag ON
it must still build a description-less agent (describe_uf's side is asserted in
test_describe_uf::test_clients_and_agent_built_once_per_chunk).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from brave.clients.factory import Clients
from brave.clients.null_llm import NullLLMClient
from brave.tasks import pipeline
from tests.fakes.fake_llm import FakeLLMClient
from tests.fakes.fake_places import FakePlacesClient

_ALL_ON = MagicMock(
    run_real_externals=True,
    places_enrichment_enabled=True,
    description_enrichment_enabled=True,
    atrativo_description_batch_enabled=False,
    atrativo_description_cascade_enabled=False,
)


def test_enrich_places_task_never_turns_the_copywriter_on(monkeypatch):
    session = MagicMock()
    session.get.return_value = MagicMock(id=uuid.uuid4())
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, MagicMock()))
    monkeypatch.setattr(pipeline, "AppConfig", lambda: _ALL_ON)
    monkeypatch.setattr(pipeline, "load_effective_config", lambda s, r=None: _ALL_ON)
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr("brave.shared.ibge_distritos.load_distritos", lambda s: [])
    places = FakePlacesClient()
    monkeypatch.setattr(
        pipeline,
        "clients_for",
        lambda a, e=None, **k: Clients(a, e, places=places, llm=FakeLLMClient()),
    )
    built = MagicMock(return_value=MagicMock(run=AsyncMock()))
    monkeypatch.setattr("brave.lanes.atrativos.places_enrichment.PlacesEnrichmentAgent", built)

    pipeline.enrich_places_task.run(str(uuid.uuid4()))

    kwargs = built.call_args.kwargs
    assert kwargs["description_enabled"] is False
    assert isinstance(kwargs["llm_client"], NullLLMClient)
    assert kwargs["search_client"] is None
    assert kwargs["places_client"] is places  # places flag ON → the factory's adapter
