"""GET /api/v1/cost exposes today's Google Places spend + cap (C4). Offline: fakeredis +
a mocked Session (no llm_generations rows)."""

from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi.testclient import TestClient

BEARER = "test-cost-places-bearer"


@pytest.fixture
def client(monkeypatch):
    from brave.api.deps import get_db, get_redis
    from brave.api.main import app

    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("PLACES_DAILY_USD_CAP", "120.5")
    redis = fakeredis.FakeRedis()
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app), redis
    finally:
        app.dependency_overrides.pop(get_redis, None)
        app.dependency_overrides.pop(get_db, None)


def test_cost_reports_places_spend_and_cap(client, monkeypatch):
    from brave.observability.cost_guard import places_key_id, record_places_spend, record_spend

    http, redis = client
    monkeypatch.setenv("BRAVE_PLACES_API_KEY", "key-b")
    monkeypatch.setenv("PLACES_TOTAL_USD_CAP", "250")
    record_places_spend(redis, 0.057, "key-b")
    record_places_spend(redis, 7.0, "key-a")  # the exhausted old key — not the current one
    record_spend(redis, 9.0)  # LLM counter — must not leak into the Places figure

    r = http.get("/api/v1/cost", headers={"Authorization": f"Bearer {BEARER}"})

    assert r.status_code == 200, r.text
    assert r.json()["places"] == {
        "key_id": places_key_id("key-b"),
        "usd_today": pytest.approx(0.057),
        "daily_cap_usd": 120.5,
        "usd_total": pytest.approx(0.057),
        "total_cap_usd": 250.0,
    }
