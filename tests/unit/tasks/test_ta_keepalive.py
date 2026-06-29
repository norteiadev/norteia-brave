"""Offline unit tests for brave.ta_keepalive Celery task (260629-p2v).

Tests verify:
  - task skips silently when run_real_externals=False (offline/CI)
  - task skips silently when no session in Redis (TTL ≤ 0)
  - SessionExpiredError → needs_bootstrap set + engine turned OFF
  - SessionMissingError → same fallback
  - Non-session RuntimeError → task returns normally (beat must not crash)
  - Task registered in app.tasks after importing pipeline
  - TripAdvisorConfig.keepalive_interval_seconds default and env-override

All tests run 100% offline: fakeredis, monkeypatched redis.from_url, no real HTTP.
"""

from __future__ import annotations

import json
import os

import fakeredis
import pytest

from brave.lanes.tripadvisor.client import (
    BRAVE_TA_SESSION_KEY,
    SessionExpiredError,
    SessionMissingError,
)

# Redis key constants (mirrors pipeline.py)
_TA_NEEDS_BOOTSTRAP_KEY = "brave:ta:needs_bootstrap"
_ENGINE_ENABLED_KEY = "brave:engine:enabled"


# ---------------------------------------------------------------------------
# Stub TA clients (used by the tests that exercise the fallback path)
# ---------------------------------------------------------------------------


class _StubExpiredClient:
    """Stub TripAdvisorClient whose fetch_attractions_paginated raises SessionExpiredError."""

    def __init__(self, config, redis):
        pass

    async def fetch_attractions_paginated(self, geo_id, start_page, max_pages):
        raise SessionExpiredError("datadome expired — re-inject required")
        yield  # noqa: unreachable — makes this an async generator  # type: ignore[misc]


class _StubMissingClient:
    """Stub TripAdvisorClient whose fetch_attractions_paginated raises SessionMissingError."""

    def __init__(self, config, redis):
        pass

    async def fetch_attractions_paginated(self, geo_id, start_page, max_pages):
        raise SessionMissingError("no session in Redis")
        yield  # noqa: unreachable — makes this an async generator  # type: ignore[misc]


class _StubRuntimeErrorClient:
    """Stub TripAdvisorClient that raises a non-session RuntimeError."""

    def __init__(self, config, redis):
        pass

    async def fetch_attractions_paginated(self, geo_id, start_page, max_pages):
        raise RuntimeError("unexpected network error")
        yield  # noqa: unreachable — makes this an async generator  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Helper: seed a valid session into fakeredis
# ---------------------------------------------------------------------------


def _seed_session(redis, ttl: int = 1800) -> None:
    """Write a valid brave:ta:session into fakeredis with the given TTL."""
    session = {
        "cookies": {"datadome": "abc", "TAAUTHEAT": "auth"},
        "query_ids": {"destinations": "abc123", "attractions": "a5cb7fa004b5e4b5"},
        "user_agent": "Mozilla/5.0",
        "acquired_at": "2026-06-24T12:00:00Z",
        "session_id": "mysid",
    }
    redis.setex(BRAVE_TA_SESSION_KEY, ttl, json.dumps(session))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTaKeepaliveTask:
    """Unit tests for the brave.ta_keepalive Celery task."""

    def test_skips_when_run_real_externals_false(self, monkeypatch):
        """When run_real_externals=False (offline/CI), the task returns immediately."""
        import brave.tasks.pipeline as pipeline_module

        # Build a mock AppConfig with run_real_externals=False
        class _MockAppConfig:
            run_real_externals = False

        monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: _MockAppConfig())

        # If any real HTTP or Redis call is made, it would blow up without a real server.
        # The test simply verifies no exception is raised.
        from brave.tasks.pipeline import ta_keepalive  # noqa: PLC0415

        ta_keepalive()  # must return silently

    def test_skips_when_no_session(self, monkeypatch):
        """When brave:ta:session is absent from Redis (TTL ≤ 0), task returns early."""
        import brave.tasks.pipeline as pipeline_module  # noqa: F401

        fake = fakeredis.FakeRedis()  # empty — no session key
        monkeypatch.setattr("redis.from_url", lambda url, **kw: fake)
        monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost/0")

        class _MockAppConfig:
            run_real_externals = True

        monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: _MockAppConfig())

        from brave.tasks.pipeline import ta_keepalive  # noqa: PLC0415

        ta_keepalive()

        # Must NOT set needs_bootstrap — the task returned early (no session error)
        assert fake.get(_TA_NEEDS_BOOTSTRAP_KEY) is None, (
            "needs_bootstrap must NOT be set when task skips due to missing session"
        )

    def test_session_expired_sets_needs_bootstrap_and_engine_off(self, monkeypatch):
        """SessionExpiredError from fetch_attractions_paginated → needs_bootstrap + engine OFF."""
        fake = fakeredis.FakeRedis()
        _seed_session(fake, ttl=1800)

        # Global redis.from_url patch so both ta_keepalive and _mark_needs_bootstrap
        # use the SAME fakeredis instance (per plan: "monkeypatch redis.from_url GLOBALLY")
        monkeypatch.setattr("redis.from_url", lambda url, **kw: fake)
        monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost/0")

        class _MockAppConfig:
            run_real_externals = True

        monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: _MockAppConfig())
        monkeypatch.setattr(
            "brave.lanes.tripadvisor.client.TripAdvisorClient", _StubExpiredClient
        )

        from brave.tasks.pipeline import ta_keepalive  # noqa: PLC0415

        ta_keepalive()  # must not raise

        assert fake.get(_TA_NEEDS_BOOTSTRAP_KEY) is not None, (
            "needs_bootstrap must be set after SessionExpiredError"
        )
        assert fake.get(_ENGINE_ENABLED_KEY) == b"0", (
            "engine:enabled must be set to 0 (OFF) after SessionExpiredError"
        )

    def test_session_missing_also_triggers_fallback(self, monkeypatch):
        """SessionMissingError has the same fallback: needs_bootstrap + engine OFF."""
        fake = fakeredis.FakeRedis()
        _seed_session(fake, ttl=1800)

        monkeypatch.setattr("redis.from_url", lambda url, **kw: fake)
        monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost/0")

        class _MockAppConfig:
            run_real_externals = True

        monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: _MockAppConfig())
        monkeypatch.setattr(
            "brave.lanes.tripadvisor.client.TripAdvisorClient", _StubMissingClient
        )

        from brave.tasks.pipeline import ta_keepalive  # noqa: PLC0415

        ta_keepalive()

        assert fake.get(_TA_NEEDS_BOOTSTRAP_KEY) is not None, (
            "needs_bootstrap must be set after SessionMissingError"
        )
        assert fake.get(_ENGINE_ENABLED_KEY) == b"0", (
            "engine:enabled must be 0 (OFF) after SessionMissingError"
        )

    def test_non_session_error_does_not_crash(self, monkeypatch):
        """A non-session RuntimeError must be caught and logged; the beat must not crash."""
        fake = fakeredis.FakeRedis()
        _seed_session(fake, ttl=1800)

        monkeypatch.setattr("redis.from_url", lambda url, **kw: fake)
        monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost/0")

        class _MockAppConfig:
            run_real_externals = True

        monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: _MockAppConfig())
        monkeypatch.setattr(
            "brave.lanes.tripadvisor.client.TripAdvisorClient", _StubRuntimeErrorClient
        )

        from brave.tasks.pipeline import ta_keepalive  # noqa: PLC0415

        # Must not raise — the beat scheduler must survive unknown errors
        ta_keepalive()

    def test_task_registered(self):
        """brave.ta_keepalive must be registered in the Celery app task registry."""
        from brave.tasks.celery_app import app  # noqa: PLC0415
        import brave.tasks.pipeline  # noqa: F401, PLC0415 — trigger task registration

        assert "brave.ta_keepalive" in app.tasks, (
            "brave.ta_keepalive must be registered via @shared_task(name=...) in pipeline.py"
        )


class TestTaKeepaliveSettings:
    """Unit tests for the keepalive_interval_seconds field in TripAdvisorConfig."""

    def test_settings_keepalive_interval_default(self):
        """TripAdvisorConfig().keepalive_interval_seconds must default to 600."""
        from brave.config.settings import TripAdvisorConfig

        config = TripAdvisorConfig()
        assert config.keepalive_interval_seconds == 600, (
            f"Default must be 600s (10 min); got {config.keepalive_interval_seconds}"
        )

    def test_settings_keepalive_env_override(self, monkeypatch):
        """BRAVE_TA_KEEPALIVE_INTERVAL_SECONDS env var overrides the default."""
        monkeypatch.setenv("BRAVE_TA_KEEPALIVE_INTERVAL_SECONDS", "300")

        from brave.config.settings import TripAdvisorConfig

        config = TripAdvisorConfig()
        assert config.keepalive_interval_seconds == 300, (
            f"Env override must set value to 300; got {config.keepalive_interval_seconds}"
        )
