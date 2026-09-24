"""Offline tests for the Phase D config surface (GET/PATCH /api/v1/config).

Fully offline: fakeredis + an in-memory ``config_settings`` fake session (no Postgres,
no broker, no network). Mirrors the direct-function call style of
tests/unit/test_runs_write_path.py plus a few HTTP-level wiring/auth checks.

Covers (task #5):
  - GET returns the effective snapshot (env defaults + overlay), secrets redacted;
  - PATCH overlays a value and the effective snapshot round-trips it back;
  - reliability weight-sum-100 validation (422 on a single-weight edit that breaks the sum);
  - threshold bounds (422 on out-of-range) + unknown-key rejection;
  - an AuditLog row (action='config_updated', actor='steward') is written;
  - the PATCH echo is recomputed from the DB (a poisoned overlay cache is not served).
"""

from __future__ import annotations

import json
import os

import fakeredis
import pytest
from fastapi import HTTPException

os.environ.setdefault("BRAVE_USE_FAKEREDIS", "1")

from brave.api.routers.config import get_config_snapshot, update_config  # noqa: E402
from brave.config.runtime import OVERLAY_KEY  # noqa: E402
from brave.core.models import AuditLog, ConfigSetting  # noqa: E402

BEARER = "test-bearer-config"
STEWARD = "test-steward-config"
BEARER_HEADERS = {"Authorization": f"Bearer {BEARER}"}
STEWARD_HEADERS = {"X-Steward-Secret": STEWARD}


# ---------------------------------------------------------------------------
# In-memory config_settings fake session
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: dict[str, ConfigSetting]):
        self._rows = rows

    def all(self):
        # Matches runtime._read_overlay_rows: select(ConfigSetting.key, .value)
        return [(k, r.value) for k, r in self._rows.items()]

    def scalars(self):
        return list(self._rows.keys())


class FakeConfigSession:
    """Functional in-memory stand-in supporting the ORM ops the config path uses."""

    def __init__(self):
        self.rows: dict[str, ConfigSetting] = {}
        self.audits: list[AuditLog] = []
        self.commits = 0

    def get(self, _model, key):
        return self.rows.get(key)

    def add(self, obj):
        if isinstance(obj, ConfigSetting):
            self.rows[obj.key] = obj
        elif isinstance(obj, AuditLog):
            self.audits.append(obj)

    def execute(self, _stmt):
        return _FakeResult(self.rows)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def db():
    return FakeConfigSession()


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


# ---------------------------------------------------------------------------
# GET — effective snapshot
# ---------------------------------------------------------------------------


def test_get_returns_effective_snapshot(db, redis):
    snap = get_config_snapshot(db=db, redis=redis)
    assert snap["score"]["threshold_mar"] == 80.0
    assert snap["score"]["weight_origem"] == 30.0
    # 'default' (Google Places) ships DORMANT — enabled=false by default (re-enablable
    # via config); tripadvisor is the live lane.
    assert snap["sources"] == {"default": False, "tripadvisor": True}
    # Clean base default is motor OFF (EngineConfig.mode default DESLIGADO — bug-1 fix,
    # commit a94eec4). db here is an EMPTY FakeConfigSession, so the snapshot is the pure
    # env default with no overlay.
    assert snap["engine"]["mode"] == "DESLIGADO"


def test_get_redacts_secrets(db, redis, monkeypatch):
    monkeypatch.setenv("BRAVE_LLM_OPENROUTER_API_KEY", "sk-super-secret")
    monkeypatch.setenv("BRAVE_LLM_GEMINI_API_KEY", "gm-super-secret")
    snap = get_config_snapshot(db=db, redis=redis)
    assert snap["llm"]["openrouter_api_key"] == "***"  # never echoed verbatim
    assert snap["llm"]["gemini_api_key"] == "***"


def test_get_reflects_an_existing_overlay_row(db, redis):
    # A pre-seeded overlay row must surface in the effective snapshot.
    db.rows["score.threshold_mar"] = ConfigSetting(
        key="score.threshold_mar", value={"v": 90.0}
    )
    snap = get_config_snapshot(db=db, redis=redis)
    assert snap["score"]["threshold_mar"] == 90.0


# ---------------------------------------------------------------------------
# PATCH — overlay + round-trip + audit + cache-bust
# ---------------------------------------------------------------------------


def test_patch_overlays_and_round_trips(db, redis):
    out = update_config(body={"score.threshold_mar": 85.0}, db=db)
    assert "score.threshold_mar" in out["updated"]
    assert out["config"]["score"]["threshold_mar"] == 85.0
    # Row persisted under the {"v": ...} wrapper.
    assert db.rows["score.threshold_mar"].value == {"v": 85.0}
    # A subsequent GET reflects it.
    assert get_config_snapshot(db=db, redis=redis)["score"]["threshold_mar"] == 85.0


def test_patch_writes_audit_row(db, redis):
    update_config(body={"score.threshold_mar": 82.0}, db=db)
    assert len(db.audits) == 1
    audit = db.audits[0]
    assert audit.action == "config_updated"
    assert audit.actor == "steward"
    assert audit.after_state == {"score.threshold_mar": 82.0}
    # before_state captures the prior effective value (the env default).
    assert audit.before_state == {"score.threshold_mar": 80.0}
    assert db.commits == 1  # committed before the cache-bust side effect


def test_patch_response_is_read_from_the_db_not_the_cache(db, redis):
    # Poison the overlay cache with a VALID but wrong row (threshold 999).
    redis.set(OVERLAY_KEY, json.dumps({"score.threshold_mar": 999.0}))
    # Served from cache before the write.
    assert get_config_snapshot(db=db, redis=redis)["score"]["threshold_mar"] == 999.0

    out = update_config(body={"score.threshold_mar": 77.0}, db=db)

    # The PATCH echo recomputes from the DB. Dropping the cache on commit is the
    # after_commit listener's job (tests/unit/test_config_overlay_cache.py).
    assert out["config"]["score"]["threshold_mar"] == 77.0


def test_patch_toggles_source_enabled(db, redis):
    out = update_config(
        body={"source.tripadvisor.enabled": False}, db=db
    )
    assert out["config"]["sources"]["tripadvisor"] is False
    assert db.rows["source.tripadvisor.enabled"].value == {"v": False}


def test_patch_toggles_description_enrichment(db, redis):
    out = update_config(
        body={"description_enrichment_enabled": False}, db=db
    )
    # The overlay flows through load_effective_config into the returned snapshot.
    assert out["config"]["description_enrichment_enabled"] is False
    assert db.rows["description_enrichment_enabled"].value == {"v": False}


def test_patch_rejects_non_bool_description_enrichment(db, redis):
    with pytest.raises(HTTPException) as exc:
        update_config(
            body={"description_enrichment_enabled": "yes"}, db=db
        )
    assert exc.value.status_code == 422


def test_patch_toggles_atrativo_description_batch(db, redis):
    # Defaults OFF: the inline copywriter path is today's behavior until flipped.
    snap = get_config_snapshot(db=db, redis=redis)
    assert snap["atrativo_description_batch_enabled"] is False

    out = update_config(
        body={"atrativo_description_batch_enabled": True}, db=db
    )
    assert out["config"]["atrativo_description_batch_enabled"] is True
    assert db.rows["atrativo_description_batch_enabled"].value == {"v": True}


def test_patch_rejects_non_bool_atrativo_description_batch(db, redis):
    with pytest.raises(HTTPException) as exc:
        update_config(
            body={"atrativo_description_batch_enabled": 1}, db=db
        )
    assert exc.value.status_code == 422


def test_patch_accepts_weight_set_summing_100(db, redis):
    # origem 40 + completude 10 + (defaults) corroboracao 20 + atualidade 15 +
    # validacao_humana 15 == 100.
    out = update_config(
        body={"score.weight_origem": 40.0, "score.weight_completude": 10.0},
        db=db,
    )
    assert out["config"]["score"]["weight_origem"] == 40.0
    assert out["config"]["score"]["weight_completude"] == 10.0


# ---------------------------------------------------------------------------
# PATCH — validation (422 before any write)
# ---------------------------------------------------------------------------


def test_patch_rejects_weight_sum_not_100(db, redis):
    # Touching a single weight to a value that breaks the sum-100 invariant → 422.
    with pytest.raises(HTTPException) as exc:
        update_config(body={"score.weight_origem": 40.0}, db=db)
    assert exc.value.status_code == 422
    assert db.rows == {}  # nothing written
    assert db.audits == []


def test_patch_rejects_threshold_out_of_bounds(db, redis):
    with pytest.raises(HTTPException) as exc:
        update_config(body={"score.threshold_mar": 150.0}, db=db)
    assert exc.value.status_code == 422
    assert db.rows == {}


def test_patch_rejects_negative_weight(db, redis):
    with pytest.raises(HTTPException) as exc:
        update_config(body={"score.weight_origem": -5.0}, db=db)
    assert exc.value.status_code == 422


def test_patch_rejects_unknown_key(db, redis):
    with pytest.raises(HTTPException) as exc:
        update_config(body={"score.bogus": 1.0}, db=db)
    assert exc.value.status_code == 422
    assert db.rows == {}


def test_patch_rejects_empty_body(db, redis):
    with pytest.raises(HTTPException) as exc:
        update_config(body={}, db=db)
    assert exc.value.status_code == 422


def test_patch_rejects_invalid_engine_mode(db, redis):
    with pytest.raises(HTTPException) as exc:
        update_config(body={"engine.mode": "bogus"}, db=db)
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# HTTP wiring / auth (router registered in main.py, deps fire)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", BEARER)
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD)
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")

    from fastapi.testclient import TestClient

    from brave.api.deps import get_db, get_redis
    from brave.api.main import app

    get_redis().flushall()
    shared = FakeConfigSession()
    app.dependency_overrides[get_db] = lambda: shared
    try:
        yield TestClient(app, raise_server_exceptions=False), shared
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_http_get_requires_bearer(client):
    tc, _ = client
    assert tc.get("/api/v1/config").status_code == 401


def test_http_get_returns_snapshot(client):
    tc, _ = client
    r = tc.get("/api/v1/config", headers=BEARER_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["score"]["threshold_mar"] == 80.0


def test_http_patch_requires_auth(client):
    tc, _ = client
    assert tc.patch("/api/v1/config", json={"score.threshold_mar": 81.0}).status_code == 401


def test_http_patch_422_on_bad_weight(client):
    tc, shared = client
    r = tc.patch(
        "/api/v1/config", headers=STEWARD_HEADERS, json={"score.weight_origem": 40.0}
    )
    assert r.status_code == 422, r.text
    assert shared.rows == {}


def test_http_patch_success(client):
    tc, shared = client
    r = tc.patch(
        "/api/v1/config", headers=STEWARD_HEADERS, json={"score.threshold_mar": 88.0}
    )
    assert r.status_code == 200, r.text
    assert r.json()["config"]["score"]["threshold_mar"] == 88.0
    assert shared.rows["score.threshold_mar"].value == {"v": 88.0}
    assert any(a.action == "config_updated" for a in shared.audits)


def test_patch_toggles_atrativo_description_cascade(db, redis, monkeypatch):
    # Defaults OFF, round-trips through the overlay — and the Tavily key never reaches the
    # snapshot (AppConfig excludes it from dumps), so GET cannot echo it.
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    monkeypatch.setenv("PARALLEL_API_KEY", "prl-secret")
    snap = get_config_snapshot(db=db, redis=redis)
    assert snap["atrativo_description_cascade_enabled"] is False
    assert "tavily_api_key" not in snap and "tvly-secret" not in str(snap)
    assert "parallel_api_key" not in snap and "prl-secret" not in str(snap)

    out = update_config(
        body={"atrativo_description_cascade_enabled": True}, db=db
    )
    assert out["config"]["atrativo_description_cascade_enabled"] is True
    assert db.rows["atrativo_description_cascade_enabled"].value == {"v": True}
