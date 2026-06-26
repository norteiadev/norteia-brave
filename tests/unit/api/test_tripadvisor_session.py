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
# Phase 13: session_id derivation tests (BLOCKER-2 fix)
# ---------------------------------------------------------------------------


def test_inject_session_stores_session_id(authed_client, fake_redis, monkeypatch):
    """POST without session_id field but with TASID cookie → session_id auto-derived."""
    import json
    import brave.api.routers.tripadvisor_session as ts_module
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

    async def _noop_canary(session, ta_config, redis):
        pass

    monkeypatch.setattr(ts_module, "_run_canary", _noop_canary)

    body = {
        "cookies": {"datadome": "x", "TASID": "E75FBE95"},
        "query_ids": {"destinations": "abc123def456abcd"},
        "user_agent": "Mozilla/5.0",
        "acquired_at": "2026-06-24T12:00:00Z",
        # NOTE: no session_id field — must be auto-derived from cookies["TASID"]
    }
    resp = authed_client.post("/api/v1/tripadvisor/session", json=body)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    raw = fake_redis.get(BRAVE_TA_SESSION_KEY)
    assert raw is not None, "Redis key must be set"
    stored = json.loads(raw)
    assert stored["session_id"] == "E75FBE95", (
        f"session_id must be auto-derived from TASID cookie; got: {stored.get('session_id')!r}"
    )


def test_inject_session_explicit_session_id_wins(authed_client, fake_redis, monkeypatch):
    """POST with explicit session_id wins over TASID cookie value."""
    import json
    import brave.api.routers.tripadvisor_session as ts_module
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

    async def _noop_canary(session, ta_config, redis):
        pass

    monkeypatch.setattr(ts_module, "_run_canary", _noop_canary)

    body = {
        "cookies": {"datadome": "x", "TASID": "COOKIE_VALUE"},
        "query_ids": {"destinations": "abc123def456abcd"},
        "user_agent": "Mozilla/5.0",
        "acquired_at": "2026-06-24T12:00:00Z",
        "session_id": "EXPLICIT",  # explicit field must take precedence
    }
    resp = authed_client.post("/api/v1/tripadvisor/session", json=body)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    raw = fake_redis.get(BRAVE_TA_SESSION_KEY)
    assert raw is not None, "Redis key must be set"
    stored = json.loads(raw)
    assert stored["session_id"] == "EXPLICIT", (
        f"Explicit session_id must take precedence over TASID cookie; got: {stored.get('session_id')!r}"
    )


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


# ---------------------------------------------------------------------------
# Plan 15-03: GET /api/v1/tripadvisor/sweep/progress
# ---------------------------------------------------------------------------


def test_sweep_progress_idle_when_no_run(authed_client, fake_redis):
    """GET /sweep/progress with no run → state=idle + zeroed counters."""
    resp = authed_client.get("/api/v1/tripadvisor/sweep/progress")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["state"] == "idle"
    assert body["pages_done"] == 0
    assert body["pages_total"] == 0
    assert body["attractions_ingested"] == 0
    assert body["current_offset"] == 0
    assert body["error_count"] == 0
    assert body["started_at"] is None


def test_sweep_progress_running_snapshot(authed_client, fake_redis):
    """GET /sweep/progress after a seeded running sweep → live counters."""
    from brave.lanes.tripadvisor import sweep_progress

    sweep_progress.start(fake_redis, pages_total=334)
    sweep_progress.record_page(fake_redis, offset=30, ingested_delta=30)

    resp = authed_client.get("/api/v1/tripadvisor/sweep/progress")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["state"] == "running"
    assert body["pages_done"] == 1
    assert body["pages_total"] == 334
    assert body["attractions_ingested"] == 30
    assert body["current_offset"] == 30
    assert body["error_count"] == 0
    assert body["started_at"] is not None


def test_sweep_progress_no_secret_fields(authed_client, fake_redis):
    """The progress response must carry no cookie/session/datadome field (T-15-03-02)."""
    from brave.lanes.tripadvisor import sweep_progress

    sweep_progress.start(fake_redis, pages_total=334)
    sweep_progress.record_page(fake_redis, offset=30, ingested_delta=30)

    resp = authed_client.get("/api/v1/tripadvisor/sweep/progress")
    assert resp.status_code == 200
    body = resp.json()
    forbidden = {"cookies", "cookie", "session", "session_id", "datadome", "proxy", "user_agent", "query_ids"}
    assert set(body).isdisjoint(forbidden), (
        f"secret-bearing fields leaked into progress response: {set(body) & forbidden}"
    )


def test_sweep_progress_unauthenticated_gets_401(client):
    """GET /sweep/progress without auth → 401 (fail-closed, mirrors session_status)."""
    resp = client.get("/api/v1/tripadvisor/sweep/progress")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# WR-02: canary distinguishes a provably-bad session from an infra fault.
# These exercise the REAL _run_canary (not the monkeypatched stub) by forcing
# the internally-constructed TripAdvisorClient.fetch_destinations to raise.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canary_infra_error_returns_503_and_keeps_key(fake_redis, monkeypatch):
    """WR-02: an infra fault (e.g. unknown geoId ValueError) → 503 canary_unverified,
    and the freshly-injected session key is NOT deleted."""
    import json

    from fastapi import HTTPException

    import brave.api.routers.tripadvisor_session as ts_module
    from brave.config.settings import TripAdvisorConfig
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

    session = {"cookies": {"datadome": "x"}, "query_ids": {"destinations": "qid"}}
    fake_redis.set(BRAVE_TA_SESSION_KEY, json.dumps(session))

    async def _raise_infra(self, geo_id, max_pages=None):
        raise ValueError("unknown geoId / infra fault")

    monkeypatch.setattr(TripAdvisorClient, "fetch_attractions", _raise_infra)

    with pytest.raises(HTTPException) as ei:
        await ts_module._run_canary(session, TripAdvisorConfig(), fake_redis)

    assert ei.value.status_code == 503
    assert ei.value.detail == "canary_unverified"
    assert fake_redis.exists(BRAVE_TA_SESSION_KEY), (
        "infra fault must NOT destroy a possibly-valid session key (WR-02)"
    )


@pytest.mark.asyncio
async def test_canary_session_expired_returns_422_and_deletes_key(fake_redis, monkeypatch):
    """WR-02 complement: a provably-bad session (SessionExpiredError) → 422
    invalid_session, key deleted."""
    import json

    from fastapi import HTTPException

    import brave.api.routers.tripadvisor_session as ts_module
    from brave.config.settings import TripAdvisorConfig
    from brave.lanes.tripadvisor.client import (
        BRAVE_TA_SESSION_KEY,
        SessionExpiredError,
        TripAdvisorClient,
    )

    session = {"cookies": {"datadome": "x"}, "query_ids": {"destinations": "qid"}}
    fake_redis.set(BRAVE_TA_SESSION_KEY, json.dumps(session))

    async def _raise_expired(self, geo_id, max_pages=None):
        raise SessionExpiredError("403 DataDome block")

    monkeypatch.setattr(TripAdvisorClient, "fetch_attractions", _raise_expired)

    with pytest.raises(HTTPException) as ei:
        await ts_module._run_canary(session, TripAdvisorConfig(), fake_redis)

    assert ei.value.status_code == 422
    assert ei.value.detail == "invalid_session"
    assert not fake_redis.exists(BRAVE_TA_SESSION_KEY), (
        "provably-bad session key must be deleted (fail closed)"
    )


# ---------------------------------------------------------------------------
# Phase 13-02: canary probes fetch_attractions (not fetch_destinations)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canary_probes_fetch_attractions(fake_redis, monkeypatch):
    """Canary must call fetch_attractions (qid a5cb7fa004b5e4b5), never fetch_destinations.

    Monkeypatches fetch_destinations to raise AssertionError — if canary accidentally
    calls it the test fails immediately. fetch_attractions returns a non-empty list
    to simulate a valid session response.
    """
    import json

    import brave.api.routers.tripadvisor_session as ts_module
    from brave.config.settings import TripAdvisorConfig
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, TripAdvisorClient

    session = {
        "cookies": {"datadome": "x", "TASID": "E75FBE95"},
        "query_ids": {"attractions": "a5cb7fa004b5e4b5"},
    }
    fake_redis.set(BRAVE_TA_SESSION_KEY, json.dumps(session))

    fetch_attractions_called_with: list[dict] = []

    async def _record_and_return(self, geo_id, max_pages=None):
        fetch_attractions_called_with.append({"geo_id": geo_id, "max_pages": max_pages})
        return [
            {
                "name": "Iguazu Falls",
                "locationId": 312332,
                "rating": 4.9,
                "review_count": 45811,
                "category": "Waterfalls",
            }
        ]

    async def _raise_if_called(self, uf, max_pages=None):
        raise AssertionError("canary must not call fetch_destinations — use fetch_attractions")

    monkeypatch.setattr(TripAdvisorClient, "fetch_attractions", _record_and_return)
    monkeypatch.setattr(TripAdvisorClient, "fetch_destinations", _raise_if_called)

    # Should complete without raising (valid non-empty session)
    await ts_module._run_canary(session, TripAdvisorConfig(), fake_redis)

    assert len(fetch_attractions_called_with) == 1, "fetch_attractions must be called exactly once"
    assert fetch_attractions_called_with[0]["geo_id"] == 303380, (
        f"Canary must probe geo_id=303380 (Minas Gerais); got: {fetch_attractions_called_with[0]['geo_id']}"
    )
    assert fetch_attractions_called_with[0]["max_pages"] == 1, (
        f"Canary must use max_pages=1; got: {fetch_attractions_called_with[0]['max_pages']}"
    )
