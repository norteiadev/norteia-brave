"""Unit tests for POST /api/v1/engine/source (plan quick-260629-qny, Task 1).

Tests the dedicated set-source endpoint that persists the active collection
source WITHOUT starting a run:
  - valid source → 200 + persists via set_source
  - "default" source → 200 + persists
  - invalid source → 422, Redis source key untouched
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


@pytest.fixture
def client():
    from brave.api.deps import get_redis
    get_redis().flushall()

    from brave.api.main import app
    from fastapi.testclient import TestClient

    # set-source endpoint does NOT touch get_db (no RunHistory row) —
    # no DB override needed. The fixture is simpler than test_engine_source's.
    yield TestClient(app, raise_server_exceptions=False)


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


def test_engine_set_source_default(client):
    """POST /engine/source {source: 'default'} → 200 + persists 'default'."""
    from brave.api.deps import get_redis
    from brave.core import engine as collection_engine

    resp = client.post(
        "/api/v1/engine/source",
        headers=STEWARD_HEADERS,
        json={"source": "default"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("source") == "default"

    rc = get_redis()
    assert collection_engine.get_source(rc) == "default"


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
