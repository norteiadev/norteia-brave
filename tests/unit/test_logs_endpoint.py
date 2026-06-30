"""Offline tests for GET /api/v1/logs (Phase ks0).

100% offline:
  - BRAVE_USE_FAKEREDIS=1 — deps.get_redis returns fakeredis
  - dependency_overrides[get_redis] injects the module-scoped FakeRedis instance
  - No real Redis, DB, or external calls

Endpoints under test:
  GET /api/v1/logs?source=...&since=...&limit=...
    - 401 without Bearer
    - 200 + empty lines on fresh source
    - 200 + seeded lines
    - defaults to brave:engine:source when no source param given
"""

import json
import os

import fakeredis
import pytest
from fastapi.testclient import TestClient

# Set environment before importing the app — matches the workers endpoint pattern.
BEARER_TOKEN = "test-logs-bearer-ks0"
os.environ.setdefault(
    "BRAVE_DB_URL", "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave"
)
os.environ["BRAVE_USE_FAKEREDIS"] = "1"

HEADERS = {"Authorization": f"Bearer {BEARER_TOKEN}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture(scope="module")
def app(fake_redis):
    """FastAPI app with get_redis overridden to our module-scoped FakeRedis."""
    from brave.api.main import app as _app  # noqa: PLC0415
    from brave.api.deps import get_redis  # noqa: PLC0415

    os.environ["BRAVE_DASHBOARD_BEARER_TOKEN"] = BEARER_TOKEN
    _app.dependency_overrides[get_redis] = lambda: fake_redis
    yield _app
    _app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _flush(fake_redis):
    """Flush all Redis keys before each test to ensure isolation."""
    fake_redis.flushall()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_logs_requires_bearer(client):
    """GET /api/v1/logs without Authorization: Bearer → 401 (T-ks0-02)."""
    r = client.get("/api/v1/logs?source=tripadvisor")
    assert r.status_code == 401


def test_logs_empty_on_fresh_source(client):
    """GET /api/v1/logs with Bearer + empty redis → 200, lines=[], cursor=0."""
    r = client.get("/api/v1/logs?source=tripadvisor", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "tripadvisor"
    assert body["lines"] == []
    assert body["cursor"] == 0


def test_logs_returns_seeded_lines(client, fake_redis):
    """After seeding 2 append_log entries → GET returns 2 lines, cursor >= 1."""
    from brave.observability.log_buffer import append_log  # noqa: PLC0415

    append_log(fake_redis, "tripadvisor", {"event": "uf_started", "level": "info"})
    append_log(fake_redis, "tripadvisor", {"event": "page_ingested", "level": "info"})

    r = client.get("/api/v1/logs?source=tripadvisor", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert len(body["lines"]) == 2
    assert body["cursor"] >= 1


def test_logs_defaults_to_active_engine_source(client, fake_redis):
    """GET /api/v1/logs (no source param) uses brave:engine:source from Redis."""
    from brave.observability.log_buffer import append_log  # noqa: PLC0415

    # Seed the active engine source key
    fake_redis.set("brave:engine:source", "default")

    # Seed one log entry under "default"
    append_log(fake_redis, "default", {"event": "engine_tick", "level": "debug"})

    r = client.get("/api/v1/logs", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "default"
    assert len(body["lines"]) == 1
