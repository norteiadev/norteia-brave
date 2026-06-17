"""Offline integration tests for the brave.sweep_uf Destinos sweep task (ORCH-01, ORCH-04, D-06).

The sweep composes two destino producers:
  - MturSeedIngest.produce(uf)      — idempotent seed re-ingest (origem=100)
  - DesmembramentoAgent.produce(uf) — recurring LLM sub-destino discovery (origem=40)

All tests run 100% offline + keyless:
  - The Mtur side uses the bundled data/mtur/municipios_mtur_2024.csv via the real
    MturClient (offline CSV reader — no network).
  - The LLM side uses FakeLLMClient because AppConfig().run_real_externals defaults to False.

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


def _patch_fake_llm(monkeypatch) -> None:
    """Make the FakeLLMClient that sweep_uf builds return a valid DesmembramentoResult.

    sweep_uf constructs its own FakeLLMClient() with no fixture when
    run_real_externals=False, so we patch the class used at the import site to a
    factory that injects a schema-valid extract() result. Keeps the test offline +
    keyless while exercising the real DesmembramentoAgent.produce path.
    """
    from brave.lanes.destinos.schemas import DesmembramentoResult, DestinoItem
    from tests.fakes.fake_llm import FakeLLMClient

    # Porto Seguro is "A"/Oferta Principal in the bundled BA CSV (ibge 2927408).
    result = DesmembramentoResult(
        municipio_ibge="2927408",
        municipio_nome="Porto Seguro",
        destinos=[
            DestinoItem(
                nome="Trancoso",
                tipo="vila",
                posicionamento="Vila histórica com Quadrado e praias",
            ),
        ],
    )

    def _factory(*args, **kwargs):
        return FakeLLMClient(fixture_result=result)

    monkeypatch.setattr("tests.fakes.fake_llm.FakeLLMClient", _factory)


def test_sweep_uf_name_resolves():
    """The beat entry sweep-{uf}-daily → 'brave.sweep_uf' resolves to a real task [D-01].

    Pure import-time assertion — no DB needed. Guards against the phantom-task regression.
    """
    from brave.tasks.pipeline import sweep_uf

    assert sweep_uf.name == "brave.sweep_uf"


@pytest.mark.integration
def test_sweep_uf_ingests_destinos(isolated_session, monkeypatch):
    """sweep_uf("BA") creates destination RioRecords routed by §7.6 (producer-only) [D-01/D-02].

    Drives the real offline path: MturClient reads the bundled CSV, FakeLLMClient is
    selected because run_real_externals defaults to False. Asserts at least the Mtur
    seed produced destination Rio rows for BA (origem=100).
    """
    from brave.tasks import pipeline

    # Make sweep_uf use this test's transactional session (so rollback cleans up).
    monkeypatch.setattr(pipeline, "_get_session", lambda: (isolated_session, _NoDispose()))
    _patch_fake_llm(monkeypatch)

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
        assert rio.routing in ("mar", "dlq", "descarte"), (
            f"unexpected routing {rio.routing!r} — §7.6 routes, sweep adds no branch"
        )


@pytest.mark.integration
def test_sweep_uf_idempotent(isolated_session, monkeypatch):
    """Running sweep_uf twice for the same UF adds no duplicate Nascente rows [D-01, ORCH-01].

    store_raw dedups by (source, source_ref, content_hash) so a replayed sweep is a no-op.
    """
    from brave.tasks import pipeline

    monkeypatch.setattr(pipeline, "_get_session", lambda: (isolated_session, _NoDispose()))
    _patch_fake_llm(monkeypatch)

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
def test_sweep_uf_no_notebooklm(isolated_session, monkeypatch):
    """sweep_uf never produces a source='notebooklm' Nascente row [Deferred].

    NotebookLM is a manual report ingest only — the recurring sweep runs Mtur seed +
    Desmembramento, nothing else.
    """
    from brave.tasks import pipeline

    monkeypatch.setattr(pipeline, "_get_session", lambda: (isolated_session, _NoDispose()))
    _patch_fake_llm(monkeypatch)

    pipeline.sweep_uf.run("BA")

    notebooklm_rows = list(
        isolated_session.scalars(
            select(NascenteRecord).where(NascenteRecord.source == "notebooklm")
        ).all()
    )
    assert notebooklm_rows == [], "sweep_uf must not run NotebookLM (manual ingest only)"


class _NoDispose:
    """Stand-in engine whose dispose() is a no-op (the test owns the session lifecycle)."""

    def dispose(self) -> None:  # pragma: no cover - trivial
        pass
