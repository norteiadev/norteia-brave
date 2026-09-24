"""The config overlay cache: registry-only rows in Redis, dropped when a config write commits.

Fully offline: an in-memory SQLite ``config_settings`` table (a real SQLAlchemy Session,
so the after_commit/after_rollback listeners actually fire) + fakeredis.
"""

from __future__ import annotations

import json

import fakeredis
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from brave.config import runtime
from brave.config.runtime import (
    CONFIG_KEYS,
    OVERLAY_KEY,
    _apply_overlay,
    load_effective_config,
    upsert_config,
)
from brave.config.settings import AppConfig
from brave.core.models import ConfigSetting


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    ConfigSetting.__table__.create(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def redis(monkeypatch):
    rc = fakeredis.FakeRedis()
    monkeypatch.setattr(runtime, "overlay_redis", lambda: rc)
    return rc


def test_commit_of_a_config_write_drops_the_cached_overlay(session, redis):
    redis.set(OVERLAY_KEY, "{}")
    upsert_config(session, {"score.threshold_mar": 70.0})
    assert redis.exists(OVERLAY_KEY)  # flushed, not yet durable → cache untouched

    session.commit()

    assert not redis.exists(OVERLAY_KEY)


def test_rolled_back_config_write_keeps_the_cache(session, redis):
    redis.set(OVERLAY_KEY, "{}")
    upsert_config(session, {"score.threshold_mar": 70.0})
    session.rollback()
    assert redis.exists(OVERLAY_KEY)

    # The rollback cleared the mark: an unrelated later commit busts nothing.
    session.commit()
    assert redis.exists(OVERLAY_KEY)


def test_cached_overlay_holds_only_registered_keys_and_no_secret(session, redis, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "prl-secret")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    session.add(ConfigSetting(key="places_enrichment_enabled", value={"v": False}))
    session.add(ConfigSetting(key="some.future.key", value={"v": 1}))
    session.commit()

    miss = load_effective_config(session, redis)
    cached = json.loads(redis.get(OVERLAY_KEY))
    hit = load_effective_config(session, redis)

    assert cached == {"places_enrichment_enabled": False}
    assert set(cached) <= set(CONFIG_KEYS)
    # The env half is rebuilt on every read: real keys on a miss AND on a cache hit.
    for cfg in (miss, hit):
        assert cfg.parallel_api_key == "prl-secret"
        assert cfg.tavily_api_key == "tvly-secret"
        assert cfg.places_enrichment_enabled is False


def test_apply_overlay_sets_every_registered_field():
    base = AppConfig()
    values = {
        "weight": 12.5,
        "threshold": 66.0,
        "mode": "PAUSADO",
    }
    overlays = {
        key: values.get(entry.kind, not entry.read(base)) for key, entry in CONFIG_KEYS.items()
    }

    effective = _apply_overlay(base, overlays)

    for key, entry in CONFIG_KEYS.items():
        assert entry.read(effective) == overlays[key], key
    # Unknown keys are ignored.
    assert _apply_overlay(base, {"some.future.key": 1}) == base
