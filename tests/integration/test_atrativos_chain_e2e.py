"""Offline end-to-end test for the Atrativos FSM auto-advance chain (ORCH-02, ORCH-04).

Proves the load-bearing promise of Phase 5: discovery automatically drives a record
through the sub_state FSM to the human WhatsApp gate — and STOPS there. No auto-send.

The chain (all keyed on sub_state queries, D-03):
  discover_atrativo_task("BA")
    → produce() seeds Rio at sub_state='discovered' (finding #1, Plan 05-02 Task 1)
    → fan out find_contacts_task per discovered record
      → advances discovered → contacts_found, enqueues gather_signals_task
        → advances contacts_found → signals_gathered, runs §7.6
        → borderline (<85%) lands sub_state='aguardando_consulta_whatsapp' and STOPS

What this suite asserts:
  - test_chain_advances_to_gate : the full chain reaches aguardando_consulta_whatsapp.
  - test_chain_stops_at_gate    : the record settles at the gate; the auto chain promotes
                                  nothing to Mar and never advances past the gate.
  - test_no_auto_outreach       : outreach_task is NEVER invoked by the auto chain (D-07);
                                  only atrativos_gate.py:378 (the human approve) may dispatch it.
  - test_replay_is_noop         : re-dispatching find_contacts_task / gather_signals_task on an
                                  already-advanced record is a no-op (inline guards, D-04).

100% offline + keyless (D-06): AppConfig().run_real_externals defaults to False, so the tasks
select FakePlaces/FakeApify/FakeLLM. We patch those fakes at their import sites to inject the
borderline fixtures (mirrors the score math in test_atrativos_lane_e2e.py::test_sc4).

Isolation: the chain tasks call session.commit() internally, so we use the SAVEPOINT-isolated
session pattern established in test_sweep_uf.py — every commit only releases a savepoint and the
outer rollback at teardown discards everything (no leakage into the shared docker-compose DB).

Sync-fallback fidelity: there is no Celery worker in the test, so we force the production
"dispatch .delay, except → run inline" fallback by patching .delay to raise. This exercises the
exact offline path an operator hits when no broker/worker is reachable.

Requires: docker-compose postgres up + BRAVE_DB_URL set (load .env before running).
Marked @pytest.mark.integration — skipped when DB unavailable.

Score math for the borderline fixture (default ScoreConfig: threshold_dlq=40, threshold_mar=85;
weights origem=30%, completude=20%, corroboracao=20%, atualidade=15%, validacao_humana=15%):
  origem=60, completude=75, corroboracao=40 (Apify confirms via IG), atualidade=100 (recent
  review), validacao_humana=0  →  18 + 15 + 8 + 15 + 0 = 56  →  40 ≤ 56 < 85  →  DLQ (the gate).
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from brave.core.models import AuditLog, MarRecord, RioRecord
from brave.core.nascente.service import store_raw
from brave.core.rio.routing import process_nascente_record
from brave.lanes.atrativos.schemas import AtrativoResult

# Synthetic UF + IBGE: the fan-out query keys on (uf, sub_state='discovered'), so a
# distinctive UF makes this test independent of any leaked BA 'discovered' rows in the
# shared docker-compose DB (mirrors the test_sc2 'XX' isolation tactic). The IBGE is
# likewise synthetic so _resolve_parent_destino's source_ref.contains() match cannot
# cross-resolve to a real parent — only the parent this test seeds for ZZ/2999999 matches.
UF = "ZZ"
IBGE = "2999999"
PLACE_ID = "ChIJtest_chain_e2e"
SOURCE_REF = f"places:{UF}:{PLACE_ID}"

# A recent-review, OPERATIONAL, IG-linked place_details fixture: drives the borderline
# score (atualidade=100 from the recent review, corroboracao=40 from Apify confirming the
# IG handle extracted from the instagram.com website).
_PLACE_DETAILS: dict[str, Any] = {
    "place_id": PLACE_ID,
    "business_status": "OPERATIONAL",
    "international_phone_number": "+55 73 99999-0003",
    "website": "https://instagram.com/atrativo_chain_e2e",
    "weekday_text": [
        "Monday: 9:00 AM – 5:00 PM",
        "Sunday: Closed",
    ],
    "reviews": [
        {
            "publishTime": "2026-06-10T12:00:00Z",
            "rating": 5,
            "text": "Lugar incrível, recomendo muito!",
        }
    ],
}


class _NoDispose:
    """Stand-in engine whose dispose() is a no-op (the test owns the session lifecycle)."""

    def dispose(self) -> None:  # pragma: no cover - trivial
        pass


def _purge_uf(engine) -> None:
    """Delete every test row for the synthetic UF (idempotent cleanup).

    The nested chain (discover → find_contacts → gather_signals) opens its own sessions and
    commits FOR REAL — replicating production exactly (each task = a distinct engine/session).
    A SAVEPOINT-on-one-connection scheme is too fragile here (multiple sessions, cross-session
    MVCC visibility), so instead we commit for real and scrub deterministically afterward.
    The UF/IBGE are synthetic (ZZ / 2999999) and used by no other test, so deletion is
    targeted and leaves the shared docker-compose DB exactly as it was found.
    """
    from sqlalchemy import text

    like = f"%:{UF}:%"  # matches mtur:ZZ:... (parent) and places:ZZ:... (attraction) source_refs
    with engine.begin() as conn:
        # audit_log references rio/nascente ids; delete those rows first.
        conn.execute(
            text("DELETE FROM audit_log WHERE record_id IN "
                 "(SELECT id FROM rio_records WHERE uf = :uf)"),
            {"uf": UF},
        )
        # mar_records has no uf column — scrub by source_ref prefix (covers parent + attraction).
        conn.execute(
            text("DELETE FROM mar_records WHERE source_ref LIKE :like"), {"like": like}
        )
        conn.execute(text("DELETE FROM rio_records WHERE uf = :uf"), {"uf": UF})
        conn.execute(text("DELETE FROM nascente_records WHERE uf = :uf"), {"uf": UF})


class _RealDB:
    """A real-engine session factory matching pipeline._get_session()'s contract."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self._factory = sessionmaker(bind=engine)
        # A session for the test body's own seeding.
        self.session = self._factory()

    def get_session(self):
        """Mimic pipeline._get_session(): a fresh real session + no-op engine."""
        return self._factory(), _NoDispose()

    def fresh(self):
        """A brand-new session for post-chain assertions (sees all committed chain writes)."""
        return self._factory()


@pytest.fixture
def isolated_db(db_engine):
    """Real sessions for the nested chain + deterministic UF-scoped cleanup.

    The chain tasks commit for real (production fidelity); a finally block scrubs every row for
    the synthetic UF so nothing leaks into the shared docker-compose DB. _purge_uf also runs
    BEFORE the test, defending against rows left by an earlier interrupted run.
    """
    _purge_uf(db_engine)
    db = _RealDB(db_engine)
    try:
        yield db
    finally:
        db.session.rollback()
        db.session.close()
        _purge_uf(db_engine)


def _seed_parent_destino(session) -> MarRecord:
    """Seed an active parent destino MarRecord so DiscoveryAgent's D-03 lookup succeeds."""
    from brave.config.settings import ScoreConfig
    from brave.core.mar.service import promote_to_mar

    unique = uuid.uuid4().hex[:8]
    source_ref = f"mtur:{UF}:{IBGE}-{unique}"
    config = ScoreConfig()
    nascente = store_raw(
        session=session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf=UF,
        payload={
            "name": f"Praia do Forte {unique}",
            "municipio_id": IBGE,
            "uf": UF,
            "origem_value": 100.0,
            "completude_value": 100.0,
            "corroboracao_value": 50.0,
            "atualidade_value": 100.0,
            "validacao_humana_value": 100.0,
            "canonical": {"ibge_code": IBGE, "name": f"Praia do Forte {unique}", "uf": UF},
        },
    )
    session.flush()
    rio = process_nascente_record(session, nascente, config)
    session.flush()
    rio.routing = "mar"
    session.flush()
    mar = promote_to_mar(session, rio)
    session.flush()
    return mar


def _patch_fakes(monkeypatch) -> None:
    """Inject borderline fixtures into the fakes the chain tasks build internally.

    The tasks construct their own FakePlacesClient()/FakeApifyClient()/FakeLLMClient() with
    no fixtures (run_real_externals=False). We patch each class at its import site so the
    chain produces a borderline (<85%) attraction that lands at the gate.
    """
    from tests.fakes.fake_apify import FakeApifyClient
    from tests.fakes.fake_llm import FakeLLMClient
    from tests.fakes.fake_places import FakePlacesClient

    def _places_factory(*args, **kwargs):
        return FakePlacesClient(
            fixture_results={
                f"atrativos em {UF}": [
                    {
                        "place_id": PLACE_ID,
                        "name": "Atrativo Chain E2E",
                        "formatted_address": "Atrativo Chain E2E, BA",
                        "municipio_ibge": IBGE,
                        "municipio_nome": "Mata de São João",
                    }
                ],
                f"pontos turísticos em {UF}": [],
            },
            fixture_details={PLACE_ID: _PLACE_DETAILS},
        )

    def _llm_factory(*args, **kwargs):
        return FakeLLMClient(
            fixture_result=AtrativoResult(
                nome="Atrativo Chain E2E",
                tipo="praia",
                posicionamento="Praia paradisíaca com águas cristalinas e estrutura completa",
                municipio_nome="Mata de São João",
                municipio_ibge=IBGE,
                uf=UF,
                place_id=PLACE_ID,
            )
        )

    def _apify_factory(*args, **kwargs):
        return FakeApifyClient(
            fixture_data={
                "@atrativo_chain_e2e": {"followers": 5000, "last_post": "2026-06-12"}
            }
        )

    monkeypatch.setattr("brave.clients.null_places.NullPlacesClient", _places_factory)
    monkeypatch.setattr("brave.clients.null_llm.NullLLMClient", _llm_factory)
    monkeypatch.setattr("brave.clients.null_apify.NullApifyClient", _apify_factory)


def _force_inline_fallback(monkeypatch, pipeline) -> None:
    """Force the production dispatch-then-inline fallback (no broker/worker in the test).

    Patching .delay to raise makes discover_atrativo_task/find_contacts_task take their
    `except → .run(...)` branch, advancing the chain synchronously in-process — the exact
    path an operator hits with no reachable broker.
    """
    def _raise(*args, **kwargs):
        raise RuntimeError("no broker in test — force inline .run fallback")

    monkeypatch.setattr(pipeline.find_contacts_task, "delay", _raise)
    monkeypatch.setattr(pipeline.gather_signals_task, "delay", _raise)


def _run_chain(db, monkeypatch):
    """Seed the parent destino + run the full auto chain inline.

    Returns (gate_rio, read_session): read_session is a fresh post-chain session that sees
    every committed write the chain made. Caller uses read_session for all assertions (the
    seeding db.session holds an older MVCC snapshot).
    """
    from brave.tasks import pipeline

    _seed_parent_destino(db.session)
    db.session.commit()  # commit the parent for real so chain-task sessions resolve it (D-03)

    # Each nested chain task opens its OWN real session via _get_session (production fidelity).
    monkeypatch.setattr(pipeline, "_get_session", db.get_session)
    _patch_fakes(monkeypatch)
    _force_inline_fallback(monkeypatch, pipeline)

    pipeline.discover_atrativo_task.run(UF)

    read = db.fresh()
    rio = read.scalar(
        select(RioRecord).where(RioRecord.canonical_key == SOURCE_REF)
    )
    return rio, read


@pytest.mark.integration
def test_chain_advances_to_gate(isolated_db, monkeypatch):
    """The full auto chain drives a borderline attraction to the human WhatsApp gate.

    discovered → contacts_found → signals_gathered → aguardando_consulta_whatsapp.
    """
    rio, read = _run_chain(isolated_db, monkeypatch)

    assert rio is not None, "the chain must create the attraction RioRecord"
    assert rio.sub_state == "aguardando_consulta_whatsapp", (
        f"chain must advance to the gate, got sub_state={rio.sub_state!r} "
        f"(routing={rio.routing!r}, score={rio.score!r})"
    )
    assert rio.routing == "dlq", (
        f"a borderline (<85%) attraction must route dlq, got {rio.routing!r}"
    )

    # Every transition was audited (D-02): discovered, contacts_found, signals_gathered,
    # aguardando_consulta_whatsapp → ≥ 3 sub_state_advanced rows for this record.
    advanced = read.scalars(
        select(AuditLog).where(
            AuditLog.record_id == rio.id,
            AuditLog.action == "sub_state_advanced",
        )
    ).all()
    assert len(advanced) >= 3, (
        f"expected ≥3 sub_state_advanced audit rows across the chain, got {len(advanced)}: "
        f"{[r.after_state for r in advanced]}"
    )


@pytest.mark.integration
def test_chain_stops_at_gate(isolated_db, monkeypatch):
    """The auto chain settles at the gate — it promotes nothing to Mar and goes no further."""
    rio, read = _run_chain(isolated_db, monkeypatch)

    assert rio.sub_state == "aguardando_consulta_whatsapp", (
        "the auto chain must STOP at the gate, never advance to whatsapp_in_progress/validated"
    )

    # No Mar promotion by the auto chain — the attraction stays borderline awaiting the human.
    mar = read.scalar(
        select(MarRecord).where(
            MarRecord.entity_type == "attraction",
            MarRecord.source_ref == SOURCE_REF,
        )
    )
    assert mar is None, "the auto chain must NOT promote a borderline attraction to Mar"


@pytest.mark.integration
def test_no_auto_outreach(isolated_db, monkeypatch):
    """D-07 INVARIANT: the auto chain triggers NO outreach/WhatsApp send.

    outreach_task is dispatched ONLY by the human gate approve (atrativos_gate.py:378).
    Spy on outreach_task.delay AND .run; both must have call_count == 0 after the full chain.
    """
    from brave.tasks import pipeline

    delay_calls: list[tuple] = []
    run_calls: list[tuple] = []
    monkeypatch.setattr(
        pipeline.outreach_task, "delay", lambda *a, **k: delay_calls.append((a, k))
    )
    monkeypatch.setattr(
        pipeline.outreach_task, "run", lambda *a, **k: run_calls.append((a, k))
    )

    rio, _read = _run_chain(isolated_db, monkeypatch)

    assert rio.sub_state == "aguardando_consulta_whatsapp"
    assert len(delay_calls) == 0, (
        f"the auto chain must NOT dispatch outreach_task.delay (D-07), got {len(delay_calls)}"
    )
    assert len(run_calls) == 0, (
        f"the auto chain must NOT run outreach_task inline (D-07), got {len(run_calls)}"
    )


@pytest.mark.integration
def test_replay_is_noop(isolated_db, monkeypatch):
    """D-04: re-dispatching chain tasks on an already-advanced record changes nothing.

    The agents' inline sub_state precondition guards (finding #2) absorb a duplicate dispatch:
    re-running find_contacts_task / gather_signals_task on a record already at the gate must
    leave sub_state unchanged and add no new sub_state_advanced audit rows.
    """
    from brave.tasks import pipeline

    rio, read = _run_chain(isolated_db, monkeypatch)
    assert rio.sub_state == "aguardando_consulta_whatsapp"
    rio_id = rio.id

    advanced_before = read.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.record_id == rio_id,
            AuditLog.action == "sub_state_advanced",
        )
    )

    # Replay both advancing tasks directly on the already-advanced record (own sessions).
    pipeline.find_contacts_task.run(str(rio_id))
    pipeline.gather_signals_task.run(str(rio_id))

    read2 = isolated_db.fresh()
    rio = read2.get(RioRecord, rio_id)
    assert rio.sub_state == "aguardando_consulta_whatsapp", (
        f"replay must be a no-op, sub_state changed to {rio.sub_state!r}"
    )

    advanced_after = read2.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.record_id == rio_id,
            AuditLog.action == "sub_state_advanced",
        )
    )
    assert advanced_after == advanced_before, (
        f"replay must add no sub_state_advanced audit rows "
        f"(before={advanced_before}, after={advanced_after})"
    )
