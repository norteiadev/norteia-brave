"""Unit tests: brave.describe_uf — the per-UF description producer (engine action "describe").

The TA sweep never writes descriptions; describe_uf backfills them per UF through the
same _enrich_one path as enrich_places_task, 25 ids per run on an id keyset cursor,
self-chaining while the chunk comes back full. Only the terminal run of a chain
decrements the engine inflight counter.

100% offline: fakeredis, a MagicMock DB session, _enrich_one and the self-chain .delay
replaced by spies. No DB, no external API.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import fakeredis
import pytest
from sqlalchemy.dialects import postgresql

from brave.config.settings import LLMConfig
from brave.core import engine as collection_engine
from brave.observability.cost_guard import _daily_key
from brave.tasks import pipeline

_ON = MagicMock(
    run_real_externals=True,
    description_enrichment_enabled=True,
    atrativo_description_batch_enabled=False,
    atrativo_description_cascade_enabled=False,
    llm=LLMConfig(openrouter_api_key="or", usd_daily_budget=10.0),
)


@pytest.fixture
def harness(monkeypatch):
    """Wire describe_uf to fakes; return a namespace the test drives + inspects."""
    fake = fakeredis.FakeStrictRedis()
    fake.set(collection_engine._STATE_KEY, collection_engine.RUNNING)
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)

    session = MagicMock()
    session.get.side_effect = lambda model, rio_id: MagicMock(id=rio_id)
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, MagicMock()))
    monkeypatch.setattr(pipeline, "AppConfig", lambda: _ON)
    monkeypatch.setattr(pipeline, "load_effective_config", lambda s, r=None: _ON)

    enriched: list = []
    monkeypatch.setattr(pipeline, "_enrich_one", lambda s, rio: enriched.append(rio.id))
    lifecycle = MagicMock()
    monkeypatch.setattr(pipeline, "_producer_finally_lifecycle", lifecycle)

    run = pipeline.describe_uf.run  # the real body, captured before the name is swapped
    chain = MagicMock()
    monkeypatch.setattr(pipeline, "describe_uf", chain)

    class H:
        pass

    h = H()
    h.redis, h.session, h.enriched, h.lifecycle, h.chain, h.run = (
        fake, session, enriched, lifecycle, chain, run
    )

    def ids(n):
        out = sorted(uuid.uuid4() for _ in range(n))
        session.scalars.return_value.all.return_value = out
        return out

    h.ids = ids
    return h


def _stmt(h):
    stmt = h.session.scalars.call_args.args[0]
    return stmt, stmt.compile(dialect=postgresql.dialect())


def test_selects_by_uf_with_cursor_and_chunk_limit(harness):
    harness.ids(0)
    after = str(uuid.uuid4())
    harness.run("SP", after_id=after)

    stmt, compiled = _stmt(harness)
    sql = str(compiled)
    assert "rio_records.uf = " in sql and "SP" in compiled.params.values()
    assert "rio_records.id > " in sql and uuid.UUID(after) in compiled.params.values()
    # the shared copy_batch predicate
    assert "descricao_batch_id IS NULL" in sql
    assert "descricao_editorial" in compiled.params.values()
    assert "FOR UPDATE" not in sql
    # only records whose paid Places sub-step already ran (no Places spend here)
    assert "google_enriched" in compiled.params.values()
    assert stmt._limit_clause.value == pipeline._DESCRIBE_CHUNK


def test_max_n_caps_the_chunk(harness):
    harness.ids(0)
    harness.run("SP", max_n=3)
    stmt, _ = _stmt(harness)
    assert stmt._limit_clause.value == 3


def test_full_chunk_self_chains_from_last_id_without_decrement(harness):
    ids = harness.ids(pipeline._DESCRIBE_CHUNK)
    harness.run("SP", max_n=60)

    assert harness.enriched == ids
    harness.chain.delay.assert_called_once_with("SP", 35, after_id=str(ids[-1]))
    harness.lifecycle.assert_not_called()  # the inflight token rides the chain


def test_uncapped_full_chunk_chains_with_none(harness):
    ids = harness.ids(pipeline._DESCRIBE_CHUNK)
    harness.run("SP")
    harness.chain.delay.assert_called_once_with("SP", None, after_id=str(ids[-1]))


def test_short_chunk_is_terminal(harness):
    ids = harness.ids(3)
    harness.run("SP")

    assert harness.enriched == ids
    harness.chain.delay.assert_not_called()
    harness.lifecycle.assert_called_once()


def test_budget_spent_is_terminal(harness):
    harness.ids(pipeline._DESCRIBE_CHUNK)
    harness.run("SP", max_n=pipeline._DESCRIBE_CHUNK)
    harness.chain.delay.assert_not_called()
    harness.lifecycle.assert_called_once()


def test_halt_stops_before_next_record_and_is_terminal(harness, monkeypatch):
    ids = harness.ids(pipeline._DESCRIBE_CHUNK)
    calls = iter([False, False, True])
    monkeypatch.setattr(collection_engine, "should_halt_producer", lambda rc: next(calls))
    harness.run("SP")

    assert harness.enriched == ids[:2]
    harness.chain.delay.assert_not_called()
    harness.lifecycle.assert_called_once()


def test_record_failure_is_logged_and_the_chunk_continues(harness, monkeypatch):
    ids = harness.ids(3)
    done: list = []

    def flaky(session, rio):
        if rio.id == ids[0]:
            raise RuntimeError("copywriter blew up")
        done.append(rio.id)

    monkeypatch.setattr(pipeline, "_enrich_one", flaky)
    harness.run("SP")

    assert done == ids[1:]
    harness.session.rollback.assert_called_once()
    harness.lifecycle.assert_called_once()


def test_soft_time_limit_hands_the_rest_of_the_uf_on(harness, monkeypatch):
    """SoftTimeLimitExceeded is an Exception: swallowed per-record, the hard kill would
    skip the finally and leak inflight. It must end the chunk and chain past the record."""
    from celery.exceptions import SoftTimeLimitExceeded

    ids = harness.ids(pipeline._DESCRIBE_CHUNK)
    done: list = []

    def slow(session, rio):
        if rio.id == ids[2]:
            raise SoftTimeLimitExceeded()
        done.append(rio.id)

    monkeypatch.setattr(pipeline, "_enrich_one", slow)
    harness.run("SP", max_n=60)

    assert done == ids[:2]
    harness.chain.delay.assert_called_once_with("SP", 57, after_id=str(ids[2]))
    harness.lifecycle.assert_not_called()


def test_tripped_cost_guard_ends_the_chain(harness):
    ids = harness.ids(pipeline._DESCRIBE_CHUNK)
    harness.redis.set(_daily_key(), "10.0")  # budget already spent today
    harness.run("SP")

    assert harness.enriched == []
    assert len(ids) == pipeline._DESCRIBE_CHUNK
    harness.chain.delay.assert_not_called()
    harness.lifecycle.assert_called_once()


def test_misconfigured_cascade_exits_without_processing(harness, monkeypatch):
    def boom(*_a):
        raise RuntimeError("cascade model 'gemini-2.5-flash' needs BRAVE_LLM_GEMINI_API_KEY")

    monkeypatch.setattr(pipeline, "_cascade_search_client", boom)
    harness.ids(pipeline._DESCRIBE_CHUNK)
    harness.run("SP")

    harness.session.scalars.assert_not_called()
    assert harness.enriched == []
    harness.chain.delay.assert_not_called()
    harness.lifecycle.assert_called_once()


def test_description_off_exits_without_processing(harness, monkeypatch):
    off = MagicMock(
        run_real_externals=True,
        description_enrichment_enabled=True,
        atrativo_description_batch_enabled=True,  # batch lane owns descriptions
    )
    monkeypatch.setattr(pipeline, "load_effective_config", lambda s, r=None: off)
    harness.ids(5)
    harness.run("SP")

    harness.session.scalars.assert_not_called()
    assert harness.enriched == []
    harness.chain.delay.assert_not_called()
    harness.lifecycle.assert_called_once()


def test_engine_describe_dispatches_describe_uf_per_uf(monkeypatch):
    """engine_sweep_run(action="describe") fans out describe_uf, counting inflight first."""
    fake = fakeredis.FakeStrictRedis()
    fake.set(collection_engine._STATE_KEY, collection_engine.RUNNING)
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    monkeypatch.setenv("BRAVE_ENGINE_UF_DELAY_SECONDS", "0")
    task = MagicMock()
    monkeypatch.setattr(pipeline, "describe_uf", task)
    sweep = MagicMock()
    monkeypatch.setattr(pipeline, "sweep_tripadvisor", sweep)

    pipeline.engine_sweep_run.run(ufs=["SP", "RJ"], max_per_uf=4, action="describe")

    assert [c.args for c in task.delay.call_args_list] == [("SP", 4), ("RJ", 4)]
    assert collection_engine.get_inflight(fake) == 2
    sweep.delay.assert_not_called()


def test_enrich_one_fails_before_the_agent_on_empty_gemini_key(monkeypatch):
    """The cascade build guard now lives in _enrich_one: a gemini-* writer with no key
    raises BEFORE PlacesEnrichmentAgent exists, so no descricao_attempt is burned."""
    from brave.config.settings import LLMConfig

    app = MagicMock(
        run_real_externals=True,
        atrativo_cascade_model="gemini-2.5-flash",
        llm=LLMConfig(openrouter_api_key="or", gemini_api_key=""),
    )
    effective = MagicMock(
        places_enrichment_enabled=False,
        description_enrichment_enabled=True,
        atrativo_description_batch_enabled=False,
        atrativo_description_cascade_enabled=True,
    )
    monkeypatch.setattr(pipeline, "AppConfig", lambda: app)
    monkeypatch.setattr(pipeline, "load_effective_config", lambda s, r=None: effective)
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fakeredis.FakeStrictRedis())
    monkeypatch.setattr("brave.clients.llm.RealLLMClient", lambda **kw: MagicMock())
    built = MagicMock()
    monkeypatch.setattr(
        "brave.lanes.atrativos.places_enrichment.PlacesEnrichmentAgent", built
    )

    with pytest.raises(RuntimeError, match="BRAVE_LLM_GEMINI_API_KEY"):
        pipeline._enrich_one(MagicMock(), MagicMock(id=uuid.uuid4()))
    built.assert_not_called()
