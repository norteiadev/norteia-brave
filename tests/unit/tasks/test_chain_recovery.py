"""Places chain recovery: sub_state_changed_at + brave.redispatch_stalled_chain.

The listener tests are pure ORM (transient objects, no DB). The sweeper's selection tests
run against the test DB inside one outer transaction (the task's commit only releases a
savepoint) and roll everything back; its gate tests need no DB. No broker: every chain
task's .delay is a spy.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import fakeredis
import pytest
from celery import states
from sqlalchemy import update
from sqlalchemy.orm import Session

from brave.core import engine as collection_engine
from brave.core.models import RioRecord
from brave.core.nascente.service import store_raw
from brave.tasks import pipeline

_OLD = datetime(2026, 1, 1, tzinfo=UTC)


# -- sub_state_changed_at listener ---------------------------------------------------


def test_listener_stamps_only_when_sub_state_changes():
    rio = RioRecord(entity_type="attraction", uf="ZZ", sub_state="discovered")
    assert rio.sub_state_changed_at is not None

    rio.sub_state_changed_at = _OLD
    rio.sub_state = "discovered"  # same value → no stamp
    assert rio.sub_state_changed_at == _OLD

    rio.sub_state = "contacts_found"
    assert rio.sub_state_changed_at > _OLD

    rio.sub_state_changed_at = _OLD
    rio.sub_state = None
    assert rio.sub_state_changed_at > _OLD


def test_listener_leaves_records_without_sub_state_alone():
    assert RioRecord(entity_type="destination", uf="ZZ").sub_state_changed_at is None


# -- redispatch_stalled_chain --------------------------------------------------------


def _config(externals: bool = True, default_lane: bool = True) -> MagicMock:
    return MagicMock(run_real_externals=externals, sources={"default": default_lane})


@pytest.fixture
def spies(monkeypatch):
    """.delay spies per chain task + a LIGADO fakeredis; returns (dispatched, redis)."""
    dispatched: list[tuple[str, str]] = []
    for attr in ("find_contacts_task", "gather_signals_task", "enrich_places_task"):
        monkeypatch.setattr(
            getattr(pipeline, attr), "delay", lambda rid, _a=attr: dispatched.append((_a, rid))
        )
    fake = fakeredis.FakeStrictRedis()
    collection_engine.set_mode(fake, collection_engine.LIGADO)
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    monkeypatch.setattr(pipeline, "_load_config", lambda _s: _config())
    return dispatched, fake


@pytest.mark.parametrize(
    ("mode", "config"),
    [
        (collection_engine.PAUSADO, _config()),
        (collection_engine.DESLIGADO, _config()),
        (collection_engine.LIGADO, _config(externals=False)),
        (collection_engine.LIGADO, _config(default_lane=False)),
    ],
    ids=["pausado", "desligado", "externals-off", "lane-off"],
)
def test_gates_skip_the_whole_round(monkeypatch, spies, mode, config):
    dispatched, fake = spies
    collection_engine.set_mode(fake, mode)
    monkeypatch.setattr(pipeline, "_load_config", lambda _s: config)
    session = MagicMock()
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, None))

    assert pipeline.redispatch_stalled_chain.run() == 0

    assert dispatched == []
    session.execute.assert_not_called()


@pytest.fixture
def tx_session(db_engine, monkeypatch):
    """A session inside an outer transaction that is rolled back; the task gets it too.

    Rows other tests left in flight are parked (sub_state NULL) inside the same
    transaction, so the LIMIT/ordering assertions only see this test's rows.
    """
    conn = db_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    session.execute(
        update(RioRecord)
        .where(RioRecord.sub_state.in_(list(pipeline._CHAIN_NEXT_TASK)))
        .values(sub_state=None)
    )
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, None))
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


def _rio(session, sub_state, changed_at, *, routing="dlq", entity_type="attraction"):
    ref = f"test:chain-recovery:{uuid.uuid4().hex}"
    nascente = store_raw(session, "test", ref, entity_type, "ZZ", {"name": ref})
    rio = RioRecord(
        nascente_id=nascente.id, entity_type=entity_type, uf="ZZ", routing=routing,
        sub_state=sub_state, canonical_key=ref,
    )
    rio.sub_state_changed_at = changed_at  # override the listener's now()
    session.add(rio)
    session.flush()
    return str(rio.id)


@pytest.mark.integration
def test_selects_stalled_records_and_dispatches_the_next_task(tx_session, spies):
    dispatched, _fake = spies
    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    discovered = _rio(tx_session, "discovered", hour_ago)
    contacts = _rio(tx_session, "contacts_found", None)  # unknown age → old enough
    signals = _rio(tx_session, "signals_gathered", hour_ago, routing="in_progress")
    _rio(tx_session, "signals_gathered", hour_ago)  # dlq → never enriched
    _rio(tx_session, "discovered", datetime.now(UTC) - timedelta(minutes=5))  # fresh
    _rio(tx_session, "discovered", hour_ago, entity_type="destination")
    tx_session.commit()

    assert pipeline.redispatch_stalled_chain.run() == 3

    assert sorted(dispatched) == sorted([
        ("find_contacts_task", discovered),
        ("gather_signals_task", contacts),
        ("enrich_places_task", signals),
    ])
    # Re-dispatched rows go to the back of the line: a second round picks nothing.
    dispatched.clear()
    assert pipeline.redispatch_stalled_chain.run() == 0
    assert dispatched == []


@pytest.mark.integration
def test_limit_takes_the_oldest_first(tx_session, spies, monkeypatch):
    dispatched, _fake = spies
    monkeypatch.setattr(pipeline, "_STALLED_BATCH", 2)
    unknown = _rio(tx_session, "discovered", None)
    oldest = _rio(tx_session, "discovered", _OLD)
    _rio(tx_session, "discovered", datetime.now(UTC) - timedelta(hours=1))
    tx_session.commit()

    assert pipeline.redispatch_stalled_chain.run() == 2

    assert [rid for _t, rid in dispatched] == [unknown, oldest]


@pytest.mark.integration
def test_quarantined_record_is_never_redispatched(tx_session, spies):
    """A task that exhausted its retries must not be paid for again every 30 min."""
    from brave.core.quarantine import quarantine_poison

    dispatched, _fake = spies
    poisoned = _rio(tx_session, "contacts_found", None)
    healthy = _rio(tx_session, "contacts_found", None)
    quarantine_poison(
        session=tx_session, nascente_id=None, task_name="brave.gather_signals",
        error="boom", payload={"rio_id": poisoned},
    )
    tx_session.commit()

    assert pipeline.redispatch_stalled_chain.run() == 1

    assert dispatched == [("gather_signals_task", healthy)]


@pytest.mark.integration
def test_nascente_rio_depth_leaves_discovered_alone(tx_session, spies):
    dispatched, fake = spies
    collection_engine.set_depth(fake, collection_engine.NASCENTE_RIO)
    _rio(tx_session, "discovered", None)
    contacts = _rio(tx_session, "contacts_found", None)
    tx_session.commit()

    assert pipeline.redispatch_stalled_chain.run() == 1

    assert dispatched == [("gather_signals_task", contacts)]


# -- failed .delay: no inline .run (Q3) ----------------------------------------------


class _Clients:
    places = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _AdvancingAgent:
    def __init__(self, **_k):
        pass

    async def run(self, rio):
        rio.sub_state = "contacts_found"


def test_failed_dispatch_leaves_the_record_and_never_quarantines_the_parent(monkeypatch):
    rio = MagicMock(sub_state="discovered")
    session = MagicMock()
    session.get.return_value = rio
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, None))
    monkeypatch.setattr(pipeline, "_load_config", lambda _s: MagicMock())
    monkeypatch.setattr(pipeline, "clients_for", lambda *_a, **_k: _Clients())
    monkeypatch.setattr(
        "brave.domains.places.contact_finder_agent.ContactFinderAgent", _AdvancingAgent
    )

    def _no_broker(*_a, **_k):
        raise ConnectionError("broker down")

    monkeypatch.setattr(pipeline.gather_signals_task, "delay", _no_broker)
    inline = MagicMock()
    monkeypatch.setattr(pipeline.gather_signals_task, "run", inline)
    quarantine = MagicMock()
    monkeypatch.setattr("brave.core.quarantine.quarantine_poison", quarantine)

    result = pipeline.find_contacts_task.apply(args=(str(uuid.uuid4()),))

    assert result.state == states.SUCCESS
    inline.assert_not_called()
    quarantine.assert_not_called()
    assert rio.sub_state == "contacts_found"  # waits there for redispatch_stalled_chain
