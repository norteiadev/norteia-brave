"""FastAPI dependency injection (D-21).

Provides:
  get_db()      — yields a synchronous SQLAlchemy Session
  get_redis()   — yields a Redis client
  get_config()  — returns AppConfig singleton
  get_db_config() — returns DBConfig
  get_webhook_config() — returns WebhookConfig
"""

import hmac
import os
from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, status
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from brave.config.settings import AppConfig, DashboardConfig, StewardConfig, WebhookConfig

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


def get_dashboard_config() -> DashboardConfig:
    """Return DashboardConfig (BRAVE_DASHBOARD_BEARER_TOKEN) for the dashboard read surface."""
    return DashboardConfig()


# ---------------------------------------------------------------------------
# Auth dependencies (DASH-06, D-02)
# ---------------------------------------------------------------------------


def require_bearer(
    authorization: str | None = Header(None, alias="Authorization"),
    dashboard_config: DashboardConfig = Depends(get_dashboard_config),
) -> None:
    """Authenticate the dashboard read surface via an Authorization: Bearer token.

    Mirrors require_steward (dlq.py) exactly, swapping the header: constant-time
    hmac.compare_digest, fail-closed (an unset BRAVE_DASHBOARD_BEARER_TOKEN rejects
    every caller), 401 before any DB work, token never logged. This is the D-02
    Bearer-at-the-edge gate the dashboard BFF presents.
    """
    expected = dashboard_config.bearer_token
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    if not token or not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization: Bearer token required",
        )
    if not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
        )


def require_steward_or_bearer(
    x_steward_secret: str | None = Header(None, alias="X-Steward-Secret"),
    authorization: str | None = Header(None, alias="Authorization"),
    steward_config: StewardConfig = Depends(get_steward_config),
    dashboard_config: DashboardConfig = Depends(get_dashboard_config),
) -> None:
    """Authenticate a mutation endpoint via EITHER X-Steward-Secret OR Bearer (R4, D-02).

    Lets the dashboard's single operator Bearer token drive the existing DLQ + gate
    approve/reject/validate routes WITHOUT breaking the Phase 2/3 steward callers.
    Passes if either a valid X-Steward-Secret OR a valid Authorization: Bearer is
    present; raises 401 only when neither validates.

    Both paths keep the full security discipline: constant-time hmac.compare_digest,
    fail-closed (an unset secret/token can never validate — so an unset
    BRAVE_DASHBOARD_BEARER_TOKEN does NOT let a Bearer-presented request pass, and an
    unset BRAVE_STEWARD_SECRET does NOT let a steward-presented request pass), 401
    before any DB work, secrets never logged. The either-or still requires ONE valid
    secret — it does not weaken the write-to-production trust boundary (T-04-02).
    """
    steward_expected = steward_config.secret
    if (
        x_steward_secret
        and steward_expected
        and hmac.compare_digest(x_steward_secret, steward_expected)
    ):
        return

    bearer_expected = dashboard_config.bearer_token
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    if token and bearer_expected and hmac.compare_digest(token, bearer_expected):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="X-Steward-Secret or Authorization: Bearer token required",
    )


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
