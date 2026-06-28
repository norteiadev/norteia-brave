"""Unit tests for engine source validation in POST /api/v1/engine/start (plan 11-03).

Tests the source validation added to the engine /start endpoint:
  - Invalid source returns 422 before touching engine state (T-11-03-03)
  - Valid source='tripadvisor' returns 202 with source echoed
  - GET /engine/status includes 'source' key
  - engine_sweep_run dispatches sweep_tripadvisor when source='tripadvisor'
  - engine_sweep_run dispatches sweep_uf when source='default' (no regression)
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

# Set up fakeredis before importing the app
os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

BEARER = "test-bearer-token-engine-source"
STEWARD = "test-steward-secret-engine-source"
BEARER_HEADERS = {"Authorization": f"Bearer {BEARER}"}
STEWARD_HEADERS = {"X-Steward-Secret": STEWARD}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD)
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")


@pytest.fixture
def dispatched():
    return {}


@pytest.fixture
def client(monkeypatch, dispatched):
    from brave.api.deps import get_db, get_redis
    get_redis().flushall()

    import brave.tasks.pipeline as pipeline

    def _capture(*args, **kwargs):
        dispatched.clear()
        dispatched.update(kwargs)
        return None

    monkeypatch.setattr(pipeline.engine_sweep_run, "delay", _capture)

    from brave.api.main import app
    from fastapi.testclient import TestClient

    # engine_start now persists a runs_history row via get_db. The offline suite
    # has no DB — override get_db with a no-op MagicMock session (the runs-history
    # write is best-effort and irrelevant to these source-validation assertions).
    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Source validation
# ---------------------------------------------------------------------------


def test_engine_start_invalid_source_returns_422(client):
    """POST /engine/start with source='invalid' → 422, no engine state change."""
    from brave.api.deps import get_redis
    from brave.core import engine as collection_engine

    resp = client.post(
        "/api/v1/engine/start",
        headers=STEWARD_HEADERS,
        json={"depth": "nascente", "source": "invalid"},
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    # Engine state must NOT be mutated — still idle
    rc = get_redis()
    assert collection_engine.get_state(rc) == collection_engine.IDLE


def test_engine_start_tripadvisor_source_returns_202(client, dispatched):
    """POST /engine/start with source='tripadvisor' + valid depth → 202 + source echoed."""
    resp = client.post(
        "/api/v1/engine/start",
        headers=STEWARD_HEADERS,
        json={"depth": "nascente", "source": "tripadvisor"},
    )
    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("source") == "tripadvisor"


def test_engine_start_default_source_returns_202(client, dispatched):
    """POST /engine/start with source='default' (or omitted) → 202 — no regression."""
    resp = client.post(
        "/api/v1/engine/start",
        headers=STEWARD_HEADERS,
        json={"depth": "nascente", "source": "default"},
    )
    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("source") == "default"


def test_engine_status_includes_source_key():
    """GET /engine/status includes 'source' key — tested via engine module (no HTTP)."""
    # Test the engine module directly (HTTP layer would need a DB for _pipeline_counts).
    import fakeredis
    from brave.core import engine as collection_engine

    rc = fakeredis.FakeRedis()
    status = collection_engine.get_status(rc)
    assert "source" in status
    assert status["source"] is None  # unset → None

    collection_engine.set_source(rc, "tripadvisor")
    assert collection_engine.get_status(rc)["source"] == "tripadvisor"


# ---------------------------------------------------------------------------
# engine_sweep_run source dispatch (unit — no HTTP, no DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def running_engine(monkeypatch):
    """Fakeredis with engine state=RUNNING and no per-UF delay."""
    from brave.core import engine as collection_engine
    fake = fakeredis.FakeStrictRedis()
    fake.set(collection_engine._STATE_KEY, collection_engine.RUNNING)
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    monkeypatch.setenv("BRAVE_ENGINE_UF_DELAY_SECONDS", "0")
    return fake


class _FakeTask:
    def __init__(self, sink):
        self._sink = sink

    def delay(self, *args, **kwargs):
        self._sink.append((args, kwargs))


def test_engine_sweep_run_tripadvisor_dispatches_sweep_tripadvisor(monkeypatch, running_engine):
    """engine_sweep_run(source='tripadvisor') dispatches sweep_tripadvisor.delay per UF."""
    from brave.tasks import pipeline
    from brave.core import engine as collection_engine

    ta_calls = []
    uf_calls = []
    discover_calls = []

    monkeypatch.setattr(pipeline, "sweep_tripadvisor", _FakeTask(ta_calls))
    monkeypatch.setattr(pipeline, "sweep_uf", _FakeTask(uf_calls))
    monkeypatch.setattr(pipeline, "discover_atrativo_task", _FakeTask(discover_calls))

    pipeline.engine_sweep_run.run(
        ufs=["BA"], lane="both", depth=collection_engine.NASCENTE_RIO, source="tripadvisor"
    )

    assert len(ta_calls) == 1, f"Expected 1 sweep_tripadvisor call, got {len(ta_calls)}"
    assert len(uf_calls) == 0, "sweep_uf must NOT be called when source=tripadvisor"
    assert len(discover_calls) == 0, "discover_atrativo_task must NOT be called when source=tripadvisor"


def test_engine_sweep_run_default_dispatches_sweep_uf(monkeypatch, running_engine):
    """engine_sweep_run(source='default') dispatches sweep_uf (no regression)."""
    from brave.tasks import pipeline
    from brave.core import engine as collection_engine

    ta_calls = []
    uf_calls = []
    discover_calls = []

    monkeypatch.setattr(pipeline, "sweep_tripadvisor", _FakeTask(ta_calls))
    monkeypatch.setattr(pipeline, "sweep_uf", _FakeTask(uf_calls))
    monkeypatch.setattr(pipeline, "discover_atrativo_task", _FakeTask(discover_calls))

    pipeline.engine_sweep_run.run(
        ufs=["BA"], lane="both", depth=collection_engine.NASCENTE_RIO, source="default"
    )

    assert len(uf_calls) == 1, f"Expected 1 sweep_uf call, got {len(uf_calls)}"
    assert len(ta_calls) == 0, "sweep_tripadvisor must NOT be called when source=default"
