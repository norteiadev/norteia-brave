"""Integration tests for the WhatsApp gate FastAPI endpoints (03-03, D-06).

Endpoints under test:
  GET  /api/v1/atrativos/gate             — list aguardando_consulta_whatsapp queue
  PATCH /api/v1/atrativos/gate/{rio_id}/approve  — requires X-Steward-Secret, flips sub_state
  PATCH /api/v1/atrativos/gate/{rio_id}/reject   — requires X-Steward-Secret, routes to dlq
  POST  /api/v1/atrativos/whatsapp/quality-rating-webhook  — sets wa:quality_red Redis flag
  POST  /api/v1/atrativos/whatsapp/inbound — dispatches resume_conversation_task by phone lookup

All tests are integration-marked (require docker-compose postgres + Redis fallback via fakeredis).

Security tests (T-03-03-01):
  - PATCH /approve without X-Steward-Secret → 401
  - PATCH /reject without X-Steward-Secret → 401

Business logic tests:
  - GET /gate: lists only records with sub_state=aguardando_consulta_whatsapp + entity_type=attraction
  - PATCH /approve: sets sub_state="whatsapp_in_progress", writes audit row
  - PATCH /reject: sets routing="dlq", sub_state=None
  - POST /quality-rating-webhook: sets wa:quality_red Redis key on RED
  - POST /inbound: dispatches resume task if phone found in consent_log
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from brave.core.models import RioRecord, ConsentLog

os.environ.setdefault(
    "BRAVE_DB_URL",
    "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
)

STEWARD_SECRET = "test-atrativos-gate-steward-secret"
os.environ.setdefault("BRAVE_STEWARD_SECRET", STEWARD_SECRET)

# WR-03: the quality-rating + inbound webhooks are now authenticated with
# X-Webhook-Secret (shared-secret, mirroring require_steward / error-report).
WEBHOOK_SECRET = "test-atrativos-gate-webhook-secret"
os.environ.setdefault("BRAVE_WEBHOOK_SECRET", WEBHOOK_SECRET)
WEBHOOK_HEADERS = {"X-Webhook-Secret": WEBHOOK_SECRET}


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient (no default steward header — auth tests need bare client)."""
    from brave.api.main import app
    os.environ["BRAVE_STEWARD_SECRET"] = STEWARD_SECRET
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def authed_client():
    """FastAPI TestClient with X-Steward-Secret pre-set."""
    from brave.api.main import app
    os.environ["BRAVE_STEWARD_SECRET"] = STEWARD_SECRET
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers={"X-Steward-Secret": STEWARD_SECRET},
    )


def _make_rio_record(
    db_session: Session,
    sub_state: str | None = "aguardando_consulta_whatsapp",
    entity_type: str = "attraction",
    uf: str = "BA",
) -> RioRecord:
    """Helper: insert a RioRecord for gate endpoint tests.

    Creates a minimal RioRecord that looks like an atrativo awaiting gate approval.
    Does NOT go through store_raw/process_nascente_record — we only need a RioRecord
    in the right state, not a full pipeline record.
    """
    from brave.core.models import NascenteRecord

    src_ref = f"places:BA:{uuid.uuid4().hex}"
    nascente = NascenteRecord(
        id=uuid.uuid4(),
        source="places_discovery",
        source_ref=src_ref,
        entity_type=entity_type,
        uf=uf,
        payload={"name": f"Atrativo Teste {src_ref}", "place_id": src_ref},
        content_hash=f"hash:{src_ref}",
        version=1,
    )
    db_session.add(nascente)
    db_session.flush()

    rio = RioRecord(
        id=uuid.uuid4(),
        nascente_id=nascente.id,
        entity_type=entity_type,
        uf=uf,
        routing="in_progress",
        sub_state=sub_state,
        normalized={"window_open": True, "name": f"Atrativo Teste {src_ref}"},
    )
    db_session.add(rio)
    db_session.flush()
    return rio


# ---------------------------------------------------------------------------
# GET /api/v1/atrativos/gate — list queue
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_list_gate_queue_returns_only_awaiting_records(client, db_session: Session) -> None:
    """GET /gate returns only sub_state=aguardando_consulta_whatsapp + entity_type=attraction."""
    # Seed 1 record in the right state
    awaiting = _make_rio_record(db_session, sub_state="aguardando_consulta_whatsapp")
    # Seed 1 record NOT in the gate queue (different sub_state)
    discovered = _make_rio_record(db_session, sub_state="discovered")
    db_session.commit()

    r = client.get("/api/v1/atrativos/gate")
    assert r.status_code == 200, f"Unexpected status: {r.status_code} — {r.text}"

    body = r.json()
    assert isinstance(body, list)

    # awaiting record must be in the list
    ids_in_response = [item["rio_id"] for item in body]
    assert str(awaiting.id) in ids_in_response, (
        f"Expected {awaiting.id} in gate queue, got: {ids_in_response}"
    )
    # discovered record must NOT be in the list
    assert str(discovered.id) not in ids_in_response, (
        f"Record with sub_state=discovered should NOT appear in gate queue"
    )


# ---------------------------------------------------------------------------
# PATCH /api/v1/atrativos/gate/{rio_id}/approve — auth tests
# ---------------------------------------------------------------------------


def test_approve_gate_requires_steward_secret(client) -> None:
    """PATCH /approve without X-Steward-Secret → 401 (T-03-03-01).

    Auth gate fires before any DB work — no DB fixture needed.
    Uses a random UUID to ensure no DB lookup occurs before the 401.
    """
    r = client.patch(f"/api/v1/atrativos/gate/{uuid.uuid4()}/approve")
    assert r.status_code == 401, (
        f"Expected 401 without X-Steward-Secret, got {r.status_code}"
    )


def test_reject_gate_requires_steward_secret(client) -> None:
    """PATCH /reject without X-Steward-Secret → 401 (T-03-03-01).

    Auth gate fires before any DB work — no DB fixture needed.
    Uses a random UUID to ensure no DB lookup occurs before the 401.
    """
    r = client.patch(f"/api/v1/atrativos/gate/{uuid.uuid4()}/reject")
    assert r.status_code == 401, (
        f"Expected 401 without X-Steward-Secret, got {r.status_code}"
    )


# ---------------------------------------------------------------------------
# PATCH /api/v1/atrativos/gate/{rio_id}/approve — business logic
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_approve_gate_advances_sub_state(authed_client, db_session: Session) -> None:
    """PATCH /approve with valid secret → 202, rio.sub_state == 'whatsapp_in_progress'."""
    rio = _make_rio_record(db_session, sub_state="aguardando_consulta_whatsapp")
    db_session.commit()

    r = authed_client.patch(f"/api/v1/atrativos/gate/{rio.id}/approve")
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"

    body = r.json()
    assert body.get("status") == "accepted"
    assert body.get("rio_id") == str(rio.id)

    # Verify DB state
    db_session.refresh(rio)
    assert rio.sub_state == "whatsapp_in_progress", (
        f"rio.sub_state should be 'whatsapp_in_progress', got '{rio.sub_state}'"
    )


@pytest.mark.integration
def test_approve_gate_returns_404_for_missing_record(authed_client) -> None:
    """PATCH /approve for non-existent rio_id → 404."""
    r = authed_client.patch(f"/api/v1/atrativos/gate/{uuid.uuid4()}/approve")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/v1/atrativos/gate/{rio_id}/reject — business logic
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_reject_gate_routes_to_dlq(authed_client, db_session: Session) -> None:
    """PATCH /reject with valid secret → 200, rio.routing == 'dlq', rio.sub_state is None."""
    rio = _make_rio_record(db_session, sub_state="aguardando_consulta_whatsapp")
    db_session.commit()

    r = authed_client.patch(f"/api/v1/atrativos/gate/{rio.id}/reject")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert body.get("status") == "ok"
    assert body.get("routing") == "dlq"

    # Verify DB state
    db_session.refresh(rio)
    assert rio.routing == "dlq", f"routing should be 'dlq', got '{rio.routing}'"
    assert rio.sub_state is None, f"sub_state should be None, got '{rio.sub_state}'"


@pytest.mark.integration
def test_reject_gate_returns_404_for_missing_record(authed_client) -> None:
    """PATCH /reject for non-existent rio_id → 404."""
    r = authed_client.patch(f"/api/v1/atrativos/gate/{uuid.uuid4()}/reject")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/atrativos/whatsapp/quality-rating-webhook
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_quality_rating_webhook_sets_redis_flag(client, db_session: Session) -> None:
    """POST /quality-rating-webhook {"quality_rating": "RED"} → 200, wa:quality_red set."""
    r = client.post(
        "/api/v1/atrativos/whatsapp/quality-rating-webhook",
        json={"quality_rating": "RED"},
        headers=WEBHOOK_HEADERS,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert body.get("status") == "ok"
    assert body.get("rating") == "RED"

    # WR-06: the webhook now injects redis via Depends(get_redis) instead of
    # calling get_redis() inline. With no dependency_override registered, the
    # DI-resolved client IS the get_redis() singleton — so observing the RED
    # flag through get_redis() here proves the flag was written to the SAME
    # Redis the gate reads (the test-override / fail-closed contract).
    from brave.api.deps import get_redis
    from brave.compliance.quality_rating import is_quality_red
    redis = get_redis()
    assert is_quality_red(redis), "wa:quality_red flag should be set after RED webhook"

    # Cleanup: clear the flag so other tests aren't affected
    from brave.compliance.quality_rating import set_quality_flag
    set_quality_flag(redis, "GREEN")


@pytest.mark.integration
def test_quality_rating_webhook_clears_redis_flag_on_green(client, db_session: Session) -> None:
    """POST /quality-rating-webhook {"quality_rating": "GREEN"} → clears wa:quality_red."""
    from brave.api.deps import get_redis
    redis = get_redis()
    redis.set("wa:quality_red", "1")  # pre-seed flag

    r = client.post(
        "/api/v1/atrativos/whatsapp/quality-rating-webhook",
        json={"quality_rating": "GREEN"},
        headers=WEBHOOK_HEADERS,
    )
    assert r.status_code == 200

    from brave.compliance.quality_rating import is_quality_red
    assert not is_quality_red(redis), "wa:quality_red should be cleared after GREEN webhook"


@pytest.mark.integration
def test_quality_rating_webhook_requires_secret(client, db_session: Session) -> None:
    """WR-03: POST /quality-rating-webhook without X-Webhook-Secret → 401."""
    r = client.post(
        "/api/v1/atrativos/whatsapp/quality-rating-webhook",
        json={"quality_rating": "RED"},
    )
    assert r.status_code == 401, (
        f"Expected 401 without X-Webhook-Secret, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# POST /api/v1/atrativos/whatsapp/inbound
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_inbound_reply_dispatches_task(client, db_session: Session) -> None:
    """POST /inbound with known phone → 200 {"status": "accepted"}."""
    from datetime import datetime, timezone

    rio = _make_rio_record(db_session, sub_state="whatsapp_in_progress")
    phone = "+5511999990101"
    now = datetime.now(timezone.utc)
    consent = ConsentLog(
        id=uuid.uuid4(),
        phone_e164=phone,
        rio_id=rio.id,
        legal_basis="legitimate_interest_commercial_verification",
        norteia_identified=True,
        opted_out=False,
        first_contact_at=now,
        last_contact_at=now,
        purpose="business_validation",
    )
    db_session.add(consent)
    db_session.commit()

    r = client.post(
        "/api/v1/atrativos/whatsapp/inbound",
        json={"from": phone, "body": "Sim, funcionamos normalmente."},
        headers=WEBHOOK_HEADERS,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("status") == "accepted", f"Expected accepted, got: {body}"


@pytest.mark.integration
def test_inbound_reply_ignored_for_unknown_phone(client, db_session: Session) -> None:
    """POST /inbound with unknown phone → 200 {"status": "ignored"}."""
    unknown_phone = "+5599900000001"

    r = client.post(
        "/api/v1/atrativos/whatsapp/inbound",
        json={"from": unknown_phone, "body": "Olá"},
        headers=WEBHOOK_HEADERS,
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("status") == "ignored", f"Expected ignored, got: {body}"


@pytest.mark.integration
def test_inbound_reply_requires_secret(client, db_session: Session) -> None:
    """WR-03: POST /inbound without X-Webhook-Secret → 401 (before any lookup)."""
    r = client.post(
        "/api/v1/atrativos/whatsapp/inbound",
        json={"from": "+5511999990101", "body": "Olá"},
    )
    assert r.status_code == 401, (
        f"Expected 401 without X-Webhook-Secret, got {r.status_code}: {r.text}"
    )
