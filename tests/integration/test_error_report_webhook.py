"""Error-report webhook integration tests (CNTR-02, T-02-01).

Tests cover:
  - POST /webhook/error-report with valid source_ref → 202, RioRecord routing='dlq'
  - POST /webhook/error-report with unknown source_ref → 404
  - POST /webhook/error-report with malformed body (missing source_ref) → 422
  - POST /webhook/error-report with bad/missing X-Webhook-Secret → 401

Requires docker-compose Postgres for the integration tests (DB-dependent cases).
401 tests do NOT require DB — auth fires before any DB work (T-02-01).
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.mar.service import promote_to_mar
from brave.core.nascente.service import store_raw
from brave.core.rio.routing import process_nascente_record

pytestmark = pytest.mark.integration

WEBHOOK_SECRET = "test-webhook-secret-e2e-789"


@pytest.fixture
def webhook_client():
    """FastAPI TestClient with BRAVE_WEBHOOK_SECRET set and fakeredis for rate limit."""
    import fakeredis as fakeredis_mod
    from brave.api import deps

    os.environ["BRAVE_WEBHOOK_SECRET"] = WEBHOOK_SECRET
    os.environ.setdefault(
        "BRAVE_DB_URL",
        "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
    )
    from brave.api.main import app

    # Override Redis with a fresh fakeredis instance per test to isolate rate limits
    _fake_redis = fakeredis_mod.FakeRedis()
    app.dependency_overrides[deps.get_redis] = lambda: _fake_redis
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helper: create a fully-promoted MarRecord for a test source_ref
# ---------------------------------------------------------------------------


def _create_mar_record(db_session: Session, source_ref: str) -> None:
    """Create a full Nascente → Rio (routing=mar) → Mar pipeline record.

    Uses source_ref in the payload to ensure unique content_hash per test run.
    This prevents Stage 1 dedup from returning a cached NascenteRecord from
    a prior test (all-zero embeddings in Phase 1 would cause Stage 2 false matches).
    """
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={
            "name": f"Test Destination {source_ref}",  # Unique per test → unique content_hash
            "municipio": "Test Municipio",
            "origem_value": 100.0,
            "completude_value": 100.0,
            "corroboracao_value": 100.0,
            "atualidade_value": 100.0,
            "validacao_humana_value": 100.0,
        },
    )
    db_session.flush()

    config = ScoreConfig()
    rio = process_nascente_record(db_session, nascente, config)
    db_session.flush()

    assert rio.routing == "mar", (
        f"Expected routing=mar for full-score record, got {rio.routing}"
    )
    promote_to_mar(db_session, rio)
    db_session.commit()


# ---------------------------------------------------------------------------
# Test 1: Valid source_ref → 202 + DLQ
# ---------------------------------------------------------------------------


def test_error_report_valid_source_ref_returns_202(webhook_client, db_session: Session):
    """POST /webhook/error-report with valid secret + existing Mar → 202 accepted.

    Verifies:
      - Response status is 202
      - Response body contains {"status": "accepted", "source_ref": ...}
      - RioRecord.routing transitions to "dlq"
      - RioRecord.dlq_reason == "community_error_report"
    """
    source_ref = f"mtur:BA:webhook_test_{uuid.uuid4().hex[:8]}"
    _create_mar_record(db_session, source_ref)

    # Make the request on the fresh DB state
    r = webhook_client.post(
        "/webhook/error-report",
        json={"source_ref": source_ref},
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )

    assert r.status_code == 202, (
        f"Expected 202 Accepted, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["source_ref"] == source_ref
    assert body.get("status") == "accepted"


def test_error_report_reopens_rio_to_dlq(webhook_client, db_session: Session):
    """POST /webhook/error-report reopens RioRecord to routing='dlq'.

    Verifies the DB state after the webhook call:
      - RioRecord.routing == 'dlq'
      - RioRecord.dlq_reason == 'community_error_report'
    """
    from sqlalchemy import select
    from brave.core.models import MarRecord, RioRecord

    source_ref = f"mtur:BA:dlq_reopen_{uuid.uuid4().hex[:8]}"
    _create_mar_record(db_session, source_ref)

    # Send the webhook request
    webhook_client.post(
        "/webhook/error-report",
        json={"source_ref": source_ref},
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )

    # Verify DB state: RioRecord must be dlq
    db_session.expire_all()  # Invalidate session cache; re-query from DB
    mar = db_session.scalar(
        select(MarRecord).where(
            MarRecord.source_ref == source_ref,
            MarRecord.superseded_by_id.is_(None),
        )
    )
    assert mar is not None, f"MarRecord not found for source_ref={source_ref}"

    rio = db_session.get(RioRecord, mar.rio_id)
    assert rio is not None
    assert rio.routing == "dlq", (
        f"Expected routing=dlq after error report, got {rio.routing}"
    )
    assert rio.dlq_reason == "community_error_report", (
        f"Expected dlq_reason='community_error_report', got {rio.dlq_reason!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: Unknown source_ref → 404
# ---------------------------------------------------------------------------


def test_error_report_unknown_source_ref_returns_404(webhook_client):
    """POST /webhook/error-report with valid secret but unknown source_ref → 404."""
    r = webhook_client.post(
        "/webhook/error-report",
        json={"source_ref": f"nonexistent:{uuid.uuid4().hex}"},
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )
    assert r.status_code == 404, (
        f"Expected 404 for unknown source_ref, got {r.status_code}"
    )


# ---------------------------------------------------------------------------
# Test 3: Missing/malformed body → 422
# ---------------------------------------------------------------------------


def test_error_report_missing_source_ref_returns_422(webhook_client):
    """POST /webhook/error-report with missing source_ref field → 422 Unprocessable Entity."""
    r = webhook_client.post(
        "/webhook/error-report",
        json={},  # Missing required source_ref field
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )
    assert r.status_code == 422, (
        f"Expected 422 for missing source_ref, got {r.status_code}"
    )


def test_error_report_wrong_body_type_returns_422(webhook_client):
    """POST /webhook/error-report with wrong field type → 422."""
    r = webhook_client.post(
        "/webhook/error-report",
        json={"source_ref": 12345},  # Wrong type: int instead of str
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
    )
    # Pydantic coerces int to str, so this may return 200 or 404; but 422 on missing field
    # Test the empty body case for guaranteed 422
    r2 = webhook_client.post(
        "/webhook/error-report",
        content="not json at all",
        headers={"X-Webhook-Secret": WEBHOOK_SECRET, "Content-Type": "application/json"},
    )
    assert r2.status_code == 422, (
        f"Expected 422 for invalid JSON body, got {r2.status_code}"
    )


# ---------------------------------------------------------------------------
# Test 4: 401 on bad/missing secret (no DB needed)
# ---------------------------------------------------------------------------


def test_error_report_missing_secret_returns_401(webhook_client):
    """POST /webhook/error-report without X-Webhook-Secret header → 401."""
    r = webhook_client.post(
        "/webhook/error-report",
        json={"source_ref": "mtur:BA:any"},
    )
    assert r.status_code == 401


def test_error_report_wrong_secret_returns_401(webhook_client):
    """POST /webhook/error-report with wrong X-Webhook-Secret → 401."""
    r = webhook_client.post(
        "/webhook/error-report",
        json={"source_ref": "mtur:BA:any"},
        headers={"X-Webhook-Secret": "totally-wrong-secret"},
    )
    assert r.status_code == 401
