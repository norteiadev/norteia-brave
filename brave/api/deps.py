"""FastAPI dependency injection (D-21).

Provides:
  get_db()      — yields a synchronous SQLAlchemy Session
  get_redis()   — yields a Redis client
  get_config()  — returns AppConfig singleton
  get_db_config() — returns DBConfig
  get_webhook_config() — returns WebhookConfig
"""

import os
from collections.abc import Generator

from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from brave.config.settings import AppConfig, StewardConfig, WebhookConfig

# ---------------------------------------------------------------------------
# Config singletons (lazily initialized)
# ---------------------------------------------------------------------------


def get_config() -> AppConfig:
    """Return AppConfig singleton."""
    return AppConfig()


def get_webhook_config() -> WebhookConfig:
    """Return WebhookConfig (BRAVE_WEBHOOK_SECRET)."""
    return WebhookConfig()


def get_steward_config() -> StewardConfig:
    """Return StewardConfig (BRAVE_STEWARD_SECRET) for mutating DLQ endpoints."""
    return StewardConfig()


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

    CR-02: NEVER silently falls back to fakeredis. The Redis client backs the
    compliance gate (RED quality auto-pause flag, volume-ramp counter) which the
    Celery workers read from the SAME real Redis. A fakeredis fallback on a
    transient connection blip would write the RED pause flag to an in-process
    instance the workers never see, so sends would continue while quality is RED
    — a BSP violation. A real Redis failure must surface, not be masked.

    Fakeredis is selectable ONLY by an explicit dev/test flag
    (BRAVE_USE_FAKEREDIS=1). It is fail-closed by default: with the flag unset,
    a ping failure raises and the request/webhook fails loudly.
    """
    global _redis_client
    if _redis_client is None:
        if os.environ.get("BRAVE_USE_FAKEREDIS", "").lower() in ("1", "true", "yes"):
            # Explicit dev/test opt-in only — never a production fallback.
            import fakeredis

            _redis_client = fakeredis.FakeRedis()
            return _redis_client

        redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        client = Redis.from_url(redis_url, socket_connect_timeout=1)
        client.ping()  # let it raise in prod — do NOT swallow (CR-02)
        _redis_client = client
    return _redis_client
