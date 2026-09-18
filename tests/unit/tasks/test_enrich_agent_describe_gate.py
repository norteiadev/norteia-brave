"""Rule: a description is ONLY written by brave.describe_uf (Painel "describe" action).

_enrich_agent is shared by enrich_places_task (sweeps) and describe_uf; with every flag ON
it must still build a description-less agent unless the caller passes describe=True.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from brave.tasks import pipeline


@pytest.mark.parametrize(("describe", "expected"), [(False, False), (True, True)])
def test_only_describe_turns_the_copywriter_on(monkeypatch, describe, expected):
    app_config = MagicMock(run_real_externals=False)  # Null Places client, no network
    effective = MagicMock(places_enrichment_enabled=False)
    monkeypatch.setattr(pipeline, "_description_on", lambda a, e: True)  # flags all ON
    monkeypatch.setattr(pipeline, "_cascade_search_client", lambda a, e, r: None)
    monkeypatch.setattr("brave.clients.llm.RealLLMClient", MagicMock())
    built = MagicMock()
    monkeypatch.setattr("brave.lanes.atrativos.places_enrichment.PlacesEnrichmentAgent", built)

    ctx = pipeline._EnrichCtx(app_config, effective, None, None, None)
    pipeline._enrich_agent(MagicMock(), ctx, describe=describe)

    assert built.call_args.kwargs["description_enabled"] is expected
