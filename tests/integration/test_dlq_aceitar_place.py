"""Steward routes over a Google match parked for review.

PATCH /api/v1/dlq/{id}/aceitar-place restores the place_id the Places gate dropped (from the
places_hard_descarte audit), marks it accepted, clears the sticky google_tipo_negocio reason
and re-dispatches the enrichment. PATCH /api/v1/dlq/{id}/liberar-google drops Google's
verdict instead (homonym or wrong listing): the record goes on without Google data.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from brave.core.models import AuditLog, NascenteRecord, RioRecord
from brave.observability.audit import write_audit

os.environ.setdefault(
    "BRAVE_DB_URL",
    "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave_test",
)
DASHBOARD_BEARER = "test-dlq-aceitar-place-bearer"
HEADERS = {"Authorization": f"Bearer {DASHBOARD_BEARER}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", DASHBOARD_BEARER)


@pytest.fixture(autouse=True)
def _cleanup_committed_rows(db_engine):
    with db_engine.connect() as conn:
        before = {r[0] for r in conn.execute(text("SELECT id FROM rio_records"))}
    yield
    with db_engine.begin() as conn:
        new_ids = list({r[0] for r in conn.execute(text("SELECT id FROM rio_records"))} - before)
        if not new_ids:
            return
        conn.execute(text("DELETE FROM audit_log WHERE record_id = ANY(:i)"), {"i": new_ids})
        nasc = [r[0] for r in conn.execute(
            text("SELECT nascente_id FROM rio_records WHERE id = ANY(:i)"), {"i": new_ids}
        )]
        conn.execute(text("DELETE FROM rio_records WHERE id = ANY(:i)"), {"i": new_ids})
        conn.execute(text("DELETE FROM nascente_records WHERE id = ANY(:i)"), {"i": nasc})


@pytest.fixture
def dispatched(monkeypatch):
    from brave.api.routers import dlq

    calls: list[tuple] = []
    monkeypatch.setattr(dlq, "_dispatch_or_inline", lambda task, *a: calls.append((task.name, *a)))
    return calls


def _business_matched_atrativo(db_session, *, audit_reason: str | None) -> RioRecord:
    src = f"tripadvisor:attraction:{uuid.uuid4().hex}"
    nascente = NascenteRecord(
        id=uuid.uuid4(), source="tripadvisor", source_ref=src, entity_type="attraction",
        uf="MA", payload={"name": "Cachoeira Paracupeba"}, content_hash=f"hash:{src}", version=1,
    )
    db_session.add(nascente)
    db_session.flush()
    rio = RioRecord(
        id=uuid.uuid4(), nascente_id=nascente.id, entity_type="attraction", uf="MA",
        routing="dlq", dlq_reason="google_tipo_negocio", canonical_key=src,
        normalized={"name": "Cachoeira Paracupeba", "google_enriched": True,
                    "place_id_cache": None, "google_place_id": None},
    )
    db_session.add(rio)
    db_session.flush()
    if audit_reason:
        write_audit(
            session=db_session, action="places_hard_descarte", entity_type="attraction",
            record_id=rio.id, before_state={"routing": "dlq"},
            after_state={"routing": "dlq", "reason": audit_reason, "place_id": "ChIJparacupeba",
                         "place_name": "Cachoeira Paracupeba",
                         "primary_type": "brazilian_restaurant"},
            actor="places_enrichment_agent",
        )
    db_session.commit()
    return rio


@pytest.mark.integration
def test_accept_restores_the_place_id_and_re_enriches(db_session, dispatched) -> None:
    from brave.api.main import app

    rio = _business_matched_atrativo(db_session, audit_reason="google_tipo_negocio")
    r = TestClient(app).patch(f"/api/v1/dlq/{rio.id}/aceitar-place", headers=HEADERS)

    assert r.status_code == 202, r.text
    assert r.json()["place_id"] == "ChIJparacupeba"
    assert dispatched == [("brave.enrich_places", str(rio.id))]
    db_session.expire_all()
    rio = db_session.get(RioRecord, rio.id)
    assert rio.normalized["place_id_cache"] == "ChIJparacupeba"
    assert rio.normalized["google_tipo_aceito"] == "ChIJparacupeba"
    assert rio.normalized["google_enriched"] is False  # re-opens the Details sub-step
    assert (rio.routing, rio.dlq_reason) == ("dlq", None)
    assert db_session.scalar(select(AuditLog).where(
        AuditLog.record_id == rio.id, AuditLog.action == "dlq_place_accepted"))


@pytest.mark.integration
@pytest.mark.parametrize("audit_reason", [None, "nao_atrativo"])
def test_accept_needs_a_google_tipo_negocio_match(db_session, dispatched, audit_reason) -> None:
    from brave.api.main import app

    rio = _business_matched_atrativo(db_session, audit_reason=audit_reason)
    r = TestClient(app).patch(f"/api/v1/dlq/{rio.id}/aceitar-place", headers=HEADERS)

    assert r.status_code == 422
    assert dispatched == []


def _parked(db_session, reason: str) -> RioRecord:
    rio = _business_matched_atrativo(db_session, audit_reason=None)
    rio.dlq_reason = reason
    rio.normalized = {**rio.normalized, "origem_value": 65.0, "completude_value": 90.0}
    db_session.commit()
    return rio


@pytest.mark.integration
@pytest.mark.parametrize(
    "reason", ["google_fechado_revisao", "google_tipo_negocio", "categoria_revisao"]
)
def test_release_drops_the_google_verdict_and_re_scores(db_session, reason) -> None:
    from brave.api.main import app

    rio = _parked(db_session, reason)
    r = TestClient(app).patch(f"/api/v1/dlq/{rio.id}/liberar-google", headers=HEADERS)

    assert r.status_code == 200, r.text
    db_session.expire_all()
    rio = db_session.get(RioRecord, rio.id)
    assert rio.routing == "dlq" and rio.dlq_reason.startswith("score=")
    assert rio.score is not None
    # Released as an atrativo: the category gate must not park it again on the next pass.
    assert bool(rio.normalized.get("categoria_liberada")) is (reason == "categoria_revisao")
    action = "dlq_categoria_released" if reason == "categoria_revisao" else "dlq_google_released"
    assert db_session.scalar(select(AuditLog).where(
        AuditLog.record_id == rio.id, AuditLog.action == action))


@pytest.mark.integration
def test_release_needs_a_google_review_reason(db_session) -> None:
    from brave.api.main import app

    rio = _parked(db_session, "no_recent_reviews")
    r = TestClient(app).patch(f"/api/v1/dlq/{rio.id}/liberar-google", headers=HEADERS)
    assert r.status_code == 422
