"""Shared pytest fixtures for norteia-brave test suite.

Fixtures:
  - db_engine       — synchronous SQLAlchemy engine pointing at docker-compose DB
  - db_session      — synchronous session (integration tests)
  - fake_redis      — fakeredis.FakeRedis for unit tests (no container required)
  - app_config      — AppConfig loaded from environment
  - score_config    — ScoreConfig with default §7.6 weights
  - db_config       — DBConfig (requires BRAVE_DB_URL env var)

Integration fixtures require:
  BRAVE_DB_URL=postgresql+psycopg://brave:brave@localhost:5432/norteia_brave

pytest-socket enforcement (PITFALLS §5, TEST-01):
  Unit tests run with --disable-socket by default (no outbound network).
  Integration tests that need DB/Redis connections use localhost only.
  Real external calls require RUN_REAL_EXTERNALS=1 (CI always 0).
"""

import os

import fakeredis
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from brave.config.settings import AppConfig, DBConfig, ScoreConfig


# ---------------------------------------------------------------------------
# Marker gating: skip opt-in real-browser tests unless RUN_REAL_EXTERNALS=1
# real_browser tests need a live browser + TripAdvisor access (real external).
# CI runs with RUN_REAL_EXTERNALS=0, so they skip by default.
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config, items):
    """Skip @pytest.mark.real_browser tests unless RUN_REAL_EXTERNALS=1."""
    if os.environ.get("RUN_REAL_EXTERNALS") == "1":
        return
    skip_real_browser = pytest.mark.skip(
        reason="real_browser test skipped — set RUN_REAL_EXTERNALS=1 to opt in"
    )
    for item in items:
        if "real_browser" in item.keywords:
            item.add_marker(skip_real_browser)


# ---------------------------------------------------------------------------
# pytest-socket: disable real network in unit tests
# Unit tests should not make any outbound connections.
# Integration tests that need localhost (DB, Redis) use pytest.mark.enable_socket.
# ---------------------------------------------------------------------------
# Note: pytest-socket is configured via CLI flag (--disable-socket) or
# pytestmark on individual test modules. We don't set it globally here
# to avoid breaking integration tests that need localhost connections.
# See pyproject.toml [tool.pytest.ini_options] for CI configuration.


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def score_config() -> ScoreConfig:
    """ScoreConfig with default §7.6 weights and thresholds."""
    return ScoreConfig()


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    """AppConfig loaded from environment."""
    return AppConfig()


@pytest.fixture(scope="session")
def db_config() -> DBConfig | None:
    """DBConfig loaded from environment.

    Returns None if BRAVE_DB_URL is not set (unit tests that don't need a DB).
    """
    url = os.environ.get("BRAVE_DB_URL")
    if not url:
        return None
    return DBConfig(url=url)


# ---------------------------------------------------------------------------
# Database fixtures (require docker-compose postgres)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def db_engine(db_config: DBConfig | None):
    """Synchronous SQLAlchemy engine for the docker-compose test database.

    Session-scoped: one engine for the whole test run.
    Skips if BRAVE_DB_URL is not set.
    """
    if db_config is None:
        pytest.skip("BRAVE_DB_URL not set — skipping integration test")
    engine = create_engine(db_config.url, echo=False)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine) -> Session:
    """Synchronous SQLAlchemy session, rolled back after each test."""
    SessionFactory = sessionmaker(bind=db_engine)
    session = SessionFactory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# Redis fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    """In-process FakeRedis instance for unit tests.

    No Redis container required. State is reset per test.
    """
    return fakeredis.FakeRedis()
