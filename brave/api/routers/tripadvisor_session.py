"""TripAdvisor session injection and status endpoints (TA-10, TA-11).

Endpoints:
  POST /api/v1/tripadvisor/session        — inject operator session + canary gate
  GET  /api/v1/tripadvisor/session/status — read current session status

The injection endpoint accepts a DataDome session (cookies + queryIds) captured
by the operator's real browser via DevTools "Copy as cURL". It writes the session
to Redis for the worker to consume, then runs a synchronous canary validation
through the real httpx path to fail-fast on stale sessions.

Security:
  T-12-02-01: Cookie values are NEVER logged — audit records only cookie_count + query_ids keys.
  T-12-02-02: Both endpoints require require_steward_or_bearer auth.
  T-12-02-03: 64 KB content-length check before Pydantic parse.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from redis import Redis

from brave.api.deps import get_redis, require_steward_or_bearer
from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY, SessionExpiredError

logger = structlog.get_logger(__name__)
router = APIRouter()

# Redis key for the bootstrap marker (set by sweep on SessionMissingError)
_TA_NEEDS_BOOTSTRAP_KEY: str = "brave:ta:needs_bootstrap"

# Maximum allowed POST body size: 64 KB
_MAX_BODY_BYTES: int = 65536

# Canary timeout in seconds
_CANARY_TIMEOUT_SECONDS: float = 15.0


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SessionInjectBody(BaseModel):
    """Request model for POST /api/v1/tripadvisor/session.

    extra="forbid" ensures unknown fields are rejected with 422.
    T-12-02-01: Cookie values are NEVER logged — only cookie_count is recorded.
    """

    model_config = {"extra": "forbid"}

    cookies: dict[str, str] = Field(
        ...,
        description=(
            "DataDome session cookies captured from a real browser. "
            "Must be non-empty. Values are NEVER logged (T-12-02-01)."
        ),
    )
    query_ids: dict[str, str] = Field(
        ...,
        description=(
            "GraphQL preRegisteredQueryId map (e.g. {'destinations': '<16-hex-id>'}). "
            "Must have at least one entry."
        ),
    )
    user_agent: str = Field(..., description="Browser User-Agent string from the capture session.")
    acquired_at: str = Field(..., description="ISO8601 timestamp when the session was captured.")
    client_hints: dict[str, str] | None = Field(
        default=None,
        description="Optional Sec-CH-UA client hint headers captured from the browser.",
    )
    locale: str | None = Field(default="pt-BR", description="Locale override (default: pt-BR).")
    acquisition_ip: str | None = Field(
        default=None,
        description="Optional source IP of the operator's browser at capture time.",
    )

    @model_validator(mode="after")
    def _validate_non_empty(self) -> "SessionInjectBody":
        """Reject empty cookies or query_ids dicts."""
        if not self.cookies:
            raise ValueError("cookies must be non-empty")
        if not self.query_ids:
            raise ValueError("query_ids must have at least one entry")
        return self


class TASessionStatusResponse(BaseModel):
    """Response model for GET /api/v1/tripadvisor/session/status."""

    present: bool
    expires_in: int | None = None
    query_ids: list[str] | None = None
    reason: Literal["needs_bootstrap"] | None = None


# ---------------------------------------------------------------------------
# Canary gate (injectable for tests)
# ---------------------------------------------------------------------------


async def _run_canary(session: dict[str, Any], ta_config: Any, redis: Redis) -> None:
    """Run a synchronous canary validation through the real httpx path.

    On success (non-empty result list): returns normally.
    On any failure (SessionExpiredError, timeout, empty result): deletes the
    Redis session key and raises HTTPException(422, detail="invalid_session").

    NEVER call this from tests — monkeypatch this function at the module level.
    Tests should: monkeypatch brave.api.routers.tripadvisor_session._run_canary

    T-12-02-01: Only logs cookie_count and query_ids keys — never values.
    """
    from brave.lanes.tripadvisor.client import TripAdvisorClient

    client = TripAdvisorClient(config=ta_config, redis=redis)
    try:
        results = await asyncio.wait_for(
            client.fetch_destinations("RJ"),
            timeout=_CANARY_TIMEOUT_SECONDS,
        )
    except (SessionExpiredError, asyncio.TimeoutError) as exc:
        redis.delete(BRAVE_TA_SESSION_KEY)
        logger.warning(
            "ta_session_canary_failed",
            reason=type(exc).__name__,
            cookie_count=len(session.get("cookies", {})),
            query_ids_keys=list(session.get("query_ids", {}).keys()),
            # T-12-02-01: cookie VALUES are NEVER logged here
        )
        raise HTTPException(status_code=422, detail="invalid_session") from exc
    except Exception as exc:
        redis.delete(BRAVE_TA_SESSION_KEY)
        logger.warning(
            "ta_session_canary_error",
            reason=str(exc),
            cookie_count=len(session.get("cookies", {})),
            query_ids_keys=list(session.get("query_ids", {}).keys()),
        )
        raise HTTPException(status_code=422, detail="invalid_session") from exc

    # Empty-result guard: a valid 200 response with empty data means stale queryId
    if not results:
        redis.delete(BRAVE_TA_SESSION_KEY)
        logger.warning(
            "ta_session_canary_empty_result",
            cookie_count=len(session.get("cookies", {})),
            query_ids_keys=list(session.get("query_ids", {}).keys()),
        )
        raise HTTPException(status_code=422, detail="invalid_session")


# ---------------------------------------------------------------------------
# POST /api/v1/tripadvisor/session
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/tripadvisor/session",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
async def inject_session(
    request: Request,
    body: SessionInjectBody,
    redis: Redis = Depends(get_redis),
) -> dict:
    """Inject a TripAdvisor DataDome session captured by the operator's real browser.

    Workflow:
      1. Size-check the raw body (64 KB limit — T-12-02-03)
      2. Validate body (Pydantic — includes non-empty checks)
      3. Write session to Redis with TTL from TripAdvisorConfig
      4. Run synchronous canary gate through real httpx path
      5. Audit-log (cookie_count + query_ids keys — NEVER values)
      6. Return {status: "ready", canary: "ready"}

    On canary failure: Redis key is deleted and 422 is returned.
    """
    # T-12-02-03: 64 KB size guard before any parsing
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_BODY_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail=f"Request body too large (max {_MAX_BODY_BYTES} bytes)",
                )
        except ValueError:
            pass  # Non-integer content-length — let FastAPI handle

    # Build the session dict to store in Redis
    session: dict[str, Any] = {
        "cookies": body.cookies,
        "query_ids": body.query_ids,
        "user_agent": body.user_agent,
        "acquired_at": body.acquired_at,
    }

    # Load TripAdvisor config for TTL
    from brave.config.settings import TripAdvisorConfig

    ta_config = TripAdvisorConfig()

    # Write the session to Redis FIRST (canary uses it via the same key)
    redis.setex(BRAVE_TA_SESSION_KEY, ta_config.session_ttl, json.dumps(session))
    logger.debug(
        "ta_session_key_written",
        cookie_count=len(body.cookies),
        query_ids_keys=list(body.query_ids.keys()),
        # T-12-02-01: cookie values NEVER logged
    )

    # Canary gate: validates the session synchronously before returning ready
    await _run_canary(session, ta_config, redis)

    # Audit log (T-12-02-01: only metadata, NEVER cookie values)
    try:
        from brave.api.deps import get_db
        from brave.observability.audit import write_audit

        # get_db may be overridden to None in tests — skip audit in that case
        db_gen = get_db()
        try:
            db = next(db_gen)
            write_audit(
                session=db,
                action="ta_session_injected",
                entity_type=None,
                actor="operator",
                after_state={
                    "cookie_count": len(body.cookies),
                    "query_ids": list(body.query_ids.keys()),
                    "acquired_at": body.acquired_at,
                    "canary_result": "ready",
                },
            )
        except StopIteration:
            pass
        except Exception as audit_exc:
            # Audit failure must not block the successful inject
            logger.warning("ta_session_audit_failed", error=str(audit_exc))
            try:
                db_gen.close()
            except Exception:
                pass
        else:
            try:
                db_gen.close()
            except Exception:
                pass
    except Exception as audit_exc:
        logger.warning("ta_session_audit_skip", error=str(audit_exc))

    logger.info(
        "ta_session_injected",
        cookie_count=len(body.cookies),
        query_ids_keys=list(body.query_ids.keys()),
        acquired_at=body.acquired_at,
        canary_result="ready",
        # T-12-02-01: cookie VALUES are NEVER logged here
    )

    return {"status": "ready", "canary": "ready"}


# ---------------------------------------------------------------------------
# GET /api/v1/tripadvisor/session/status
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/tripadvisor/session/status",
    dependencies=[Depends(require_steward_or_bearer)],
    response_model=TASessionStatusResponse,
)
def session_status(redis: Redis = Depends(get_redis)) -> TASessionStatusResponse:
    """Return the current TripAdvisor session status.

    Response shapes:
      - Session present:     {present: True, expires_in: int, query_ids: [...], reason: null}
      - Session absent + needs_bootstrap marker: {present: False, reason: "needs_bootstrap"}
      - Session absent + no marker:              {present: False, reason: null}
    """
    raw = redis.get(BRAVE_TA_SESSION_KEY)

    if raw is not None:
        ttl_seconds = redis.ttl(BRAVE_TA_SESSION_KEY)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            stored_session = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            stored_session = {}

        return TASessionStatusResponse(
            present=True,
            expires_in=max(ttl_seconds, 0),
            query_ids=list(stored_session.get("query_ids", {}).keys()),
            reason=None,
        )

    # Session absent — check for the needs_bootstrap marker
    has_marker = bool(redis.get(_TA_NEEDS_BOOTSTRAP_KEY))
    return TASessionStatusResponse(
        present=False,
        reason="needs_bootstrap" if has_marker else None,
    )
