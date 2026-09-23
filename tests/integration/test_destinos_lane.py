"""DLQ validate endpoints integration tests — D-07 and D-08.

Tests:
  - PATCH /api/v1/dlq/{rio_id}/validate with corroboration → routes to 'mar'
  - PATCH /api/v1/dlq/{rio_id}/validate: DB round-trip proves flag_modified persisted
  - PATCH /api/v1/dlq/{rio_id}/validate with zero corroboration → stays 'dlq'
  - PATCH /api/v1/dlq/{rio_id}/validate with non-existent rio_id → 404
  - POST /api/v1/dlq/validate-batch?uf=BA: validates all DLQ records for UF
  - Both endpoints write audit rows with action='dlq_validated' and actor='steward'

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


# Steward secret for the mutating DLQ endpoints (T-02-06-01 / CR-01).
STEWARD_SECRET = "test-steward-secret"
os.environ.setdefault("BRAVE_STEWARD_SECRET", STEWARD_SECRET)


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient that authenticates as a steward by default.

    Sends X-Steward-Secret on every request so the existing business-logic tests
    exercise the validate/validate-batch endpoints. Auth itself is covered
    separately by test_validate_requires_steward_secret (bare client).
    """
    from brave.api.main import app

    os.environ["BRAVE_STEWARD_SECRET"] = STEWARD_SECRET
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={"X-Steward-Secret": STEWARD_SECRET},
    )


def _make_dlq_record(db_session: Session, uf: str = "BA", corroboracao: float = 50.0) -> RioRecord:
    """Helper: insert a RioRecord in 'dlq' routing for endpoint tests.

    Sets up normalized dict with all reliability criterion values.
    With corroboracao=50 + validacao_humana=100: score will be >=85 → 'mar'.
    With corroboracao=0 + validacao_humana=100: score will be 80.0 → still 'dlq'.
    """
    from brave.config.settings import ScoreConfig
    from brave.core.nascente.service import store_raw
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


@pytest.fixture(autouse=True)
def enqueued():
    """Route brave.publish_mar enqueues into a list — no test here needs a real broker."""
    from brave.api.deps import get_publish_enqueue
    from brave.api.main import app

    sent: list[str] = []
    app.dependency_overrides[get_publish_enqueue] = lambda: sent.append
    try:
        yield sent
    finally:
        app.dependency_overrides.pop(get_publish_enqueue, None)


def _broker_down():
    """Override get_publish_enqueue with a dispatcher whose broker is unreachable."""
    from brave.api.deps import get_publish_enqueue
    from brave.api.main import app

    def _raise(_rid: str) -> None:
        raise RuntimeError("broker unreachable (simulated)")

    app.dependency_overrides[get_publish_enqueue] = lambda: _raise


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
def test_validate_endpoint_promotes_at_threshold_without_corroboration(client, db_session):
    """PATCH /api/v1/dlq/{rio_id}/validate with corroboracao=0 reaches 'mar' at the binary threshold.

    Mtur cold-start: origem=100, completude=100, corroboracao=0, atualidade=100,
    validacao_humana=100 → score = 30+20+0+15+15 = 80.0. Under the binary gate
    (≥80 → Mar) this record now promotes to Mar even without corroboration —
    the old three-band 85 threshold that held it in dlq is gone.
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

    # Binary gate: score 80.0 ≥ threshold_mar (80.0) → mar
    assert updated_rio.routing == "mar"


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
    count = db_session.scalar(select(AuditLog).where(AuditLog.action == "dlq_validated"))
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
# Broker-down on Mar push — surface (503 + rollback), never silently drop.
# Mirrors the atrativos_gate outreach-dispatch contract: under
# run_real_externals a failed push must NOT leave a record promoted-to-Mar-but-
# unpublished with no log. Offline (default) the failure is an expected no-op.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_validate_broker_down_returns_202_pending(client, db_session):
    """A broker outage never fails the promote: 202, push_queued=False, row pending.

    WR-01: the promotion + audit are committed before the enqueue, so the record IS
    in Mar with pushed_at NULL — the outbox (beat + Painel Reenviar) publishes it.
    """
    from sqlalchemy import select

    from brave.core.models import MarRecord

    rio = _make_dlq_record(db_session, corroboracao=50.0)
    rio_id = rio.id
    _broker_down()

    r = client.patch(f"/api/v1/dlq/{rio_id}/validate")
    assert r.status_code == 202
    assert r.json()["push_queued"] is False

    db_session.expire_all()
    reloaded = db_session.get(RioRecord, rio_id)
    assert reloaded is not None and reloaded.routing == "mar"
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "dlq_validated", AuditLog.record_id == rio_id
        )
    )
    assert audit is not None, "dlq_validated audit row must persist (WR-01)"
    mar = db_session.scalar(
        select(MarRecord).where(
            MarRecord.rio_id == rio_id, MarRecord.superseded_by_id.is_(None)
        )
    )
    assert mar is not None and mar.pushed_at is None


@pytest.mark.integration
def test_validate_enqueues_publish_after_commit(client, db_session, enqueued):
    """A successful promote enqueues brave.publish_mar with the rio id → 202, queued."""
    rio = _make_dlq_record(db_session, corroboracao=50.0)

    r = client.patch(f"/api/v1/dlq/{rio.id}/validate")
    assert r.status_code == 202
    assert r.json()["push_queued"] is True
    assert enqueued == [str(rio.id)]


@pytest.mark.integration
def test_validate_batch_broker_down_returns_202_and_validates_every_row(client, db_session):
    """Broker down mid-batch: 202, every row is validated and committed to Mar (pending)."""
    from sqlalchemy import update

    test_uf = "PE"

    # Clean up accumulated PE dlq rows from prior test runs so the batch processes
    # exactly our two new records.
    db_session.execute(
        update(RioRecord)
        .where(
            RioRecord.uf == test_uf,
            RioRecord.routing == "dlq",
            RioRecord.entity_type == "destination",
        )
        .values(routing="descarte", dlq_reason="test_cleanup")
    )
    db_session.commit()

    rio_a = _make_dlq_record(db_session, uf=test_uf, corroboracao=50.0)
    rio_b = _make_dlq_record(db_session, uf=test_uf, corroboracao=50.0)
    _broker_down()

    r = client.post(f"/api/v1/dlq/validate-batch?uf={test_uf}&entity_type=destination")
    assert r.status_code == 202
    assert r.json()["validated"] == 2

    db_session.expire_all()
    assert db_session.get(RioRecord, rio_a.id).routing == "mar"
    assert db_session.get(RioRecord, rio_b.id).routing == "mar"


# ---------------------------------------------------------------------------
# Steward authentication on mutating DLQ endpoints (T-02-06-01 / CR-01)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_validate_requires_steward_secret(db_session: Session):
    """Mutating DLQ endpoints reject callers without a valid X-Steward-Secret.

    The validate / validate-batch / reprocess / descarte endpoints promote records
    to Mar and push to the production norteia-api — a write-to-production trust
    boundary. Without the steward secret they must return 401 BEFORE any mutation.
    """
    from brave.api.main import app

    os.environ["BRAVE_STEWARD_SECRET"] = STEWARD_SECRET
    rio = _make_dlq_record(db_session, uf="BA", corroboracao=50.0)

    # Bare client — no default auth header.
    bare = TestClient(app, raise_server_exceptions=False)

    # Missing header → 401, record stays in DLQ.
    r = bare.patch(f"/api/v1/dlq/{rio.id}/validate")
    assert r.status_code == 401, f"expected 401 without steward secret, got {r.status_code}"

    # Wrong secret → 401.
    r = bare.patch(
        f"/api/v1/dlq/{rio.id}/validate",
        headers={"X-Steward-Secret": "wrong-secret"},
    )
    assert r.status_code == 401, f"expected 401 with wrong secret, got {r.status_code}"

    # validate-batch and reprocess and descarte are likewise guarded.
    r = bare.post("/api/v1/dlq/validate-batch?uf=BA&entity_type=destination")
    assert r.status_code == 401
    r = bare.patch(f"/api/v1/dlq/{rio.id}/reprocess")
    assert r.status_code == 401
    r = bare.patch(f"/api/v1/dlq/{rio.id}/descarte")
    assert r.status_code == 401

    # Correct secret → not 401 (record still in DLQ, untouched by the rejected calls).
    db_session.expire_all()
    fresh = db_session.get(RioRecord, rio.id)
    assert fresh.routing == "dlq", "record must not have mutated on rejected calls"

    r = bare.patch(
        f"/api/v1/dlq/{rio.id}/validate",
        headers={"X-Steward-Secret": STEWARD_SECRET},
    )
    assert r.status_code == 202, f"expected 202 with valid steward secret, got {r.status_code}"
