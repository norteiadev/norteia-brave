"""Integration tests for the optional ops-trigger endpoint POST /api/v1/sweep.

ORCH-03 / ORCH-04, D-05, security (T-05-07/T-05-09).

The endpoint is the D-05 nice-to-have manual fan-out surface. It MUST be
Bearer-guarded so an unauthenticated caller cannot fan out expensive LLM/Places
sweeps (T-05-07). It only kicks the existing producer/chain tasks — it never
auto-validates, never bypasses the reliability gate, and never reaches the WhatsApp send
path (T-05-09).

These tests are 100% offline / keyless (D-06 / ORCH-04):
  - run_real_externals defaults to False → the broker error is swallowed and the
    task would run inline; here discover_atrativo_task.delay is monkeypatched with a
    spy so neither a broker nor a DB is required.
  - outreach_task.delay is spied to assert the endpoint NEVER triggers outreach.
"""

import os

import pytest
from fastapi.testclient import TestClient

# Auth secrets (set before app import so deps read them; mirrors gate tests).
DASHBOARD_BEARER = "test-sweep-endpoint-dashboard-bearer"
os.environ.setdefault("BRAVE_DASHBOARD_BEARER_TOKEN", DASHBOARD_BEARER)
STEWARD_SECRET = "test-sweep-endpoint-steward-secret"
os.environ.setdefault("BRAVE_STEWARD_SECRET", STEWARD_SECRET)

BEARER_HEADERS = {"Authorization": f"Bearer {DASHBOARD_BEARER}"}


@pytest.fixture(scope="module")
def client():
    """Bare FastAPI TestClient (no default auth header — 401 tests need it bare)."""
    os.environ["BRAVE_DASHBOARD_BEARER_TOKEN"] = DASHBOARD_BEARER
    os.environ["BRAVE_STEWARD_SECRET"] = STEWARD_SECRET
    from brave.api.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def spies(monkeypatch):
    """Spy discover_atrativo_task .delay and outreach_task .delay (no broker/DB)."""
    from brave.tasks import pipeline

    calls = {"atrativo": [], "outreach": []}
    monkeypatch.setattr(
        pipeline.discover_atrativo_task, "delay", lambda uf: calls["atrativo"].append(uf)
    )
    monkeypatch.setattr(
        pipeline.outreach_task, "delay", lambda rio_id: calls["outreach"].append(rio_id)
    )
    return calls


# ---------------------------------------------------------------------------
# Auth (T-05-07): unauthenticated callers cannot fan out sweeps
# ---------------------------------------------------------------------------


def test_sweep_without_bearer_returns_401(client, spies):
    """POST /api/v1/sweep with no Authorization header → 401 (T-05-07).

    No task is dispatched — the auth gate fails closed before any fan-out.
    """
    r = client.post("/api/v1/sweep", params={"uf": "BA"})
    assert r.status_code == 401, f"Expected 401 without Bearer, got {r.status_code}: {r.text}"
    assert spies["atrativo"] == []


def test_sweep_with_invalid_bearer_returns_401(client, spies):
    """An invalid Bearer token → 401, nothing dispatched."""
    r = client.post(
        "/api/v1/sweep",
        params={"uf": "BA"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401
    assert spies["atrativo"] == []


# ---------------------------------------------------------------------------
# Authorized fan-out (202) + lane routing
# ---------------------------------------------------------------------------


def test_sweep_with_bearer_returns_202_atrativos(client, spies):
    """A valid Bearer → 202 and dispatches discover_atrativo_task (destinos has no producer)."""
    r = client.post("/api/v1/sweep", params={"uf": "BA"}, headers=BEARER_HEADERS)
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["status"] == "accepted"
    assert body["uf"] == "BA"
    assert spies["atrativo"] == ["BA"]


def test_sweep_lane_destinos_dispatches_nothing(client, spies):
    """`lane=destinos` dispatches nothing (Mtur seed retired; destinos come from the DB)."""
    r = client.post(
        "/api/v1/sweep", params={"uf": "BA", "lane": "destinos"}, headers=BEARER_HEADERS
    )
    assert r.status_code == 202
    assert spies["atrativo"] == []


def test_sweep_lane_atrativos_only(client, spies):
    """`lane=atrativos` dispatches only discover_atrativo_task."""
    r = client.post(
        "/api/v1/sweep", params={"uf": "BA", "lane": "atrativos"}, headers=BEARER_HEADERS
    )
    assert r.status_code == 202
    assert spies["atrativo"] == ["BA"]


def test_sweep_uf_uppercased(client, spies):
    """A lowercase uf is normalized to uppercase."""
    r = client.post("/api/v1/sweep", params={"uf": "ba"}, headers=BEARER_HEADERS)
    assert r.status_code == 202
    assert r.json()["uf"] == "BA"
    assert spies["atrativo"] == ["BA"]


def test_sweep_unknown_lane_returns_422(client, spies):
    """An unknown lane value → 422 (validation), nothing dispatched."""
    r = client.post(
        "/api/v1/sweep", params={"uf": "BA", "lane": "bogus"}, headers=BEARER_HEADERS
    )
    assert r.status_code == 422
    assert spies["atrativo"] == []


# ---------------------------------------------------------------------------
# No gate/send bypass (T-05-09)
# ---------------------------------------------------------------------------


def test_sweep_never_triggers_outreach(client, spies):
    """The endpoint only kicks producer/chain tasks — it NEVER dispatches outreach (T-05-09)."""
    r = client.post("/api/v1/sweep", params={"uf": "BA"}, headers=BEARER_HEADERS)
    assert r.status_code == 202
    assert spies["outreach"] == [], "ops trigger must not reach the WhatsApp send path"
