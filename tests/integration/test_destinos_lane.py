"""DLQ validate endpoints integration tests — D-07 and D-08.

Tests:
  - PATCH /api/v1/dlq/{rio_id}/validate with corroboration → routes to 'mar'
  - PATCH /api/v1/dlq/{rio_id}/validate: DB round-trip proves flag_modified persisted
  - PATCH /api/v1/dlq/{rio_id}/validate with zero corroboration → stays 'dlq'
  - PATCH /api/v1/dlq/{rio_id}/validate with non-existent rio_id → 404
  - POST /api/v1/dlq/validate-batch?uf=BA: validates all DLQ records for UF
  - Both endpoints write audit rows with action='dlq_validated' and actor='steward'

NotebookLMIngest corroboration tests (D-02, DEST-02):
  - test_notebooklm_corroboration_boosts_mtur: DB-level proof that calling
    NotebookLMIngest.produce('BA') on a municipality that already has a Mtur RioRecord
    boosts normalized['corroboracao_value'] by >=50 on the surviving record.
    If flag_modified was omitted or the IBGE-match query did not fire, the value
    remains 0.0 and this test fails.

All tests are integration-marked (require docker-compose postgres).
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from brave.core.models import AuditLog, RioRecord


os.environ.setdefault(
    "BRAVE_DB_URL",
    "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
)


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient for validate endpoint tests."""
    from brave.api.main import app
    return TestClient(app, raise_server_exceptions=False)


def _make_dlq_record(db_session: Session, uf: str = "BA", corroboracao: float = 50.0) -> RioRecord:
    """Helper: insert a RioRecord in 'dlq' routing for endpoint tests.

    Sets up normalized dict with all §7.6 criterion values.
    With corroboracao=50 + validacao_humana=100: score will be >=85 → 'mar'.
    With corroboracao=0 + validacao_humana=100: score will be 80.0 → still 'dlq'.
    """
    from brave.core.models import NascenteRecord
    from brave.core.nascente.service import store_raw
    from brave.config.settings import ScoreConfig
    from brave.core.rio.routing import process_nascente_record

    unique_tag = uuid.uuid4().hex[:8]
    source_ref = f"mtur:{uf}:{unique_tag}"

    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf=uf,
        payload={
            "name": f"DLQ Test Dest {unique_tag}",
            "municipio_id": f"292{unique_tag[:4]}",
            "uf": uf,
            "origem_value": 100.0,
            "completude_value": 100.0,
            "corroboracao_value": corroboracao,
            "atualidade_value": 100.0,
            "validacao_humana_value": 0.0,
        },
    )
    db_session.flush()

    config = ScoreConfig()
    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    # Force routing to 'dlq' for the endpoint to act on
    rio.routing = "dlq"
    rio.dlq_reason = "score=below_threshold"
    db_session.flush()
    db_session.commit()
    return rio


# ---------------------------------------------------------------------------
# PATCH /api/v1/dlq/{rio_id}/validate — single record
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_validate_endpoint_404_for_unknown_rio_id(client):
    """PATCH /api/v1/dlq/{rio_id}/validate with unknown id returns 404."""
    unknown_id = str(uuid.uuid4())
    r = client.patch(f"/api/v1/dlq/{unknown_id}/validate")
    assert r.status_code == 404


@pytest.mark.integration
def test_validate_endpoint_returns_202_with_routing(client, db_session):
    """PATCH /api/v1/dlq/{rio_id}/validate returns 202 with status and routing."""
    rio = _make_dlq_record(db_session, corroboracao=50.0)
    r = client.patch(f"/api/v1/dlq/{rio.id}/validate")
    assert r.status_code == 202
    data = r.json()
    assert data["status"] == "accepted"
    assert data["rio_id"] == str(rio.id)
    assert "routing" in data


@pytest.mark.integration
def test_validate_endpoint_promotes_to_mar_with_corroboration(client, db_session):
    """PATCH /api/v1/dlq/{rio_id}/validate with corroboracao=50 promotes to 'mar'.

    DB round-trip proof: the test re-reads rio.normalized['validacao_humana_value']
    from the DB after PATCH and asserts == 100.0. If flag_modified was omitted,
    SQLAlchemy does not track in-place JSON mutations and the read-back returns 0.0.
    This test is the functional proof that the mutation was committed and persisted.
    """
    rio = _make_dlq_record(db_session, corroboracao=50.0)
    rio_id = rio.id

    r = client.patch(f"/api/v1/dlq/{rio_id}/validate")
    assert r.status_code == 202

    # DB round-trip: re-read from a fresh query (not the ORM cache)
    db_session.expire_all()
    updated_rio = db_session.get(RioRecord, rio_id)
    assert updated_rio is not None

    # flag_modified proof: validacao_humana_value must be 100.0 in DB
    assert updated_rio.normalized is not None
    assert updated_rio.normalized.get("validacao_humana_value") == 100.0, (
        f"Expected 100.0 but got {updated_rio.normalized.get('validacao_humana_value')} "
        "— flag_modified was likely omitted (Pitfall 3)"
    )

    # With corroboracao=50 + validacao_humana=100, score >=85 → 'mar'
    assert updated_rio.routing == "mar", (
        f"Expected 'mar' but got '{updated_rio.routing}' "
        "(score={updated_rio.score}) — corroboration + validation should push past 85%"
    )


@pytest.mark.integration
def test_validate_endpoint_stays_dlq_without_corroboration(client, db_session):
    """PATCH /api/v1/dlq/{rio_id}/validate with corroboracao=0 stays in 'dlq'.

    Mtur cold-start: origem=100, completude=100, corroboracao=0, atualidade=100,
    validacao_humana=100 → score = 30+20+0+15+15 = 80.0 < 85 threshold → stays dlq.
    """
    rio = _make_dlq_record(db_session, corroboracao=0.0)
    rio_id = rio.id

    r = client.patch(f"/api/v1/dlq/{rio_id}/validate")
    assert r.status_code == 202

    db_session.expire_all()
    updated_rio = db_session.get(RioRecord, rio_id)
    assert updated_rio is not None

    # validacao_humana_value persisted via flag_modified
    assert updated_rio.normalized is not None
    assert updated_rio.normalized.get("validacao_humana_value") == 100.0

    # But routing stays dlq — 80.0 < 85 threshold
    assert updated_rio.routing == "dlq"


@pytest.mark.integration
def test_validate_endpoint_writes_audit_row(client, db_session):
    """PATCH /api/v1/dlq/{rio_id}/validate writes AuditLog with action='dlq_validated'."""
    from sqlalchemy import select

    rio = _make_dlq_record(db_session, corroboracao=50.0)
    rio_id = rio.id

    r = client.patch(f"/api/v1/dlq/{rio_id}/validate")
    assert r.status_code == 202

    # Find audit log row
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "dlq_validated",
            AuditLog.record_id == rio_id,
        )
    )
    assert audit is not None, "No audit row found with action='dlq_validated'"
    assert audit.actor == "steward"
    assert audit.entity_type == "destination"


# ---------------------------------------------------------------------------
# POST /api/v1/dlq/validate-batch — batch by UF
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_validate_batch_returns_202(client, db_session):
    """POST /api/v1/dlq/validate-batch?uf=BA returns 202 with validated count."""
    # Create 2 DLQ records for BA
    _make_dlq_record(db_session, uf="BA", corroboracao=50.0)
    _make_dlq_record(db_session, uf="BA", corroboracao=50.0)

    r = client.post("/api/v1/dlq/validate-batch?uf=BA&entity_type=destination")
    assert r.status_code == 202
    data = r.json()
    assert data["status"] == "accepted"
    assert data["uf"] == "BA"
    assert isinstance(data["validated"], int)
    assert data["validated"] >= 2


@pytest.mark.integration
def test_validate_batch_writes_audit_row(client, db_session):
    """POST /api/v1/dlq/validate-batch?uf=CE writes audit row with 'dlq_validated'."""
    from sqlalchemy import select

    _make_dlq_record(db_session, uf="CE", corroboracao=50.0)

    r = client.post("/api/v1/dlq/validate-batch?uf=CE&entity_type=destination")
    assert r.status_code == 202

    # At least one dlq_validated audit row exists for CE records
    count = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "dlq_validated")
    )
    assert count is not None, "No audit row found with action='dlq_validated' after batch validate"


@pytest.mark.integration
def test_validate_batch_respects_limit(client, db_session):
    """POST /api/v1/dlq/validate-batch?uf=RJ&limit=1 validates at most 1 record."""
    _make_dlq_record(db_session, uf="RJ", corroboracao=50.0)
    _make_dlq_record(db_session, uf="RJ", corroboracao=50.0)

    r = client.post("/api/v1/dlq/validate-batch?uf=RJ&entity_type=destination&limit=1")
    assert r.status_code == 202
    data = r.json()
    assert data["validated"] <= 1


@pytest.mark.integration
def test_validate_batch_limit_bounds(client):
    """POST /api/v1/dlq/validate-batch with limit=0 or limit=1001 returns 422."""
    r = client.post("/api/v1/dlq/validate-batch?uf=BA&limit=0")
    assert r.status_code == 422

    r = client.post("/api/v1/dlq/validate-batch?uf=BA&limit=1001")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# NotebookLMIngest corroboration boost (D-02, DEST-02, RISK-02)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_notebooklm_corroboration_boosts_mtur(db_session: Session):
    """DB-level proof: NotebookLMIngest.produce('BA') boosts corroboracao_value on a
    pre-existing Mtur RioRecord that shares the same IBGE code.

    Setup:
      1. Ingest a Mtur RioRecord for Porto Seguro (IBGE 2927408) via store_raw +
         process_nascente_record — simulates the post-02-05 state.
      2. Force routing='dlq' so the RioRecord is a live record eligible for boost.

    Action:
      3. Call NotebookLMIngest.produce('BA') with a FakeNotebookLMClient that returns
         a non-empty report for "Porto Seguro:BA:2927408".

    Assertion:
      4. Re-read the Mtur RioRecord from DB (expire_all to bypass ORM cache).
      5. Assert normalized['corroboracao_value'] >= 50.0
         — if flag_modified was omitted or the IBGE-match query did not fire,
         the value remains 0.0 and this test fails.

    This is the load-bearing corroboration test: without it, Mtur records score max
    80.0 after human validation and never cross the Mar threshold (RESEARCH.md Pitfall 2).
    """
    import asyncio

    from brave.config.settings import ScoreConfig
    from brave.core.models import NascenteRecord, RioRecord
    from brave.core.nascente.service import store_raw
    from brave.core.rio.routing import process_nascente_record
    from brave.lanes.destinos.notebooklm import NotebookLMIngest
    from tests.fakes.fake_notebooklm import FakeNotebookLMClient

    uf = "BA"
    ibge_code = "2927408"
    municipio_key = f"Porto Seguro:{uf}:{ibge_code}"

    # Step 1: Seed a Mtur RioRecord for Porto Seguro (simulates 02-05 state)
    unique_tag = uuid.uuid4().hex[:8]
    source_ref = f"mtur:{uf}:{ibge_code}-{unique_tag}"

    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf=uf,
        payload={
            "name": "Porto Seguro",
            "municipio_id": ibge_code,
            "uf": uf,
            "origem_value": 100.0,
            "completude_value": 100.0,
            "corroboracao_value": 0.0,
            "atualidade_value": 70.0,
            "validacao_humana_value": 0.0,
            "canonical": {
                "name": "Porto Seguro",
                "uf": uf,
                "municipio": "Porto Seguro",
                "ibge_code": ibge_code,
            },
        },
    )

    config = ScoreConfig()
    rio = process_nascente_record(db_session, nascente, config)

    # Step 2: Force routing to 'dlq' — NotebookLMIngest queries routing.in_(["dlq","mar"])
    rio.routing = "dlq"
    db_session.flush()
    mtur_rio_id = rio.id

    # Confirm baseline: corroboracao starts at 0
    assert rio.normalized is not None
    assert float(rio.normalized.get("corroboracao_value", 0.0)) == 0.0, (
        "Pre-condition failed: expected corroboracao_value=0.0 before NotebookLM ingest"
    )

    # Step 3: Run NotebookLMIngest.produce('BA') with a fake client that returns
    # a non-empty report for the municipio_key.
    fake_nb = FakeNotebookLMClient(
        reports={
            municipio_key: {
                "name": "Porto Seguro",
                "highlights": ["beaches", "history"],
                "publish_date": "2024-01-15",
            }
        }
    )

    mtur_municipalities = [
        {"ibge_code": ibge_code, "name": "Porto Seguro", "uf": uf}
    ]

    lane = NotebookLMIngest(
        notebooklm_client=fake_nb,
        session=db_session,
        config=config,
        mtur_municipalities=mtur_municipalities,
    )
    asyncio.run(lane.produce(uf))

    # Step 4: Re-read from DB, bypassing ORM cache
    db_session.expire_all()
    updated_rio = db_session.get(RioRecord, mtur_rio_id)
    assert updated_rio is not None

    # Step 5: Assert corroboracao_value was boosted by >= 50
    assert updated_rio.normalized is not None
    actual_corroboracao = float(updated_rio.normalized.get("corroboracao_value", 0.0))
    assert actual_corroboracao >= 50.0, (
        f"Expected corroboracao_value >= 50.0 but got {actual_corroboracao}. "
        "flag_modified was likely omitted OR the IBGE exact-match query did not fire "
        "(check routing.in_(['dlq','mar']) filter and municipio_id match)."
    )
