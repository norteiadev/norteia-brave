"""Unit tests for PATCH /api/v1/atrativos/{rio_id}/promote endpoint (plan 11-03).

Tests the promote-override gate:
  - 409 for non-mar_ready records (T-11-03-01)
  - 202 for mar_ready records with audit written
  - GET /mar-ready requires auth
  - PATCH requires auth

Uses FastAPI dependency_overrides to inject a mock DB session.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

BEARER = "test-bearer-token-promote"
STEWARD = "test-steward-secret-promote"
BEARER_HEADERS = {"Authorization": f"Bearer {BEARER}"}
STEWARD_HEADERS = {"X-Steward-Secret": STEWARD}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD)
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")


def _make_rio_mock(mar_ready: bool, routing: str = "dlq") -> MagicMock:
    rio = MagicMock()
    rio.id = uuid.uuid4()
    rio.mar_ready = mar_ready
    rio.routing = routing
    rio.entity_type = "attraction"
    rio.uf = "BA"
    rio.score = 67.05
    rio.canonical_key = f"tripadvisor:attraction:{rio.id}"
    return rio


@contextmanager
def _app_with_db(db_mock):
    """Context manager: override get_db with a mock session, yield TestClient."""
    from brave.api.main import app
    from brave.api.deps import get_db
    from fastapi.testclient import TestClient

    def _fake_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# PATCH /api/v1/atrativos/{rio_id}/promote
# ---------------------------------------------------------------------------


def test_promote_non_mar_ready_returns_409(_env):
    """PATCH /atrativos/{id}/promote with mar_ready=False → 409 Conflict."""
    rio = _make_rio_mock(mar_ready=False)
    rio_id = rio.id

    db = MagicMock()
    db.get.return_value = rio

    with _app_with_db(db) as client:
        resp = client.patch(
            f"/api/v1/atrativos/{rio_id}/promote",
            headers=STEWARD_HEADERS,
        )
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"


def test_promote_mar_ready_returns_202_and_writes_audit(_env):
    """PATCH /atrativos/{id}/promote with mar_ready=True → 202 + audit written."""
    rio = _make_rio_mock(mar_ready=True, routing="dlq")
    rio_id = rio.id

    audit_calls = []
    fake_mar = MagicMock()
    fake_mar.provenance = {"promotion_reason": "steward_override_review_validated"}

    db = MagicMock()
    db.get.return_value = rio

    with _app_with_db(db) as client:
        with (
            patch(
                "brave.api.routers.atrativos.promote_override",
                return_value=fake_mar,
            ),
            patch(
                "brave.api.routers.atrativos.write_audit",
                side_effect=lambda **kwargs: audit_calls.append(kwargs),
            ),
            patch(
                "brave.api.routers.atrativos.push_attraction_task_delay",
                side_effect=Exception("no broker"),
            ),
        ):
            resp = client.patch(
                f"/api/v1/atrativos/{rio_id}/promote",
                headers=STEWARD_HEADERS,
            )

    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    assert any(
        c.get("action") == "atrativo_promoted_override" for c in audit_calls
    ), f"Expected audit call with 'atrativo_promoted_override', got: {audit_calls}"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_promote_requires_auth(_env):
    """PATCH /atrativos/{id}/promote without auth → 401."""
    from brave.api.main import app
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.patch(f"/api/v1/atrativos/{uuid.uuid4()}/promote")
    assert resp.status_code == 401


def test_mar_ready_list_requires_auth(_env):
    """GET /atrativos/mar-ready without auth → 401."""
    from brave.api.main import app
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/v1/atrativos/mar-ready")
    assert resp.status_code == 401
