"""GET /api/v1/cost exposes the Apify spend + caps (places_provider="hybrid"). Offline."""

from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi.testclient import TestClient

BEARER = "test-cost-apify-bearer"


def test_cost_reports_apify_spend_and_caps(monkeypatch):
    from brave.api.deps import get_db, get_redis
    from brave.api.main import app
    from brave.observability.cost_guard import record_apify_spend, record_places_spend

    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("APIFY_DAILY_USD_CAP", "2.5")
    monkeypatch.setenv("APIFY_TOTAL_USD_CAP", "150")
    redis = fakeredis.FakeRedis()
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    record_apify_spend(redis, 0.0065)
    record_places_spend(redis, 1.0, "")  # Places spend must not leak into Apify's figure
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get("/api/v1/cost", headers={"Authorization": f"Bearer {BEARER}"})
    finally:
        app.dependency_overrides.pop(get_redis, None)
        app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 200, r.text
    assert r.json()["apify"] == {
        "usd_today": pytest.approx(0.0065),
        "daily_cap_usd": 2.5,
        "usd_total": pytest.approx(0.0065),
        "total_cap_usd": 150.0,
    }
