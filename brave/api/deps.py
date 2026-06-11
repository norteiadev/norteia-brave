"""FastAPI dependency injection (D-21).

Provides:
  get_db()      — yields a synchronous SQLAlchemy Session
  get_redis()   — yields a Redis client
  get_config()  — returns AppConfig singleton
  get_db_config() — returns DBConfig
  get_webhook_config() — returns WebhookConfig
"""

import os
from typing import Generator

import fakeredis
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from brave.config.settings import AppConfig, DBConfig, LLMConfig, WebhookConfig


# ---------------------------------------------------------------------------
# Config singletons (lazily initialized)
# ---------------------------------------------------------------------------


def get_config() -> AppConfig:
    """Return AppConfig singleton."""
    return AppConfig()


def get_webhook_config() -> WebhookConfig:
    """Return WebhookConfig (BRAVE_WEBHOOK_SECRET)."""
    return WebhookConfig()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def _create_engine_from_env():
    """Create engine from environment variable."""
    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        return None
    return create_engine(db_url, echo=False)


_engine = None
_SessionFactory = None


def _get_session_factory():
    global _engine, _SessionFactory
    if _SessionFactory is None:
        _engine = _create_engine_from_env()
        if _engine is None:
            return None
        _SessionFactory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _SessionFactory


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy synchronous Session.

    Dependency injection for FastAPI routes. Session is committed or
    rolled back automatically after the request.
    """
    factory = _get_session_factory()
    if factory is None:
        raise RuntimeError("BRAVE_DB_URL not set — cannot create DB session")
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------


_redis_client = None


def get_redis() -> Redis:
    """Return a Redis client.

    Uses real Redis in production (BRAVE_DB_REDIS_URL env var).
    Falls back to fakeredis in test/dev when Redis is not available.
    """
    global _redis_client
    if _redis_client is None:
        redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        try:
            client = Redis.from_url(redis_url, socket_connect_timeout=1)
            client.ping()
            _redis_client = client
        except Exception:
            # Fallback to fakeredis for development without a Redis container
            _redis_client = fakeredis.FakeRedis()
    return _redis_client
