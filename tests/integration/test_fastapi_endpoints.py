"""FastAPI endpoint tests — health, metrics, DLQ, audit, webhook.

These tests use FastAPI TestClient with a real DB (docker-compose).
The webhook tests are critical: 401 on bad secret, 202 on valid secret.

Mark: @pytest.mark.integration for DB-dependent tests.
Webhook 401 test does NOT need DB (tested before any DB work).
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient for endpoint tests."""
    # Set env var for TestClient (no DB needed for pure unit endpoint tests)
    os.environ.setdefault("BRAVE_DB_URL", "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave")
    from brave.api.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def webhook_secret():
    """Set and return a test webhook secret."""
    secret = "test-secret-brave-123"
    os.environ["BRAVE_WEBHOOK_SECRET"] = secret
    return secret


# ---------------------------------------------------------------------------
# Health endpoint (no DB required — uses fakeredis fallback)
# ---------------------------------------------------------------------------


def test_health_returns_200(client):
    """GET /api/v1/health returns 200."""
    r = client.get("/api/v1/health")
    assert r.status_code == 200


def test_health_returns_status_ok(client):
    """GET /api/v1/health body contains status='ok'."""
    r = client.get("/api/v1/health")
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Webhook security tests (401 on bad/missing secret)
# These tests do NOT require DB — auth gate fires before any DB work.
# ---------------------------------------------------------------------------


def test_webhook_missing_secret_returns_401(client, webhook_secret):
    """POST /webhook/error-report without X-Webhook-Secret returns 401."""
    r = client.post(
        "/webhook/error-report",
        json={"source_ref": "mtur:BA:1"},
        # No X-Webhook-Secret header
    )
    assert r.status_code == 401


def test_webhook_wrong_secret_returns_401(client, webhook_secret):
    """POST /webhook/error-report with wrong X-Webhook-Secret returns 401."""
    r = client.post(
        "/webhook/error-report",
        json={"source_ref": "mtur:BA:1"},
        headers={"X-Webhook-Secret": "wrong-secret-value"},
    )
    assert r.status_code == 401


def test_webhook_401_does_not_touch_db(client, webhook_secret):
    """POST /webhook/error-report with bad secret returns 401 BEFORE any DB work.

    Verifying that the auth gate is truly pre-DB: even a non-existent source_ref
    with a bad secret returns 401 (not 404 or 500), proving the check fires first.
    """
    r = client.post(
        "/webhook/error-report",
        json={"source_ref": "nonexistent:source:ref"},
        headers={"X-Webhook-Secret": "definitely-wrong"},
    )
    assert r.status_code == 401


@pytest.mark.integration
def test_webhook_valid_secret_returns_404_no_mar(client, webhook_secret, db_session):
    """POST /webhook/error-report with valid secret but no Mar record returns 404.

    This confirms: auth passes, then source_ref lookup returns 404 (not 500).
    """
    # Reset webhook config to use the test secret
    from brave.config.settings import WebhookConfig
    import brave.api.routers.webhook as webhook_module

    r = client.post(
        "/webhook/error-report",
        json={"source_ref": f"nonexistent:{uuid.uuid4().hex}"},
        headers={"X-Webhook-Secret": webhook_secret},
    )
    # 404 because source_ref doesn't exist in Mar
    assert r.status_code == 404


@pytest.mark.integration
def test_webhook_valid_secret_reopens_mar_record(client, webhook_secret, db_session):
    """POST /webhook/error-report with valid secret + existing Mar → 202 + DLQ.

    Full integration test: create Nascente → Rio (mar) → Mar → error report → DLQ.
    """
    from brave.config.settings import ScoreConfig
    from brave.core.mar.service import promote_to_mar
    from brave.core.nascente.service import store_raw
    from brave.core.rio.routing import process_nascente_record

    source_ref = f"mtur:BA:{uuid.uuid4().hex[:8]}"

    # Create a record that routes to Mar
    # Include source_ref in payload to ensure unique content_hash per test run.
    # Phase 1 zero-vector embeddings mean Stage 1 dedup (content_hash) must be unique
    # to avoid returning a cached RioRecord with stale routing.
    nascente = store_raw(
        session=db_session,
        source="mtur",
        source_ref=source_ref,
        entity_type="destination",
        uf="BA",
        payload={
            "name": f"Trancoso Test {source_ref}",  # unique per test run
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

    assert rio.routing == "mar"
    promote_to_mar(db_session, rio)
    db_session.commit()

    # Now send error report
    r = client.post(
        "/webhook/error-report",
        json={"source_ref": source_ref},
        headers={"X-Webhook-Secret": webhook_secret},
    )
    assert r.status_code == 202
    assert r.json()["source_ref"] == source_ref

    # Verify RioRecord is now in DLQ
    db_session.refresh(rio)
    assert rio.routing == "dlq"
    assert rio.dlq_reason == "community_error_report"


# ---------------------------------------------------------------------------
# Metrics endpoint
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_metrics_returns_required_keys(client, db_session):
    """GET /api/v1/metrics returns JSON with nascente_count, rio_count, mar_count."""
    r = client.get("/api/v1/metrics")
    assert r.status_code == 200
    data = r.json()
    assert "nascente_count" in data
    assert "rio_count" in data
    assert "mar_count" in data
    assert "in_progress" in data["rio_count"]
    assert "mar" in data["rio_count"]
    assert "dlq" in data["rio_count"]
    assert "descarte" in data["rio_count"]


# ---------------------------------------------------------------------------
# DLQ endpoint
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dlq_returns_list(client, db_session):
    """GET /api/v1/dlq returns a list (possibly empty)."""
    r = client.get("/api/v1/dlq")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
