"""Offline unit tests for brave.ta_keepalive Celery task (260629-p2v).

Tests verify:
  - task skips silently when run_real_externals=False (offline/CI)
  - task skips silently when no session in Redis (TTL ≤ 0)
  - the ping goes over the GraphQL transport (the same one the sweep uses)
  - a successful ping slides the session TTL even with no rotated cookie
  - SessionExpiredError / SessionMissingError NEVER touch the engine (260917-tkd)
  - needs_bootstrap only after 3 CONSECUTIVE failures; one success resets the streak
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
_ENGINE_MODE_KEY = "brave:engine:mode"
_TA_KEEPALIVE_FAILURES_KEY = "brave:ta:keepalive_failures"


# ---------------------------------------------------------------------------
# Stub TA clients (used by the tests that exercise the fallback path)
# ---------------------------------------------------------------------------


class _StubOkClient:
    """Stub TripAdvisorClient that answers one GraphQL page and records the call.

    The HTML transport raises: a call to it is the regression this fix exists for.
    """

    calls: list[dict] = []

    def __init__(self, config, redis):
        pass

    async def fetch_attractions_paginated_gql(self, geo_id, start_page=1, max_pages=1):
        _StubOkClient.calls.append(
            {"geo_id": geo_id, "start_page": start_page, "max_pages": max_pages}
        )
        yield 0, [{"name": "Atrativo", "locationId": "1"}]

    async def fetch_attractions_paginated(self, geo_id, start_page, max_pages):
        raise AssertionError("the keepalive must not use the HTML transport (DataDome 403s it)")
        yield  # noqa: unreachable — makes this an async generator  # type: ignore[misc]


class _StubExpiredClient:
    """Stub TripAdvisorClient whose GraphQL ping raises SessionExpiredError."""

    def __init__(self, config, redis):
        pass

    async def fetch_attractions_paginated_gql(self, geo_id, start_page=1, max_pages=1):
        raise SessionExpiredError("datadome expired — re-inject required")
        yield  # noqa: unreachable — makes this an async generator  # type: ignore[misc]


class _StubMissingClient:
    """Stub TripAdvisorClient whose GraphQL ping raises SessionMissingError."""

    def __init__(self, config, redis):
        pass

    async def fetch_attractions_paginated_gql(self, geo_id, start_page=1, max_pages=1):
        raise SessionMissingError("no session in Redis")
        yield  # noqa: unreachable — makes this an async generator  # type: ignore[misc]


class _StubRuntimeErrorClient:
    """Stub TripAdvisorClient that raises a non-session RuntimeError."""

    def __init__(self, config, redis):
        pass

    async def fetch_attractions_paginated_gql(self, geo_id, start_page=1, max_pages=1):
        raise RuntimeError("unexpected network error")
        yield  # noqa: unreachable — makes this an async generator  # type: ignore[misc]


def _run_keepalive(monkeypatch, fake, stub) -> None:
    """Wire fakeredis + a stub client and run the beat once."""
    monkeypatch.setattr("redis.from_url", lambda url, **kw: fake)
    monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://localhost/0")

    class _MockAppConfig:
        run_real_externals = True

    monkeypatch.setattr("brave.tasks.pipeline.AppConfig", lambda: _MockAppConfig())
    monkeypatch.setattr("brave.lanes.tripadvisor.client.TripAdvisorClient", stub)

    from brave.tasks.pipeline import ta_keepalive  # noqa: PLC0415

    ta_keepalive()


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

    def test_ping_uses_the_graphql_transport(self, monkeypatch):
        """The beat pings AttractionsFusion over GraphQL — the same transport the sweep
        uses. The HTML page is 403'd by DataDome even on a healthy session, which is what
        used to kill live sweeps."""
        fake = fakeredis.FakeRedis()
        _seed_session(fake, ttl=1800)
        _StubOkClient.calls = []

        _run_keepalive(monkeypatch, fake, _StubOkClient)

        assert _StubOkClient.calls == [{"geo_id": 294280, "start_page": 1, "max_pages": 1}]
        assert fake.get(_TA_NEEDS_BOOTSTRAP_KEY) is None

    def test_successful_ping_slides_the_ttl_and_clears_the_streak(self, monkeypatch):
        """A 200 that rotates no cookie still keeps the session alive: the beat expires
        the key forward itself (persist_rotated_cookies only writes when cookies rotate)."""
        fake = fakeredis.FakeRedis()
        _seed_session(fake, ttl=120)  # nearly dead
        fake.set(_TA_KEEPALIVE_FAILURES_KEY, 2)
        _StubOkClient.calls = []

        _run_keepalive(monkeypatch, fake, _StubOkClient)

        from brave.config.settings import TripAdvisorConfig

        assert fake.ttl(BRAVE_TA_SESSION_KEY) > 120, "the TTL must slide forward"
        assert fake.ttl(BRAVE_TA_SESSION_KEY) <= TripAdvisorConfig().session_ttl
        assert fake.get(_TA_KEEPALIVE_FAILURES_KEY) is None, "a success resets the streak"

    def test_session_expired_never_touches_the_engine(self, monkeypatch):
        """THE regression this fix exists for: one 403 must not stop a running sweep.
        Deciding a session is dead belongs to sweep_tripadvisor (R1), not to a health beat."""
        fake = fakeredis.FakeRedis()
        _seed_session(fake, ttl=1800)
        fake.set(_ENGINE_MODE_KEY, "LIGADO")
        fake.set(_ENGINE_ENABLED_KEY, "1")

        _run_keepalive(monkeypatch, fake, _StubExpiredClient)

        assert fake.get(_ENGINE_MODE_KEY) == b"LIGADO", "the keepalive must not turn the motor off"
        assert fake.get(_ENGINE_ENABLED_KEY) == b"1"
        assert fake.get(_TA_NEEDS_BOOTSTRAP_KEY) is None, "one failure is not a verdict"
        assert fake.get(_TA_KEEPALIVE_FAILURES_KEY) == b"1"

    def test_needs_bootstrap_only_after_three_consecutive_failures(self, monkeypatch):
        """Two failures stay quiet; the third marks the operator flag — engine still on."""
        fake = fakeredis.FakeRedis()
        _seed_session(fake, ttl=1800)
        fake.set(_ENGINE_MODE_KEY, "LIGADO")

        for _ in range(2):
            _run_keepalive(monkeypatch, fake, _StubExpiredClient)
        assert fake.get(_TA_NEEDS_BOOTSTRAP_KEY) is None

        _run_keepalive(monkeypatch, fake, _StubExpiredClient)

        assert fake.get(_TA_NEEDS_BOOTSTRAP_KEY) is not None
        assert fake.get(_ENGINE_MODE_KEY) == b"LIGADO", "even a streak leaves the motor alone"

    def test_a_success_between_failures_resets_the_streak(self, monkeypatch):
        """Blips scattered over hours must never add up to a verdict."""
        fake = fakeredis.FakeRedis()
        _seed_session(fake, ttl=1800)
        _StubOkClient.calls = []

        for _ in range(2):
            _run_keepalive(monkeypatch, fake, _StubExpiredClient)
        _run_keepalive(monkeypatch, fake, _StubOkClient)
        for _ in range(2):
            _run_keepalive(monkeypatch, fake, _StubExpiredClient)

        assert fake.get(_TA_NEEDS_BOOTSTRAP_KEY) is None
        assert fake.get(_TA_KEEPALIVE_FAILURES_KEY) == b"2"

    def test_session_missing_follows_the_same_rule(self, monkeypatch):
        """SessionMissingError counts like an expiry: no engine change, no marker at one."""
        fake = fakeredis.FakeRedis()
        _seed_session(fake, ttl=1800)
        fake.set(_ENGINE_MODE_KEY, "LIGADO")

        _run_keepalive(monkeypatch, fake, _StubMissingClient)

        assert fake.get(_ENGINE_MODE_KEY) == b"LIGADO"
        assert fake.get(_TA_NEEDS_BOOTSTRAP_KEY) is None
        assert fake.get(_TA_KEEPALIVE_FAILURES_KEY) == b"1"

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
