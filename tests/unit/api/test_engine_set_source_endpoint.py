"""Unit tests for POST /api/v1/engine/source (plan quick-260629-qny, Task 1).

Tests the dedicated set-source endpoint that persists the active collection
source WITHOUT starting a run:
  - valid source → 200 + persists via set_source
  - "default" source → 200 + persists
  - invalid source → 422, Redis source key untouched
  - lane disabled in config_settings → 422 (the DB overlay gates it, like /start)
  - no auth → 401 or 403 (non-2xx)
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

BEARER = "test-bearer-set-source"
STEWARD = "test-steward-set-source"
BEARER_HEADERS = {"Authorization": f"Bearer {BEARER}"}
STEWARD_HEADERS = {"X-Steward-Secret": STEWARD}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD)
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")


class _OverlaySession:
    """config_settings stand-in: ``rows`` is what load_effective_config reads."""

    def __init__(self):
        self.rows: dict = {}

    def execute(self, _stmt):
        rows = [(k, {"v": v}) for k, v in self.rows.items()]
        return type("_Result", (), {"all": lambda _self: rows})()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def db():
    return _OverlaySession()


@pytest.fixture
def client(db):
    from brave.api.deps import get_db, get_redis
    get_redis().flushall()

    from brave.api.main import app
    from fastapi.testclient import TestClient

    # The allowed set is read from the config_settings overlay (no RunHistory row).
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# POST /api/v1/engine/source tests
# ---------------------------------------------------------------------------


def test_engine_set_source_valid(client):
    """POST /engine/source {source: 'tripadvisor'} → 200 + source persisted."""
    from brave.api.deps import get_redis
    from brave.core import engine as collection_engine

    resp = client.post(
        "/api/v1/engine/source",
        headers=STEWARD_HEADERS,
        json={"source": "tripadvisor"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("source") == "tripadvisor"

    # Verify it was persisted to Redis
    rc = get_redis()
    assert collection_engine.get_source(rc) == "tripadvisor"


def test_engine_set_source_default_disabled_422(client):
    """POST /engine/source {source: 'default'} → 422: the Places lane ships dormant.

    /engine/source only accepts ENABLED sources (``enabled_sources`` of the effective
    config); the 'default' (Google Places) lane is disabled by default, so it is
    rejected and the Redis source key is left untouched. Re-enable via config to select it.
    """
    from brave.api.deps import get_redis
    from brave.core import engine as collection_engine

    rc = get_redis()
    before = collection_engine.get_source(rc)

    resp = client.post(
        "/api/v1/engine/source",
        headers=STEWARD_HEADERS,
        json={"source": "default"},
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    # Redis source key untouched by the rejected write.
    assert collection_engine.get_source(rc) == before


def test_engine_set_source_disabled_in_db_422(client, db):
    """A lane the operator disabled in config_settings is rejected — the env default
    (tripadvisor enabled) no longer wins over the DB overlay."""
    from brave.api.deps import get_redis
    from brave.core import engine as collection_engine

    db.rows["source.tripadvisor.enabled"] = False

    resp = client.post(
        "/api/v1/engine/source",
        headers=STEWARD_HEADERS,
        json={"source": "tripadvisor"},
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    assert collection_engine.get_source(get_redis()) is None


def test_engine_set_source_enabled_in_db_200(client, db):
    """The dormant 'default' lane, enabled in config_settings, becomes selectable."""
    db.rows["source.default.enabled"] = True

    resp = client.post(
        "/api/v1/engine/source",
        headers=STEWARD_HEADERS,
        json={"source": "default"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_engine_set_source_invalid_422(client):
    """POST /engine/source {source: 'mtur'} → 422, Redis source key untouched."""
    from brave.api.deps import get_redis
    from brave.core import engine as collection_engine

    rc = get_redis()
    # Ensure source key is absent before the call
    assert collection_engine.get_source(rc) is None

    resp = client.post(
        "/api/v1/engine/source",
        headers=STEWARD_HEADERS,
        json={"source": "mtur"},
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    # Redis source key must remain untouched (still None)
    assert collection_engine.get_source(rc) is None


def test_engine_set_source_no_auth(client):
    """POST /engine/source without auth headers → 401 or 403 (non-2xx)."""
    resp = client.post(
        "/api/v1/engine/source",
        json={"source": "tripadvisor"},
    )
    assert resp.status_code in (401, 403), (
        f"Expected 401 or 403, got {resp.status_code}: {resp.text}"
    )
