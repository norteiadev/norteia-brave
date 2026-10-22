"""Unit tests: brave.refresh_places — the daily Place Details refresh beat.

100% offline: a MagicMock DB session, a stub agent and spies on promote/publish.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from brave.shared.exceptions import CostGuardError, ProviderBalanceError
from brave.tasks import pipeline


def _effective(**over):
    base = dict(run_real_externals=True, places_enrichment_enabled=True, places_refresh_daily_limit=5)
    return SimpleNamespace(**{**base, **over})


@pytest.fixture
def harness(monkeypatch):
    ids = [uuid.uuid4(), uuid.uuid4()]
    rios = {i: MagicMock(id=i, routing="mar") for i in ids}
    session = MagicMock()
    session.get.side_effect = lambda model, rid, **k: rios[rid]
    session.scalar.return_value = None  # no active Mar row by default
    ns = SimpleNamespace(ids=ids, rios=rios, session=session, runs=[], published=[],
                         effective=_effective(), fail_with=None)

    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, MagicMock()))
    monkeypatch.setattr(pipeline, "_enrich_ctx",
                        lambda s: SimpleNamespace(effective=ns.effective, ibge_lookup=None))
    monkeypatch.setattr(pipeline, "_places_refresh_due", lambda s, limit: ns.ids[:limit])
    monkeypatch.setattr(pipeline, "clients_for", lambda *a, **k: MagicMock())
    monkeypatch.setattr(pipeline, "_beat_health", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_dispatch_chain", lambda task, rid: ns.published.append(rid))

    class Agent:
        async def run(self, rio, refresh=False):
            assert refresh is True
            if ns.fail_with:
                raise ns.fail_with
            ns.runs.append(rio.id)

    async def _using(clients, coro):
        return await coro

    monkeypatch.setattr(pipeline, "_using", _using)
    monkeypatch.setattr(pipeline, "_enrich_agent", lambda *a, **k: Agent())
    monkeypatch.setattr("brave.core.mar.service.promote_to_mar", lambda s, rio: MagicMock())
    return ns


def test_refreshes_every_due_record(harness):
    assert pipeline.refresh_places() == 2
    assert harness.runs == harness.ids
    assert harness.published == []  # none had an active Mar row


def test_record_in_mar_is_republished(harness):
    harness.session.scalar.return_value = uuid.uuid4()
    pipeline.refresh_places()
    assert harness.published == [str(i) for i in harness.ids]


@pytest.mark.parametrize("off", [
    {"run_real_externals": False},
    {"places_enrichment_enabled": False},
    {"places_refresh_daily_limit": 0},
])
def test_gated_off_spends_nothing(harness, off):
    harness.effective = _effective(**off)
    assert pipeline.refresh_places() == 0
    assert harness.runs == []


def test_balance_wall_stops_the_run(harness):
    harness.fail_with = ProviderBalanceError("places")
    with pytest.raises(ProviderBalanceError):
        pipeline.refresh_places()


def test_places_cost_cap_stops_the_run(harness):
    harness.fail_with = CostGuardError("places daily cap")
    with pytest.raises(CostGuardError):
        pipeline.refresh_places()
    assert harness.session.rollback.call_count == 1


def test_one_bad_record_does_not_abort(harness):
    harness.fail_with = RuntimeError("boom")
    assert pipeline.refresh_places() == 0
    assert harness.session.rollback.call_count == 2


def test_due_query_compiles():
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    pipeline._places_refresh_due(session, 10)
    params = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect()).params
    assert all(k in list(params.values()) for k in ("places_refreshed_at", "place_id_cache"))


@pytest.mark.parametrize("env, expected", [
    ({"BRAVE_PLACES_REFRESH_API_KEY": "refresh-key"}, "BRAVE_PLACES_REFRESH_API_KEY"),
    ({}, "BRAVE_PLACES_API_KEY"),
])
def test_refresh_uses_its_own_key_when_set(harness, monkeypatch, env, expected):
    monkeypatch.delenv("BRAVE_PLACES_REFRESH_API_KEY", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    seen = []
    monkeypatch.setattr(pipeline, "clients_for",
                        lambda *a, **k: seen.append(k.get("places_api_key_env")) or MagicMock())
    pipeline.refresh_places()
    assert seen and set(seen) == {expected}


def test_hybrid_refresh_uses_one_bag_and_one_prefetch_for_the_lot(harness, monkeypatch):
    """places_provider=hybrid: an Apify run costs ~60 s fixed, so the due lot is prefetched
    once (place_ids) inside ONE Clients bag, then each record runs as in google mode."""
    harness.effective = _effective(places_provider="hybrid")
    built, prefetched = [], []

    class HybridAgent:
        prefetches = True

        def prefetch_items(self, rios, *, refresh=False):
            return [(r.id, refresh) for r in rios]

        async def prefetch(self, items):
            prefetched.append(([i for i, _ in items], all(r for _, r in items)))

        async def run(self, rio, refresh=False):
            assert refresh is True
            harness.runs.append(rio.id)

    monkeypatch.setattr(pipeline, "clients_for", lambda *a, **k: built.append(k) or MagicMock())
    monkeypatch.setattr(pipeline, "_enrich_agent", lambda *a, **k: HybridAgent())
    harness.session.scalars.return_value.all.return_value = list(harness.rios.values())

    assert pipeline.refresh_places() == 2
    assert len(built) == 1 and prefetched == [(harness.ids, True)]
    assert harness.runs == harness.ids
    assert harness.session.commit.call_count == 2


def test_hybrid_refresh_prefetches_one_run_worth_then_commits_it(harness, monkeypatch):
    """Runs are ≤ APIFY_RUN_MAX_QUERIES: the lot goes run by run, each part committed before
    the next run, so the time limit never kills a lot whose runs were all paid up front."""
    harness.effective = _effective(places_provider="hybrid")
    events = []

    class HybridAgent:
        prefetches = True

        def prefetch_items(self, rios, *, refresh=False):
            return [r.id for r in rios]

        async def prefetch(self, items):
            events.append(("prefetch", items))

        async def run(self, rio, refresh=False):
            events.append(("run", rio.id))

    monkeypatch.setattr("brave.clients.hybrid_places.APIFY_RUN_MAX_QUERIES", 1)
    monkeypatch.setattr(pipeline, "_enrich_agent", lambda *a, **k: HybridAgent())
    a, b = harness.ids
    harness.session.scalars.return_value.all.side_effect = [[harness.rios[a]], [harness.rios[b]]]

    assert pipeline.refresh_places() == 2
    assert events == [("prefetch", [a]), ("run", a), ("prefetch", [b]), ("run", b)]


def test_google_refresh_never_prefetches(harness, monkeypatch):
    built = []
    monkeypatch.setattr(pipeline, "clients_for", lambda *a, **k: built.append(k) or MagicMock())
    pipeline.refresh_places()
    # A bag per record, as before the hybrid (the hybrid branch builds ONE for the lot).
    assert len(built) == len(harness.ids)
    harness.session.scalars.assert_not_called()  # _prefetch_places is not reached
