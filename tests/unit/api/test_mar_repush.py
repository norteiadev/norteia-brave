"""POST /api/v1/mar/repush + the norteia_api block of GET /api/v1/engine/status.

Setup mirrors test_engine_latch.py: fakeredis + MagicMock DB override.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

BEARER = "test-bearer-token-mar-repush"
BEARER_HEADERS = {"Authorization": f"Bearer {BEARER}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", "test-steward-secret-mar-repush")
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")

    from brave.api.deps import get_db, get_redis

    get_redis().flushall()

    from fastapi.testclient import TestClient

    from brave.api.main import app

    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _patch(monkeypatch, *, up, pending=0, dispatched=0):
    import brave.api.routers.engine as engine_router
    import brave.tasks.pipeline as pipeline

    calls = MagicMock(return_value=dispatched)
    monkeypatch.setattr(engine_router, "norteia_api_up", lambda _redis: up)
    monkeypatch.setattr(
        engine_router,
        "norteia_api_health",
        lambda _redis: None if up is None else ("ok" if up else "ingest:401"),
    )
    monkeypatch.setattr(engine_router, "count_pending_pushes", lambda _db: pending)
    monkeypatch.setattr(pipeline, "dispatch_pending_pushes", calls)
    return calls


def test_status_carries_norteia_api_block(client, monkeypatch):
    _patch(monkeypatch, up=False, pending=7)
    body = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert body["norteia_api"] == {"up": False, "reason": "ingest:401", "pending": 7}


def test_status_has_no_reason_when_up(client, monkeypatch):
    _patch(monkeypatch, up=True)
    body = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert body["norteia_api"] == {"up": True, "reason": None, "pending": 0}


def test_repush_dispatches_when_api_up(client, monkeypatch):
    dispatch = _patch(monkeypatch, up=True, dispatched=5)
    resp = client.post("/api/v1/mar/repush", headers=BEARER_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"dispatched": 5}
    dispatch.assert_called_once()


def test_repush_503_and_no_dispatch_when_api_down(client, monkeypatch):
    dispatch = _patch(monkeypatch, up=False)
    resp = client.post("/api/v1/mar/repush", headers=BEARER_HEADERS)
    assert resp.status_code == 503
    assert "indisponível" in resp.json()["detail"]
    dispatch.assert_not_called()


def test_repush_requires_auth(client, monkeypatch):
    dispatch = _patch(monkeypatch, up=True)
    assert client.post("/api/v1/mar/repush").status_code == 401
    dispatch.assert_not_called()
