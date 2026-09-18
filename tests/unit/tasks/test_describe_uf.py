"""Unit tests: brave.describe_uf — the per-UF description producer (engine action "describe").

The TA sweep never writes descriptions; describe_uf backfills them per UF through the
same agent as enrich_places_task, 25 ids per run on an id keyset cursor, self-chaining
while the chunk comes back full. Only the terminal run of a chain decrements the engine
inflight counter. Inside a chunk the copywriter I/O is gathered (_DESCRIBE_CONCURRENCY at
a time) and the Session writes stay serial.

100% offline: fakeredis, a MagicMock DB session, a fake agent and the self-chain .delay
replaced by spies. No DB, no external API.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import MagicMock

import fakeredis
import pytest
from sqlalchemy.dialects import postgresql

from brave.config.settings import LLMConfig
from brave.core import engine as collection_engine
from brave.observability.cost_guard import _daily_key, record_spend
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
    session.get.side_effect = lambda model, rio_id: MagicMock(
        id=rio_id, uf="SP", normalized={"name": str(rio_id)}
    )
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, MagicMock()))
    monkeypatch.setattr(pipeline, "AppConfig", lambda: _ON)
    monkeypatch.setattr(pipeline, "load_effective_config", lambda s, r=None: _ON)

    enriched: list = []
    ctx_builds = MagicMock(side_effect=lambda s, r=None: object())
    monkeypatch.setattr(pipeline, "_enrich_ctx", ctx_builds)

    class Agent:
        """Stands in for PlacesEnrichmentAgent: I/O half + write half, both spied."""

        inflight = peak = 0
        on_fetch = on_write = staticmethod(lambda rio_id: None)

        def wants_description(self, rio):
            return True

        async def write_description(self, nome, municipio, uf, details, local=""):
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            try:
                self.on_fetch(uuid.UUID(nome))
                await asyncio.sleep(0)
            finally:
                self.inflight -= 1
            return ("prosa", None, False)

        async def run(self, rio, description=None):
            assert description == ("prosa", None, False)
            self.on_write(rio.id)
            enriched.append(rio.id)

    agent = Agent()
    agent_builds = MagicMock(side_effect=lambda *a, **k: (agent, None))
    monkeypatch.setattr(pipeline, "_enrich_agent", agent_builds)
    lifecycle = MagicMock()
    monkeypatch.setattr(pipeline, "_producer_finally_lifecycle", lifecycle)

    run = pipeline.describe_uf.run  # the real body, captured before the name is swapped
    chain = MagicMock()
    monkeypatch.setattr(pipeline, "describe_uf", chain)

    class H:
        pass

    h = H()
    h.agent, h.agent_builds, h.ctx_builds = agent, agent_builds, ctx_builds
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


def test_ctx_and_agent_built_once_per_chunk(harness):
    """Reference tables, config and clients are built once for the chunk, not per atrativo."""
    ids = harness.ids(3)
    harness.run("ES")
    assert harness.ctx_builds.call_count == 1
    assert harness.agent_builds.call_count == 1
    assert harness.enriched == ids


def test_io_is_concurrent_but_bounded(harness):
    ids = harness.ids(pipeline._DESCRIBE_CHUNK)
    harness.run("SP")
    assert harness.agent.peak == pipeline._DESCRIBE_CONCURRENCY
    assert harness.enriched == ids  # written serially, in id order


def test_cost_guard_tripped_mid_chunk_stops_launching_io(harness):
    """Spend recorded by the records in flight trips the guard for the ones still queued:
    the check runs per record right before its I/O, not once per chunk."""
    ids = harness.ids(pipeline._DESCRIBE_CHUNK)
    harness.agent.on_fetch = lambda rio_id: record_spend(harness.redis, 4.0)
    harness.run("SP")

    assert harness.enriched == ids[:3]  # 4 + 4 + 4 >= 10: the 4th is never launched
    harness.chain.delay.assert_not_called()
    harness.lifecycle.assert_called_once()


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

    def flaky(rio_id):
        if rio_id == ids[0]:
            raise RuntimeError("copywriter blew up")

    harness.agent.on_fetch = flaky
    harness.run("SP")

    assert harness.enriched == ids[1:]
    # one to end the read transaction before the gather, one for the failed record
    assert harness.session.rollback.call_count == 2
    harness.lifecycle.assert_called_once()


def test_soft_time_limit_hands_the_rest_of_the_uf_on(harness, monkeypatch):
    """SoftTimeLimitExceeded is an Exception: swallowed per-record, the hard kill would
    skip the finally and leak inflight. It must end the chunk and chain past the record."""
    from celery.exceptions import SoftTimeLimitExceeded

    ids = harness.ids(pipeline._DESCRIBE_CHUNK)

    def slow(rio_id):
        if rio_id == ids[2]:
            raise SoftTimeLimitExceeded()

    harness.agent.on_write = slow
    harness.run("SP", max_n=60)

    assert harness.enriched == ids[:2]
    # the cursor moves past the whole chunk: the dropped records burn no attempt and the
    # next describe run selects them again
    harness.chain.delay.assert_called_once_with("SP", 35, after_id=str(ids[-1]))
    harness.lifecycle.assert_not_called()


def test_soft_time_limit_during_io_still_chains(harness):
    from celery.exceptions import SoftTimeLimitExceeded

    ids = harness.ids(pipeline._DESCRIBE_CHUNK)

    def slow(rio_id):
        if rio_id == ids[2]:
            raise SoftTimeLimitExceeded()

    harness.agent.on_fetch = slow
    harness.run("SP")

    harness.chain.delay.assert_called_once_with("SP", None, after_id=str(ids[-1]))
    harness.lifecycle.assert_not_called()


def test_soft_time_limit_in_the_idle_event_loop_still_chains(harness, monkeypatch):
    """Celery raises the soft limit from a signal handler: with the loop idle in select()
    it surfaces in asyncio.run, past every handler inside _describe_chunk."""
    import signal

    from celery.exceptions import SoftTimeLimitExceeded

    ids = harness.ids(pipeline._DESCRIBE_CHUNK)

    async def hang(nome, municipio, uf, details):
        await asyncio.sleep(30)

    def _raise(*_a):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(harness.agent, "write_description", hang)
    old = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, 0.2)
    try:
        harness.run("SP")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)

    harness.chain.delay.assert_called_once_with("SP", None, after_id=str(ids[-1]))
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


def test_describe_agent_fails_before_the_agent_on_empty_gemini_key(monkeypatch):
    """The cascade build guard lives in _enrich_agent's describe path (the only one that
    writes descriptions): a gemini-* writer with no key raises BEFORE
    PlacesEnrichmentAgent exists, so no descricao_attempt is burned."""
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
        session = MagicMock()
        pipeline._enrich_agent(session, pipeline._enrich_ctx(session), describe=True)
    built.assert_not_called()
