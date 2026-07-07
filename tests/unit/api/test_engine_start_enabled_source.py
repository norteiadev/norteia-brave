"""Phase D: POST /engine/start validates the source against REGISTERED and ENABLED.

The source must be a known lane in the effective config (registered) AND present in
enabled_sources (enabled). A disabled-but-registered lane is rejected with 409 before
any engine state mutation; an unknown lane stays a 422 (covered in test_engine_source).

Fully offline: fakeredis + a MagicMock db; the enabled set is driven by monkeypatching
the router's effective-config load so no DB is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi import HTTPException

from brave.api.routers import engine as engine_router
from brave.config.settings import AppConfig
from brave.core import engine as ce


def _config_with(sources: dict[str, bool]) -> AppConfig:
    return AppConfig().model_copy(update={"sources": sources})


def test_start_rejects_disabled_source_409(monkeypatch):
    """source='tripadvisor' while disabled in config → 409, engine stays idle."""
    cfg = _config_with({"default": True, "tripadvisor": False})
    monkeypatch.setattr(engine_router, "load_effective_config", lambda db, redis: cfg)

    fake = fakeredis.FakeStrictRedis()
    # Seed a valid TA session so a 409 here can ONLY be the disabled-gate, not the R2
    # session gate (which is checked later in the handler).
    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

    fake.setex(BRAVE_TA_SESSION_KEY, 3600, '{"cookies":{}}')

    with pytest.raises(HTTPException) as exc:
        engine_router.engine_start(
            redis=fake,
            body={"depth": "nascente", "source": "tripadvisor"},
            db=MagicMock(),
        )
    assert exc.value.status_code == 409
    assert "disabled" in str(exc.value.detail).lower()
    # No state mutation on a rejected start (no phantom run).
    assert ce.get_state(fake) == ce.IDLE


def test_start_allows_enabled_source(monkeypatch):
    """source='default' while enabled → passes the source gate (reaches start_run/202)."""
    cfg = _config_with({"default": True, "tripadvisor": False})
    monkeypatch.setattr(engine_router, "load_effective_config", lambda db, redis: cfg)

    import brave.tasks.pipeline as pipeline

    monkeypatch.setattr(pipeline.engine_sweep_run, "delay", lambda *a, **k: None)

    fake = fakeredis.FakeStrictRedis()
    result = engine_router.engine_start(
        redis=fake,
        body={"depth": "nascente", "source": "default"},
        db=MagicMock(),
    )
    assert result["status"] == "started"
    assert result["source"] == "default"


def test_start_unknown_source_is_422(monkeypatch):
    """An unregistered lane is a 422 (malformed), distinct from the disabled 409."""
    cfg = _config_with({"default": True, "tripadvisor": True})
    monkeypatch.setattr(engine_router, "load_effective_config", lambda db, redis: cfg)

    fake = fakeredis.FakeStrictRedis()
    with pytest.raises(HTTPException) as exc:
        engine_router.engine_start(
            redis=fake,
            body={"depth": "nascente", "source": "mtur"},
            db=MagicMock(),
        )
    assert exc.value.status_code == 422
    assert ce.get_state(fake) == ce.IDLE
