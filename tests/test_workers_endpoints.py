"""Offline pytest suite for workers + failures observability endpoints (08-07, D-07).

Endpoints under test:
  GET /api/v1/workers   — Celery inspect + Redis queue depths, graceful broker-absent
  GET /api/v1/failures  — PoisonQuarantine list, payload excluded

Security tests (T-08-21, T-08-07, T-08-08):
  - All endpoints without Authorization: Bearer → 401
  - /workers gracefully returns 200 (not 500) when broker/Redis absent
  - /failures items never include payload field (T-08-08)

100% offline:
  - No real Celery broker: monkeypatch celery_app.control.inspect
  - No real Redis: app.dependency_overrides[get_redis] = lambda: FakeRedis()
  - No real DB for most tests; integration tests use docker-compose Postgres
"""

import os
import uuid
from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Test tokens — unique to this module to avoid collision with other test modules.
BEARER_TOKEN = "test-workers-bearer-token-08-07"
os.environ.setdefault(
    "BRAVE_DB_URL",
    "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
)

BEARER_HEADERS = {"Authorization": f"Bearer {BEARER_TOKEN}"}


# ---------------------------------------------------------------------------
# Helper: build a MagicMock celery inspect object with given return values
# ---------------------------------------------------------------------------


def _make_mock_inspect(
    ping=None,
    active=None,
    reserved=None,
) -> MagicMock:
    """Return a MagicMock with .ping(), .active(), .reserved() returning given values."""
    mock = MagicMock()
    mock.ping.return_value = ping
    mock.active.return_value = active
    mock.reserved.return_value = reserved
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    """FastAPI app instance (module-scoped to avoid reimporting)."""
    from brave.api.main import app as _app  # noqa: PLC0415
    return _app


@pytest.fixture(autouse=True)
def _pin_test_secrets():
    """Force our module-specific test bearer token before each test.

    pydantic-settings DashboardConfig re-reads os.environ on every instantiation.
    This autouse fixture ensures our test token is always active regardless of
    test collection order.
    """
    os.environ["BRAVE_DASHBOARD_BEARER_TOKEN"] = BEARER_TOKEN
    yield


@pytest.fixture(scope="module")
def client(app):
    """FastAPI TestClient — bare, no default auth headers."""
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /api/v1/workers — auth tests (no DB required)
# ---------------------------------------------------------------------------


def test_workers_bearer_required(client):
    """GET /api/v1/workers without Authorization: Bearer → 401 (T-08-21)."""
    r = client.get("/api/v1/workers")
    assert r.status_code == 401


def test_failures_bearer_required(client):
    """GET /api/v1/failures without Authorization: Bearer → 401 (T-08-21)."""
    r = client.get("/api/v1/failures")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/workers — broker-absent (100% offline via monkeypatch)
# ---------------------------------------------------------------------------


def test_workers_broker_down(app, client, monkeypatch):
    """GET /api/v1/workers → 200 with broker_reachable=False, workers=[] when inspect returns None.

    Celery inspect is monkeypatched to return None for all calls.
    Redis is overridden with fakeredis (queue depths = 0 integers, not None).
    """
    mock_inspect = _make_mock_inspect(ping=None, active=None, reserved=None)

    monkeypatch.setattr(
        "brave.tasks.celery_app.app.control.inspect",
        lambda **kw: mock_inspect,
    )

    fake = fakeredis.FakeRedis()
    from brave.api import deps  # noqa: PLC0415

    app.dependency_overrides[deps.get_redis] = lambda: fake
    try:
        r = client.get("/api/v1/workers", headers=BEARER_HEADERS)
    finally:
        app.dependency_overrides.pop(deps.get_redis, None)

    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert body["broker_reachable"] is False, (
        f"broker_reachable must be False when inspect returns None, got {body['broker_reachable']!r}"
    )
    assert body["workers"] == [], (
        f"workers must be [] when broker is down, got {body['workers']!r}"
    )


def test_workers_broker_down_redis_llen_fails(app, client, monkeypatch):
    """GET /api/v1/workers → 200 with broker_reachable=False, queues values None when llen raises.

    Inspect returns None (broker down).
    Override get_redis with a MagicMock whose llen() raises ConnectionError.
    workers.py wraps Redis LLEN in try/except → returns null on error.

    Note: get_redis must succeed (inject the mock) for the handler to receive it.
    Having get_redis itself raise a ConnectionError causes FastAPI DI to return 500
    before the handler runs — the try/except in the handler never fires.
    Instead, inject a fake Redis whose llen() raises, so the handler's try/except
    catches it and returns queues=None as designed.
    """
    mock_inspect = _make_mock_inspect(ping=None, active=None, reserved=None)

    monkeypatch.setattr(
        "brave.tasks.celery_app.app.control.inspect",
        lambda **kw: mock_inspect,
    )

    # MagicMock Redis whose llen() raises to simulate Redis connection failure
    llen_failing_redis = MagicMock()
    llen_failing_redis.llen.side_effect = ConnectionError("Redis unavailable (test)")

    from brave.api import deps  # noqa: PLC0415

    app.dependency_overrides[deps.get_redis] = lambda: llen_failing_redis
    try:
        r = client.get("/api/v1/workers", headers=BEARER_HEADERS)
    finally:
        app.dependency_overrides.pop(deps.get_redis, None)

    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert body["broker_reachable"] is False
    assert body["queues"]["brave.sweep"] is None, (
        "queues.brave.sweep must be None when llen raises (workers.py try/except)"
    )
    assert body["queues"]["celery"] is None, (
        "queues.celery must be None when llen raises (workers.py try/except)"
    )


def test_workers_broker_up(app, client, monkeypatch):
    """GET /api/v1/workers → 200 with broker_reachable=True, workers has status=='up'.

    Inspect returns a valid ping dict; Redis is fakeredis (llen returns 0).
    """
    mock_inspect = _make_mock_inspect(
        ping={"celery@worker-1": {"ok": "pong"}},
        active={"celery@worker-1": []},
        reserved={"celery@worker-1": []},
    )

    monkeypatch.setattr(
        "brave.tasks.celery_app.app.control.inspect",
        lambda **kw: mock_inspect,
    )

    fake = fakeredis.FakeRedis()
    from brave.api import deps  # noqa: PLC0415

    app.dependency_overrides[deps.get_redis] = lambda: fake
    try:
        r = client.get("/api/v1/workers", headers=BEARER_HEADERS)
    finally:
        app.dependency_overrides.pop(deps.get_redis, None)

    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert body["broker_reachable"] is True, (
        f"broker_reachable must be True when inspect returns pong, got {body['broker_reachable']!r}"
    )
    assert len(body["workers"]) == 1, f"Expected 1 worker entry, got {body['workers']!r}"
    assert body["workers"][0]["status"] == "up", (
        f"worker status must be 'up', got {body['workers'][0]['status']!r}"
    )
    assert body["workers"][0]["hostname"] == "celery@worker-1"


def test_workers_response_shape(app, client, monkeypatch):
    """GET /api/v1/workers → body has all required keys: broker_reachable, workers, queues, beat_schedule."""
    mock_inspect = _make_mock_inspect(ping=None, active=None, reserved=None)

    monkeypatch.setattr(
        "brave.tasks.celery_app.app.control.inspect",
        lambda **kw: mock_inspect,
    )

    fake = fakeredis.FakeRedis()
    from brave.api import deps  # noqa: PLC0415

    app.dependency_overrides[deps.get_redis] = lambda: fake
    try:
        r = client.get("/api/v1/workers", headers=BEARER_HEADERS)
    finally:
        app.dependency_overrides.pop(deps.get_redis, None)

    assert r.status_code == 200

    body = r.json()
    for key in ("broker_reachable", "workers", "queues", "beat_schedule"):
        assert key in body, f"Response missing required key: {key!r}"

    assert "entries" in body["beat_schedule"], (
        "beat_schedule must have 'entries' key"
    )
    assert body["beat_schedule"]["entries"] == 54, (
        f"beat_schedule.entries must be 54, got {body['beat_schedule']['entries']!r}"
    )


# ---------------------------------------------------------------------------
# GET /api/v1/failures — shape and payload exclusion (integration + offline combo)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_failures_empty(client, db_session: Session):
    """GET /api/v1/failures with Bearer and no records → 200; {total:0, by_task:{}, items:[]}."""
    # Note: total reflects all PoisonQuarantine rows in DB (other tests may add some),
    # so we only assert structure is correct, not that total==0 universally.
    r = client.get("/api/v1/failures", headers=BEARER_HEADERS)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert "total" in body, "failures response must have 'total' key"
    assert "by_task" in body, "failures response must have 'by_task' key"
    assert "items" in body, "failures response must have 'items' key"
    assert isinstance(body["total"], int)
    assert isinstance(body["by_task"], dict)
    assert isinstance(body["items"], list)


@pytest.mark.integration
def test_failures_with_data(client, db_session: Session):
    """GET /api/v1/failures → 200; item has task_name; payload NOT exposed (T-08-08)."""
    from brave.core.models import PoisonQuarantine  # noqa: PLC0415

    pq = PoisonQuarantine(
        id=uuid.uuid4(),
        task_name="brave.process_nascente",
        error_message="boom — integration test error",
        payload={"x": 1, "secret_internal": "should-not-appear"},
    )
    db_session.add(pq)
    db_session.commit()

    r = client.get("/api/v1/failures", headers=BEARER_HEADERS)
    assert r.status_code == 200

    body = r.json()
    assert body["total"] >= 1

    # Find our item
    matching = [item for item in body["items"] if item.get("task_name") == "brave.process_nascente"]
    assert matching, "Our PoisonQuarantine record should appear in /failures"

    item = matching[0]
    assert item["task_name"] == "brave.process_nascente"
    assert "error_message" in item


@pytest.mark.integration
def test_failures_payload_not_exposed(client, db_session: Session):
    """GET /api/v1/failures → payload field NEVER in items (T-08-08 explicit assertion)."""
    from brave.core.models import PoisonQuarantine  # noqa: PLC0415

    pq = PoisonQuarantine(
        id=uuid.uuid4(),
        task_name="brave.sweep_uf",
        error_message="payload exposure test",
        payload={"internal_data": "must-not-be-exposed", "nascente_id": str(uuid.uuid4())},
    )
    db_session.add(pq)
    db_session.commit()

    r = client.get("/api/v1/failures", headers=BEARER_HEADERS)
    assert r.status_code == 200

    body = r.json()
    for item in body["items"]:
        assert "payload" not in item, (
            f"T-08-08: 'payload' MUST NOT appear in /failures items — found in {item!r}"
        )
