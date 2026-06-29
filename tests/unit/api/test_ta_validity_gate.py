"""Unit tests for the R2 TA session validity gate in POST /api/v1/engine/start (260629-e69).

R2: POST /engine/start with source=tripadvisor requires BRAVE_TA_SESSION_KEY present
    in Redis with TTL > 0. Missing or expired session → 409 with PT-BR reason.

Tests:
  - source='tripadvisor' + no Redis session → 409, detail contains "TTL" or "sessão"
  - source='tripadvisor' + valid session (setex 3600) → 202
  - source='default' + no Redis session → 202 (gate not invoked)
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

BEARER = "test-bearer-token-ta-validity-gate"
STEWARD = "test-steward-secret-ta-validity-gate"
STEWARD_HEADERS = {"X-Steward-Secret": STEWARD}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD)
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")


@pytest.fixture
def client(monkeypatch):
    from brave.api.deps import get_db, get_redis

    get_redis().flushall()

    import brave.tasks.pipeline as pipeline

    monkeypatch.setattr(pipeline.engine_sweep_run, "delay", lambda *a, **kw: None)

    from brave.api.main import app
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def fake_redis():
    from brave.api.deps import get_redis

    return get_redis()


# ---------------------------------------------------------------------------
# R2 gate tests
# ---------------------------------------------------------------------------


def test_ta_start_no_session_returns_409(client, fake_redis):
    """source='tripadvisor' with no Redis session → 409, detail mentions TTL or sessão."""
    # Ensure key is absent (flushall in client fixture clears it)
    resp = client.post(
        "/api/v1/engine/start",
        headers=STEWARD_HEADERS,
        json={"depth": "nascente", "source": "tripadvisor"},
    )
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
    detail = resp.json().get("detail", "")
    assert "TTL" in detail or "sessão" in detail or "sess" in detail.lower(), (
        f"409 detail must mention TTL or sessão, got: {detail!r}"
    )


def test_ta_start_valid_session_returns_202(client, fake_redis):
    """source='tripadvisor' with a present session (TTL > 0) → 202."""
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

    fake_redis.setex(BRAVE_TA_SESSION_KEY, 3600, '{"cookies":{}}')
    resp = client.post(
        "/api/v1/engine/start",
        headers=STEWARD_HEADERS,
        json={"depth": "nascente", "source": "tripadvisor"},
    )
    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    assert resp.json().get("source") == "tripadvisor"


def test_default_source_no_session_returns_202(client, fake_redis):
    """source='default' with no TA session in Redis → 202 (gate not invoked)."""
    # No session seeded — gate must be skipped for 'default'
    resp = client.post(
        "/api/v1/engine/start",
        headers=STEWARD_HEADERS,
        json={"depth": "nascente", "source": "default"},
    )
    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    assert resp.json().get("source") == "default"
