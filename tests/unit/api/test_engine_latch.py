"""Unit tests for the engine enabled latch (plan 260628-jvk).

Verifies that:
  - POST /start sets the enabled latch (is_enabled → True)
  - POST /stop when running clears enabled (is_enabled → False) + returns "stopping"
  - POST /stop when idle still clears enabled (is_enabled → False) + returns 202
  - GET /status always carries the "enabled" boolean

Setup mirrors test_engine_source.py: fakeredis + monkeypatched engine_sweep_run.delay
+ MagicMock DB override (runs_history is best-effort, irrelevant here).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

# Ensure fakeredis is active before importing the app.
os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

BEARER = "test-bearer-token-engine-latch"
STEWARD = "test-steward-secret-engine-latch"
STEWARD_HEADERS = {"X-Steward-Secret": STEWARD}
BEARER_HEADERS = {"Authorization": f"Bearer {BEARER}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD)
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")


@pytest.fixture
def dispatched():
    return {}


@pytest.fixture
def client(monkeypatch, dispatched):
    from brave.api.deps import get_db, get_redis

    get_redis().flushall()

    import brave.tasks.pipeline as pipeline

    def _capture(*args, **kwargs):
        dispatched.clear()
        dispatched.update(kwargs)
        return None

    monkeypatch.setattr(pipeline.engine_sweep_run, "delay", _capture)

    from brave.api.main import app
    from fastapi.testclient import TestClient

    # engine_start persists a runs_history row via get_db. Override with a
    # MagicMock session — runs-history is best-effort and irrelevant here.
    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Enabled latch tests
# ---------------------------------------------------------------------------


def test_start_sets_enabled(client):
    """POST /start with valid depth sets the enabled latch to True."""
    from brave.api.deps import get_redis
    from brave.core import engine as collection_engine

    rc = get_redis()
    assert collection_engine.is_enabled(rc) is False

    resp = client.post(
        "/api/v1/engine/start",
        headers=STEWARD_HEADERS,
        json={"depth": "nascente"},
    )
    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    assert collection_engine.is_enabled(rc) is True


def test_status_includes_enabled_field(client):
    """GET /status always carries the 'enabled' boolean field.

    Tested directly via the engine module to avoid the _pipeline_counts DB call
    (following the same pattern as test_engine_source.test_engine_status_includes_source_key).
    """
    import fakeredis

    from brave.core import engine as collection_engine

    rc = fakeredis.FakeRedis()
    status = collection_engine.get_status(rc)
    assert "enabled" in status, "get_status must always include 'enabled'"
    assert status["enabled"] is False

    collection_engine.start_run(rc, ufs_total=1)
    assert collection_engine.get_status(rc)["enabled"] is True


def test_stop_when_idle_clears_enabled(client):
    """POST /stop when engine is idle still returns 202 and clears the enabled latch."""
    from brave.api.deps import get_redis
    from brave.core import engine as collection_engine

    rc = get_redis()

    # Start the engine → enabled=True, state=RUNNING.
    resp = client.post(
        "/api/v1/engine/start",
        headers=STEWARD_HEADERS,
        json={"depth": "nascente"},
    )
    assert resp.status_code == 202

    # Simulate orchestrator finishing: force state back to IDLE while latch stays.
    collection_engine.mark_idle(rc)
    assert collection_engine.get_state(rc) == collection_engine.IDLE
    assert collection_engine.is_enabled(rc) is True  # latch still set before stop

    # POST /stop on an idle engine: must still clear the enabled latch.
    resp = client.post("/api/v1/engine/stop", headers=STEWARD_HEADERS)
    assert resp.status_code == 202
    assert collection_engine.is_enabled(rc) is False


def test_stop_when_running_clears_enabled(client):
    """POST /stop when running returns 202 'stopping' and clears the enabled latch."""
    from brave.api.deps import get_redis
    from brave.core import engine as collection_engine

    rc = get_redis()

    # Start the engine.
    resp = client.post(
        "/api/v1/engine/start",
        headers=STEWARD_HEADERS,
        json={"depth": "nascente"},
    )
    assert resp.status_code == 202
    assert collection_engine.get_state(rc) == collection_engine.RUNNING
    assert collection_engine.is_enabled(rc) is True

    # POST /stop while running → "stopping" + latch cleared.
    resp = client.post("/api/v1/engine/stop", headers=STEWARD_HEADERS)
    assert resp.status_code == 202
    assert resp.json()["status"] == "stopping"
    assert collection_engine.is_enabled(rc) is False
