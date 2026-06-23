"""Integration tests for the collection-engine control endpoints.

Uses fakeredis (BRAVE_USE_FAKEREDIS=1) for engine state and mocks the orchestrator
dispatch so no broker is needed. DB is required for the status counts.
"""

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")
os.environ.setdefault(
    "BRAVE_DB_URL",
    "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
)

BEARER = "test-engine-bearer-token"
STEWARD = "test-engine-steward-secret"
BEARER_HEADERS = {"Authorization": f"Bearer {BEARER}"}
STEWARD_HEADERS = {"X-Steward-Secret": STEWARD}


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD)
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")


@pytest.fixture
def dispatched():
    """Captures the kwargs the orchestrator dispatch was called with (depth, etc.)."""
    return {}


@pytest.fixture
def client(monkeypatch, dispatched):
    from brave.api.deps import get_redis

    # Reset engine state between tests (fakeredis singleton persists per process).
    get_redis().flushall()

    # Mock the orchestrator dispatch so start never touches a broker; capture the
    # call kwargs so tests can assert depth was threaded through.
    import brave.tasks.pipeline as pipeline

    def _capture(*args, **kwargs):
        dispatched.clear()
        dispatched.update(kwargs)
        return None

    monkeypatch.setattr(pipeline.engine_sweep_run, "delay", _capture)

    from brave.api.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.integration
def test_status_requires_bearer(client):
    assert client.get("/api/v1/engine/status").status_code == 401


@pytest.mark.integration
def test_status_idle_by_default_with_counts(client):
    r = client.get("/api/v1/engine/status", headers=BEARER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "idle"
    assert body["ufs_total"] == 0
    assert "counts" in body
    assert set(body["counts"]) == {"nascente", "rio", "mar", "atrativos_by_sub_state"}
    assert set(body["counts"]["rio"]) == {"in_progress", "mar", "dlq", "descarte"}


@pytest.mark.integration
def test_start_transitions_to_running(client):
    r = client.post(
        "/api/v1/engine/start", headers=STEWARD_HEADERS, json={"depth": "nascente"}
    )
    assert r.status_code == 202
    assert r.json()["status"] == "started"

    s = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert s["state"] == "running"
    assert s["ufs_total"] == 27  # full UF_LIST default


@pytest.mark.integration
def test_start_twice_returns_409(client):
    assert (
        client.post(
            "/api/v1/engine/start", headers=STEWARD_HEADERS, json={"depth": "nascente"}
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/api/v1/engine/start", headers=STEWARD_HEADERS, json={"depth": "nascente"}
        ).status_code
        == 409
    )


@pytest.mark.integration
def test_stop_requests_graceful_stop(client):
    client.post("/api/v1/engine/start", headers=STEWARD_HEADERS, json={"depth": "nascente"})
    r = client.post("/api/v1/engine/stop", headers=STEWARD_HEADERS)
    assert r.status_code == 202
    assert r.json()["status"] == "stopping"
    s = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert s["state"] == "stopping"


@pytest.mark.integration
def test_stop_when_idle_is_noop(client):
    r = client.post("/api/v1/engine/stop", headers=STEWARD_HEADERS)
    assert r.status_code == 202
    assert r.json()["status"] == "noop"


@pytest.mark.integration
def test_start_requires_auth(client):
    assert client.post("/api/v1/engine/start").status_code == 401
    assert client.post("/api/v1/engine/stop").status_code == 401


@pytest.mark.integration
def test_start_accepts_custom_ufs_and_lane(client):
    r = client.post(
        "/api/v1/engine/start",
        headers=STEWARD_HEADERS,
        json={"ufs": ["BA", "RJ", "SP"], "lane": "destinos", "depth": "nascente_rio"},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["ufs_total"] == 3 and body["lane"] == "destinos"
    s = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert s["ufs_total"] == 3


# --- Depth: required server-side, validated before start/409, threaded down ---


@pytest.mark.integration
def test_start_without_depth_is_422(client):
    r = client.post("/api/v1/engine/start", headers=STEWARD_HEADERS, json={})
    assert r.status_code == 422
    # State untouched — an invalid request never flips the engine on.
    s = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert s["state"] == "idle"
    assert s["depth"] is None


@pytest.mark.integration
def test_start_with_invalid_depth_is_422(client):
    r = client.post(
        "/api/v1/engine/start", headers=STEWARD_HEADERS, json={"depth": "rio"}
    )
    assert r.status_code == 422
    s = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert s["state"] == "idle"
    assert s["depth"] is None


@pytest.mark.integration
def test_start_threads_depth_to_dispatch_and_status(client, dispatched):
    r = client.post(
        "/api/v1/engine/start", headers=STEWARD_HEADERS, json={"depth": "nascente"}
    )
    assert r.status_code == 202
    assert r.json()["depth"] == "nascente"
    # Orchestrator dispatch received depth as a kwarg (no broker contacted).
    assert dispatched.get("depth") == "nascente"
    s = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert s["depth"] == "nascente"


@pytest.mark.integration
def test_unauthenticated_start_is_401_even_with_valid_depth(client):
    # Depth must never become an unauthenticated trigger (T-10-01 / T-05-07).
    r = client.post("/api/v1/engine/start", json={"depth": "nascente"})
    assert r.status_code == 401
    s = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert s["state"] == "idle"


@pytest.mark.integration
def test_depth_validation_precedes_already_running_check(client):
    # First a valid run goes active...
    assert (
        client.post(
            "/api/v1/engine/start", headers=STEWARD_HEADERS, json={"depth": "nascente"}
        ).status_code
        == 202
    )
    # ...then a depth-less /start mid-run returns 422 (NOT 409) and leaves the
    # active run's depth untouched — proving depth validation runs first.
    r = client.post("/api/v1/engine/start", headers=STEWARD_HEADERS, json={})
    assert r.status_code == 422
    r2 = client.post(
        "/api/v1/engine/start", headers=STEWARD_HEADERS, json={"depth": "rio"}
    )
    assert r2.status_code == 422
    s = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert s["state"] == "running"
    assert s["depth"] == "nascente"  # untouched by the rejected requests
