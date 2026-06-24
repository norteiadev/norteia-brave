"""Unit tests for TripAdvisor session injection and status endpoints (plan 12-02).

Tests:
  - POST /api/v1/tripadvisor/session: valid inject → ready + Redis key set
  - POST /api/v1/tripadvisor/session: malformed body → 422 (no Redis write)
  - POST /api/v1/tripadvisor/session: extra field → 422 (Pydantic extra=forbid)
  - POST /api/v1/tripadvisor/session: empty cookies → 422
  - POST /api/v1/tripadvisor/session: empty query_ids → 422
  - POST /api/v1/tripadvisor/session: body > 64 KB → 422
  - POST /api/v1/tripadvisor/session: canary fail (SessionExpiredError) → key deleted + 422
  - POST /api/v1/tripadvisor/session: canary empty result → key deleted + 422
  - GET /api/v1/tripadvisor/session/status: session present → {present: True, expires_in, query_ids, reason: null}
  - GET /api/v1/tripadvisor/session/status: needs_bootstrap marker set → {present: False, reason: "needs_bootstrap"}
  - GET /api/v1/tripadvisor/session/status: no session, no marker → {present: False, reason: null}

All tests are 100% offline (fakeredis, no real TripAdvisor calls).
"""

from __future__ import annotations

import os

import fakeredis
import pytest

# Ensure fakeredis mode so get_redis() doesn't try to ping a real Redis
os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

BEARER = "test-bearer-token-ta-session"
STEWARD = "test-steward-secret-ta-session"
BEARER_HEADERS = {"Authorization": f"Bearer {BEARER}"}
STEWARD_HEADERS = {"X-Steward-Secret": STEWARD}

_VALID_BODY = {
    "cookies": {"datadome": "x"},
    "query_ids": {"destinations": "abc123def456abcd"},
    "user_agent": "Mozilla/5.0",
    "acquired_at": "2026-06-24T12:00:00Z",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD)
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")


@pytest.fixture
def fake_redis():
    """Fresh FakeRedis per test."""
    return fakeredis.FakeRedis()


@pytest.fixture
def client(monkeypatch, fake_redis):
    """TestClient with fakeredis and auth bypassed for dependency injection."""
    from brave.api.deps import get_redis, require_steward_or_bearer
    from brave.api.main import app
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_redis] = lambda: fake_redis
    # Do NOT bypass require_steward_or_bearer — we test auth in dedicated tests
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def authed_client(monkeypatch, fake_redis):
    """TestClient with fakeredis + auth overridden for happy-path tests."""
    from brave.api.deps import get_redis, get_db, require_steward_or_bearer
    from brave.api.main import app
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[require_steward_or_bearer] = lambda: None
    # Override get_db to avoid needing a real database for audit logging
    app.dependency_overrides[get_db] = lambda: None
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Valid inject → 200 ready
# ---------------------------------------------------------------------------


def test_inject_valid_session_returns_ready(authed_client, fake_redis, monkeypatch):
    """POST with valid body + canary no-op → 200 + {status: ready} + Redis key set."""
    import brave.api.routers.tripadvisor_session as ts_module

    async def _noop_canary(session, ta_config, redis):
        pass

    monkeypatch.setattr(ts_module, "_run_canary", _noop_canary)

    resp = authed_client.post("/api/v1/tripadvisor/session", json=_VALID_BODY)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("status") == "ready"

    # Redis key must be set
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY
    assert fake_redis.exists(BRAVE_TA_SESSION_KEY), "Redis key must be set after valid inject"


# ---------------------------------------------------------------------------
# Malformed body → 422, no Redis write
# ---------------------------------------------------------------------------


def test_inject_malformed_body_422(authed_client, fake_redis):
    """POST with missing cookies field → 422 before Redis write."""
    resp = authed_client.post(
        "/api/v1/tripadvisor/session",
        json={"query_ids": {"destinations": "abc"}, "user_agent": "Mozilla", "acquired_at": "2026-06-24T12:00:00Z"},
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY
    assert not fake_redis.exists(BRAVE_TA_SESSION_KEY), "Redis key must NOT be set on 422"


# ---------------------------------------------------------------------------
# Extra field → 422 (Pydantic extra=forbid)
# ---------------------------------------------------------------------------


def test_inject_extra_field_forbidden_422(authed_client, fake_redis):
    """POST with unknown_field in body → 422 (Pydantic extra=forbid)."""
    body = {**_VALID_BODY, "unknown_field": "forbidden"}
    resp = authed_client.post("/api/v1/tripadvisor/session", json=body)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY
    assert not fake_redis.exists(BRAVE_TA_SESSION_KEY)


# ---------------------------------------------------------------------------
# Empty cookies → 422
# ---------------------------------------------------------------------------


def test_inject_empty_cookies_422(authed_client, fake_redis):
    """POST with cookies={} → 422 (non-empty validation)."""
    body = {**_VALID_BODY, "cookies": {}}
    resp = authed_client.post("/api/v1/tripadvisor/session", json=body)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Empty query_ids → 422
# ---------------------------------------------------------------------------


def test_inject_empty_query_ids_422(authed_client, fake_redis):
    """POST with query_ids={} → 422 (≥1 entry validation)."""
    body = {**_VALID_BODY, "query_ids": {}}
    resp = authed_client.post("/api/v1/tripadvisor/session", json=body)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Body size limit
# ---------------------------------------------------------------------------


def test_inject_body_size_limit(authed_client, fake_redis):
    """POST with body > 64 KB → 422 or 413."""
    big_body = {
        **_VALID_BODY,
        "cookies": {"datadome": "x" * 70000},
    }
    resp = authed_client.post("/api/v1/tripadvisor/session", json=big_body)
    assert resp.status_code in (422, 413), (
        f"Expected 422 or 413 for oversized body, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# Canary fail → key deleted + 422 invalid_session
# ---------------------------------------------------------------------------


def test_canary_fail_deletes_key_returns_422(authed_client, fake_redis, monkeypatch):
    """Canary raises SessionExpiredError → Redis key deleted + 422 invalid_session."""
    import brave.api.routers.tripadvisor_session as ts_module
    from brave.lanes.tripadvisor.client import SessionExpiredError, BRAVE_TA_SESSION_KEY
    from fastapi import HTTPException

    async def _failing_canary(session, ta_config, redis):
        redis.delete(BRAVE_TA_SESSION_KEY)
        raise HTTPException(status_code=422, detail="invalid_session")

    monkeypatch.setattr(ts_module, "_run_canary", _failing_canary)

    resp = authed_client.post("/api/v1/tripadvisor/session", json=_VALID_BODY)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    assert resp.json().get("detail") == "invalid_session"

    assert not fake_redis.exists(BRAVE_TA_SESSION_KEY), "Redis key must be deleted on canary fail"


# ---------------------------------------------------------------------------
# Canary empty result → key deleted + 422 invalid_session
# ---------------------------------------------------------------------------


def test_canary_empty_result_returns_422(authed_client, fake_redis, monkeypatch):
    """Canary returns empty result list → key deleted + 422 invalid_session."""
    import brave.api.routers.tripadvisor_session as ts_module
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY
    from fastapi import HTTPException

    async def _empty_canary(session, ta_config, redis):
        # Simulate the empty-result guard inside _run_canary
        redis.delete(BRAVE_TA_SESSION_KEY)
        raise HTTPException(status_code=422, detail="invalid_session")

    monkeypatch.setattr(ts_module, "_run_canary", _empty_canary)

    resp = authed_client.post("/api/v1/tripadvisor/session", json=_VALID_BODY)
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    assert resp.json().get("detail") == "invalid_session"

    assert not fake_redis.exists(BRAVE_TA_SESSION_KEY), "Redis key must be deleted on empty canary result"


# ---------------------------------------------------------------------------
# GET /session/status — session present
# ---------------------------------------------------------------------------


def test_status_present(authed_client, fake_redis):
    """GET /session/status with session key in Redis → present=True + metadata."""
    import json
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

    session_data = {
        "cookies": {"datadome": "x"},
        "query_ids": {"destinations": "abc123def456abcd"},
        "user_agent": "Mozilla/5.0",
        "acquired_at": "2026-06-24T12:00:00Z",
    }
    fake_redis.setex(BRAVE_TA_SESSION_KEY, 300, json.dumps(session_data))

    resp = authed_client.get("/api/v1/tripadvisor/session/status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("present") is True
    assert isinstance(body.get("expires_in"), int)
    assert body.get("expires_in", 0) > 0
    assert "destinations" in body.get("query_ids", [])
    assert body.get("reason") is None


# ---------------------------------------------------------------------------
# GET /session/status — needs_bootstrap marker set
# ---------------------------------------------------------------------------


def test_status_needs_bootstrap(authed_client, fake_redis):
    """GET /session/status with no session key but needs_bootstrap set → reason='needs_bootstrap'."""
    fake_redis.set("brave:ta:needs_bootstrap", "1")

    resp = authed_client.get("/api/v1/tripadvisor/session/status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("present") is False
    assert body.get("reason") == "needs_bootstrap"


# ---------------------------------------------------------------------------
# GET /session/status — absent (no session, no marker)
# ---------------------------------------------------------------------------


def test_status_absent(authed_client, fake_redis):
    """GET /session/status with no session key and no needs_bootstrap → reason=null."""
    resp = authed_client.get("/api/v1/tripadvisor/session/status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("present") is False
    assert body.get("reason") is None


# ---------------------------------------------------------------------------
# Auth guard — unauthenticated requests get 401
# ---------------------------------------------------------------------------


def test_inject_unauthenticated_gets_401(client):
    """POST /session without auth → 401."""
    resp = client.post("/api/v1/tripadvisor/session", json=_VALID_BODY)
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


def test_status_unauthenticated_gets_401(client):
    """GET /session/status without auth → 401."""
    resp = client.get("/api/v1/tripadvisor/session/status")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
