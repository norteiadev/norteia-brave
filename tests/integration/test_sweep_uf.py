"""Offline integration tests for the brave.sweep_uf Destinos sweep task (ORCH-01, ORCH-04, D-06).

The sweep runs the single destino producer:
  - MturSeedIngest.produce(uf) — idempotent seed re-ingest (origem=100)

All tests run 100% offline + keyless:
  - The Mtur side uses the bundled data/mtur/municipios_mtur_2024.csv via the real
    MturClient (offline CSV reader — no network).

Requires: docker-compose postgres up + BRAVE_DB_URL set (load .env before running).
Marked @pytest.mark.integration — skipped when DB unavailable.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from brave.core.models import NascenteRecord, PoisonQuarantine, RioRecord


@pytest.fixture
def isolated_session(db_engine):
    """A session bound to an outer transaction + SAVEPOINT, fully rolled back after the test.

    sweep_uf calls session.commit() internally, which would persist rows to the shared
    docker-compose DB and leak across tests if we relied on the plain db_session fixture
    (rollback-after-commit is a no-op). Here we open an explicit connection + outer
    transaction and run the session in "create_savepoint" join mode, so the inner
    commit() only releases a SAVEPOINT; the outer transaction.rollback() at teardown
    discards everything. This keeps the offline suite side-effect-free.
    """
    connection = db_engine.connect()
    trans = connection.begin()
    session_factory = sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint"
    )
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def test_sweep_uf_name_resolves():
    """The beat entry sweep-{uf}-daily → 'brave.sweep_uf' resolves to a real task [D-01].

    Pure import-time assertion — no DB needed. Guards against the phantom-task regression.
    """
    from brave.tasks.pipeline import sweep_uf

    assert sweep_uf.name == "brave.sweep_uf"


@pytest.mark.integration
def test_sweep_uf_ingests_destinos(isolated_session, monkeypatch):
    """sweep_uf("BA") creates destination RioRecords routed by reliability score (producer-only) [D-01/D-02].

    Drives the real offline path: MturClient reads the bundled CSV. Asserts the Mtur
    seed produced destination Rio rows for BA (origem=100).
    """
    from brave.tasks import pipeline

    # Make sweep_uf use this test's transactional session (so rollback cleans up).
    monkeypatch.setattr(pipeline, "_get_session", lambda: (isolated_session, _NoDispose()))

    pipeline.sweep_uf.run("BA")

    rios = list(
        isolated_session.scalars(
            select(RioRecord).where(
                RioRecord.entity_type == "destination",
                RioRecord.uf == "BA",
            )
        ).all()
    )
    assert len(rios) > 0, "sweep_uf must produce destination RioRecords for BA"
    # Producer-only: no auto-validation — validacao_humana stays 0 (D-02).
    for rio in rios:
        assert rio.routing in ("mar", "dlq"), (
            f"unexpected routing {rio.routing!r} — reliability score routes, sweep adds no branch"
        )


@pytest.mark.integration
def test_sweep_uf_idempotent(isolated_session, monkeypatch):
    """Running sweep_uf twice for the same UF adds no duplicate Nascente rows [D-01, ORCH-01].

    store_raw dedups by (source, source_ref, content_hash) so a replayed sweep is a no-op.
    """
    from brave.tasks import pipeline

    monkeypatch.setattr(pipeline, "_get_session", lambda: (isolated_session, _NoDispose()))

    pipeline.sweep_uf.run("BA")
    count_after_first = isolated_session.scalar(
        select(func.count()).select_from(NascenteRecord).where(NascenteRecord.uf == "BA")
    )

    pipeline.sweep_uf.run("BA")
    count_after_second = isolated_session.scalar(
        select(func.count()).select_from(NascenteRecord).where(NascenteRecord.uf == "BA")
    )

    assert count_after_first > 0, "first sweep must ingest at least one Nascente row"
    assert count_after_second == count_after_first, (
        "second sweep must be a no-op (store_raw dedup) — no duplicate Nascente rows"
    )


@pytest.mark.integration
def test_sweep_uf_quarantines_poison(isolated_session, monkeypatch):
    """A producer that raises lands a PoisonQuarantine row, not a lost record [D-01, T-05-03].

    A missing Mtur CSV raises FileNotFoundError inside MturSeedIngest.produce; the
    quarantine wrapper routes it to PoisonQuarantine with task_name="brave.sweep_uf".
    """
    from brave.tasks import pipeline

    monkeypatch.setattr(pipeline, "_get_session", lambda: (isolated_session, _NoDispose()))

    # Force the Mtur CSV reader to raise so the producer fails permanently.
    def _boom() -> None:
        raise FileNotFoundError("no Mtur seed CSV (simulated poison)")

    monkeypatch.setattr("brave.clients.mtur._load_csv", _boom)

    poison_uf = f"Z{uuid.uuid4().hex[:1].upper()}"  # unlikely-real UF code
    pipeline.sweep_uf.run(poison_uf)

    rows = list(
        isolated_session.scalars(
            select(PoisonQuarantine).where(
                PoisonQuarantine.task_name == "brave.sweep_uf",
            )
        ).all()
    )
    assert len(rows) >= 1, "poison producer failure must write a PoisonQuarantine row"


@pytest.mark.integration
def test_sweep_uf_bad_record_doesnt_discard_good_ones(isolated_session, monkeypatch):
    """A single bad process_nascente_record call is quarantined; other records survive.

    Verifies the per-record SAVEPOINT fix in MturSeedIngest.produce (pfr #2A):
    before the fix, one RuntimeError inside process_nascente_record propagated out of
    produce() and rolled back ALL previously written Mtur records. After the fix,
    only the failing record's SAVEPOINT is rolled back; the outer transaction retains
    all good records plus the quarantine row for the bad one.

    Strategy: patch process_nascente_record at the mtur.py import site to raise on
    the FIRST call only; subsequent calls delegate to the real function. Then run a
    BA sweep and assert:
      1. At least one RioRecord exists for BA (good records committed).
      2. At least one PoisonQuarantine row exists with task_name='brave.sweep_uf'
         (the bad record's savepoint was rolled back and replaced by a quarantine entry).
    """
    from sqlalchemy import select

    from brave.core.models import PoisonQuarantine, RioRecord
    from brave.core.rio.routing import process_nascente_record as _real_fn
    from brave.tasks import pipeline

    monkeypatch.setattr(pipeline, "_get_session", lambda: (isolated_session, _NoDispose()))

    call_count = [0]

    def _fail_first(session, nascente, config):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("simulated poison: bad record (pfr #2A test)")
        return _real_fn(session, nascente, config)

    monkeypatch.setattr("brave.lanes.destinos.mtur.process_nascente_record", _fail_first)

    pipeline.sweep_uf.run("BA")

    # Good records must survive (the savepoint rollback was per-record, not the whole UF).
    rios = list(
        isolated_session.scalars(
            select(RioRecord).where(
                RioRecord.uf == "BA",
                RioRecord.entity_type == "destination",
            )
        ).all()
    )
    assert len(rios) > 0, (
        "good Mtur records must survive even when one record's process_nascente_record raises "
        "(per-record SAVEPOINT isolation — pfr #2A)"
    )

    # The bad record must be quarantined, not silently lost.
    quarantines = list(
        isolated_session.scalars(
            select(PoisonQuarantine).where(
                PoisonQuarantine.task_name == "brave.sweep_uf",
            )
        ).all()
    )
    assert len(quarantines) >= 1, (
        "the failing record must produce a PoisonQuarantine row with task_name='brave.sweep_uf' "
        "(quarantine_poison called after sp.rollback in the per-record except block)"
    )


class _NoDispose:
    """Stand-in engine whose dispose() is a no-op (the test owns the session lifecycle)."""

    def dispose(self) -> None:  # pragma: no cover - trivial
        pass
