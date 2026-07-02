"""Offline tests for the Motor Pausado card edit-lock (phase C).

Covers:
  - require_editing_unlocked (deps.py): 423 while mode LIGADO, no-op while
    PAUSADO/DESLIGADO — exercised directly and through the HTTP layer.
  - The four LOCK-gated mutation endpoints return 423 when LIGADO and reach their
    handler (404 on the empty mock DB — i.e. PAST the lock) when PAUSADO.
  - Auth runs BEFORE the lock: an unauthenticated request to a locked endpoint is
    401, never a 423 that would leak lock state.
  - The gate endpoints (promote / descarte) are NOT lock-gated — they keep working
    while the engine is LIGADO.
  - POST /api/v1/engine/mode sets the mode; invalid → 422; DESLIGADO also idles the
    engine + clears the enabled latch; GET /status surfaces mode + editing_unlocked.

100% offline: fakeredis + a stub DB session (no Postgres, no Celery, no network).
The lock sits entirely before the DB layer, so 423 needs no DB and PAUSADO surfaces
a 404 (record-not-found) rather than a 200 — which cleanly proves the request got
past the lock without needing seeded data. A genuine 200-under-PAUSADO path (real
record) is covered by the integration advance tests in tests/test_cms_endpoints.py.
"""

from __future__ import annotations

import os
import uuid

import fakeredis
import pytest
from fastapi import HTTPException

# fakeredis must be active before the app imports resolve get_redis.
os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

BEARER = "test-bearer-token-editing-lock"
STEWARD = "test-steward-secret-editing-lock"
STEWARD_HEADERS = {"X-Steward-Secret": STEWARD}
BEARER_HEADERS = {"Authorization": f"Bearer {BEARER}"}

# The four LOCK-gated endpoints, with a valid body each. `path` carries a {rid}
# placeholder filled per-test. Bodies are valid so that, once past the lock, the
# handler runs its real record lookup (→ 404 on the empty stub DB).
LOCKED_ENDPOINTS = [
    ("/api/v1/destinos/{rid}/edit", {"fields": {"name": "x"}}),
    ("/api/v1/atrativos/{rid}/edit", {"fields": {"name": "x"}}),
    ("/api/v1/destinos/{rid}/transition", {"to": "mar", "expected": "rio"}),
    (
        "/api/v1/atrativos/{rid}/advance",
        {"expected_state": "discovered", "next_state": "contacts_found"},
    ),
]

# The gate approve/reject endpoints that must STAY usable while editing is locked.
GATE_ENDPOINTS = [
    "/api/v1/destinos/{rid}/promote",
    "/api/v1/destinos/{rid}/descarte",
    "/api/v1/atrativos/{rid}/descarte",
]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD)
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")


class _EmptyResult:
    def all(self):
        return []


class _StubSession:
    """Minimal Session stand-in: every record lookup misses (→ 404 past the lock)
    and the /status pipeline-count aggregates return empties (→ 200).

    Phase D: POST /engine/mode now persists the mode to config_settings via
    ``engine.set_mode(session=db)`` → ``upsert_config`` (session.get/add/flush). The
    no-op add/flush below let that durable write run without a real DB while keeping
    every edit-lock assertion unchanged (get still misses → 404 past the lock; the
    mode still round-trips through Redis, which is what these tests assert)."""

    def get(self, *a, **k):
        return None

    def scalar(self, *a, **k):
        return 0

    def execute(self, *a, **k):
        return _EmptyResult()

    def add(self, *a, **k):
        pass

    def flush(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from brave.api.deps import get_db, get_redis

    get_redis().flushall()  # fresh mode → LIGADO default (locked) per test

    from brave.api.main import app

    app.dependency_overrides[get_db] = lambda: _StubSession()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _rc():
    from brave.api.deps import get_redis

    return get_redis()


# ---------------------------------------------------------------------------
# require_editing_unlocked — direct dependency behavior
# ---------------------------------------------------------------------------


def test_require_editing_unlocked_raises_423_when_ligado():
    from brave.api import deps

    rc = fakeredis.FakeRedis()  # absent mode key → LIGADO default
    with pytest.raises(HTTPException) as exc:
        deps.require_editing_unlocked(rc)
    assert exc.value.status_code == 423


@pytest.mark.parametrize("mode", ["PAUSADO", "DESLIGADO"])
def test_require_editing_unlocked_noop_when_unlocked(mode):
    from brave.api import deps
    from brave.core import engine as collection_engine

    rc = fakeredis.FakeRedis()
    collection_engine.set_mode(rc, mode)
    assert deps.require_editing_unlocked(rc) is None


# ---------------------------------------------------------------------------
# The four locked endpoints — 423 when LIGADO, past-lock when PAUSADO
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path_tmpl,body", LOCKED_ENDPOINTS)
def test_locked_endpoint_returns_423_when_ligado(client, path_tmpl, body):
    """Default mode is LIGADO → every card mutation is 423 Locked (before any DB)."""
    path = path_tmpl.format(rid=uuid.uuid4())
    r = client.patch(path, headers=STEWARD_HEADERS, json=body)
    assert r.status_code == 423, f"{path}: expected 423, got {r.status_code}: {r.text}"


@pytest.mark.parametrize("path_tmpl,body", LOCKED_ENDPOINTS)
def test_locked_endpoint_allowed_when_pausado(client, path_tmpl, body):
    """PAUSADO releases the lock → the request reaches the handler (404 on stub DB),
    i.e. it is NOT blocked with a 423."""
    from brave.core import engine as collection_engine

    collection_engine.set_mode(_rc(), collection_engine.PAUSADO)
    path = path_tmpl.format(rid=uuid.uuid4())
    r = client.patch(path, headers=STEWARD_HEADERS, json=body)
    assert r.status_code != 423, f"{path}: still locked under PAUSADO ({r.text})"
    assert r.status_code == 404, f"{path}: expected past-lock 404, got {r.status_code}"


@pytest.mark.parametrize("path_tmpl,body", LOCKED_ENDPOINTS)
def test_locked_endpoint_allowed_when_desligado(client, path_tmpl, body):
    """DESLIGADO also releases the lock (editing_unlocked is PAUSADO or DESLIGADO)."""
    from brave.core import engine as collection_engine

    collection_engine.set_mode(_rc(), collection_engine.DESLIGADO)
    path = path_tmpl.format(rid=uuid.uuid4())
    r = client.patch(path, headers=STEWARD_HEADERS, json=body)
    assert r.status_code != 423, f"{path}: still locked under DESLIGADO ({r.text})"
    assert r.status_code == 404, f"{path}: expected past-lock 404, got {r.status_code}"


@pytest.mark.parametrize("path_tmpl,body", LOCKED_ENDPOINTS)
def test_locked_endpoint_unauthenticated_is_401_not_423(client, path_tmpl, body):
    """Auth-before-lock: no credentials → 401, never a 423 that leaks lock state.

    Mode is LIGADO (locked) here, so a lock-first ordering would return 423.
    """
    path = path_tmpl.format(rid=uuid.uuid4())
    r = client.patch(path, json=body)  # no auth headers
    assert r.status_code == 401, f"{path}: expected 401, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# Gate approve/reject endpoints stay usable while editing is LOCKED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path_tmpl", GATE_ENDPOINTS)
def test_gate_endpoints_not_locked_while_ligado(client, path_tmpl):
    """promote / descarte are the DLQ gate actions — they must NOT be edit-locked.

    Mode is LIGADO (the default), so a mistakenly-attached lock would 423. Instead
    the request reaches the handler and 404s on the empty stub DB (past the lock).
    """
    path = path_tmpl.format(rid=uuid.uuid4())
    r = client.patch(path, headers=STEWARD_HEADERS)
    assert r.status_code != 423, f"{path}: gate endpoint must not be edit-locked"
    assert r.status_code == 404, f"{path}: expected past-lock 404, got {r.status_code}"


# ---------------------------------------------------------------------------
# POST /api/v1/engine/mode — operator mode control
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,unlocked",
    [("LIGADO", False), ("PAUSADO", True), ("DESLIGADO", True)],
)
def test_set_mode_endpoint_persists_and_echoes(client, mode, unlocked):
    from brave.core import engine as collection_engine

    r = client.post("/api/v1/engine/mode", headers=STEWARD_HEADERS, json={"mode": mode})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == mode
    assert body["editing_unlocked"] is unlocked
    assert collection_engine.get_mode(_rc()) == mode


def test_set_mode_endpoint_rejects_invalid(client):
    from brave.core import engine as collection_engine

    r = client.post(
        "/api/v1/engine/mode", headers=STEWARD_HEADERS, json={"mode": "bogus"}
    )
    assert r.status_code == 422, r.text
    # No write on the invalid value → still the LIGADO default.
    assert collection_engine.get_mode(_rc()) == collection_engine.LIGADO


def test_set_mode_endpoint_requires_auth(client):
    r = client.post("/api/v1/engine/mode", json={"mode": "PAUSADO"})
    assert r.status_code == 401


def test_set_mode_desligado_idles_engine_and_clears_enabled(client):
    """DESLIGADO via the endpoint runs engine.set_mode's hard-off side effects."""
    from brave.core import engine as collection_engine

    rc = _rc()
    collection_engine.start_run(rc, ufs_total=2)
    assert collection_engine.is_enabled(rc) is True
    assert collection_engine.get_state(rc) == collection_engine.RUNNING

    r = client.post(
        "/api/v1/engine/mode", headers=STEWARD_HEADERS, json={"mode": "DESLIGADO"}
    )
    assert r.status_code == 200, r.text
    assert collection_engine.is_enabled(rc) is False
    assert collection_engine.get_state(rc) == collection_engine.IDLE


def test_set_mode_pausado_does_not_clear_enabled(client):
    """PAUSADO leaves the runtime + enabled latch intact (drain, not stop)."""
    from brave.core import engine as collection_engine

    rc = _rc()
    collection_engine.start_run(rc, ufs_total=2)

    r = client.post(
        "/api/v1/engine/mode", headers=STEWARD_HEADERS, json={"mode": "PAUSADO"}
    )
    assert r.status_code == 200, r.text
    assert collection_engine.is_enabled(rc) is True
    assert collection_engine.get_state(rc) == collection_engine.RUNNING


# ---------------------------------------------------------------------------
# GET /api/v1/engine/status surfaces mode + editing_unlocked
# ---------------------------------------------------------------------------


def test_status_surfaces_mode_and_editing_unlocked(client):
    r = client.get("/api/v1/engine/status", headers=BEARER_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "LIGADO"  # default
    assert body["editing_unlocked"] is False

    from brave.core import engine as collection_engine

    collection_engine.set_mode(_rc(), collection_engine.PAUSADO)
    body = client.get("/api/v1/engine/status", headers=BEARER_HEADERS).json()
    assert body["mode"] == "PAUSADO"
    assert body["editing_unlocked"] is True
