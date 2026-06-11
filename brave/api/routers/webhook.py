"""POST /webhook/error-report — community error report endpoint (CNTR-02, T-02-01).

SECURITY:
  - X-Webhook-Secret header required (static shared-secret, T-02-01)
  - Compared with hmac.compare_digest (constant-time, no timing attack)
  - 401 returned BEFORE any DB work or rate-limit increment on bad secret
  - Secret NEVER logged (T-02-04)
  - Rate limiting: 10 requests/minute per IP (Redis counter)
  - source_ref validated against active MarRecord (404 on miss, not 500)

Future enhancement: HMAC-of-body signature (T-02-01 note).

The static shared-secret is the Phase 1 mitigation.
The endpoint is NOT unauthenticated.
"""

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from redis import Redis
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, get_webhook_config
from brave.config.settings import WebhookConfig
from brave.core.mar.service import reopen_from_error_report
from brave.observability.audit import write_audit

router = APIRouter()

# Rate limit: 10 requests per minute per IP
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60  # seconds


class ErrorReportRequest(BaseModel):
    """Request body for the error-report webhook.

    source_ref: Canonical source reference identifying the Mar record
                to reopen into the review DLQ.
    """

    source_ref: str


def _check_rate_limit(redis: Redis, ip: str) -> None:
    """Enforce per-IP rate limit (10 requests/minute).

    Raises HTTPException 429 if limit exceeded.
    Note: Rate limit is checked AFTER secret verification to prevent
    rate-limit oracles from leaking valid IP information to unauthenticated callers.
    """
    key = f"brave:webhook:ratelimit:{ip}"
    count = redis.incr(key)
    if count == 1:
        redis.expire(key, RATE_LIMIT_WINDOW)
    if count > RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Maximum 10 error reports per minute.",
        )


@router.post("/webhook/error-report", status_code=202)
def error_report(
    request: Request,
    body: ErrorReportRequest,
    x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
    webhook_config: WebhookConfig = Depends(get_webhook_config),
) -> dict:
    """Reopen a published MarRecord into the review DLQ via community error report.

    SECURITY ENFORCEMENT (T-02-01):
    1. Check X-Webhook-Secret header FIRST — 401 BEFORE any DB work
    2. Constant-time comparison via hmac.compare_digest (no timing oracle)
    3. Secret never logged
    4. Rate limit checked AFTER authentication (10 req/min per IP)
    5. source_ref validated against active MarRecord (404, not 500)

    Args:
        body: {"source_ref": "mtur:BA:123"}

    Returns:
        202 Accepted with {"status": "accepted", "source_ref": ..., "rio_id": ...}
        404 if source_ref not found in active Mar records
        401 if X-Webhook-Secret missing or incorrect
        429 if rate limit exceeded

    Note: Returns 202 even if the record is already in DLQ (idempotent).
    """
    # --- AUTHENTICATION GATE (T-02-01) ---
    # Check secret BEFORE any DB access or rate-limit increment
    # Using hmac.compare_digest for constant-time comparison (no timing oracle)
    expected_secret = webhook_config.secret

    if not x_webhook_secret or not expected_secret:
        # Missing header or unconfigured secret → 401
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Webhook-Secret header required",
        )

    if not hmac.compare_digest(x_webhook_secret, expected_secret):
        # Wrong secret → 401 (constant-time comparison complete before raising)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Webhook-Secret",
        )
    # --- END AUTHENTICATION GATE ---

    # Rate limit check (after authentication)
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(redis, client_ip)

    # Find and reopen the MarRecord
    rio = reopen_from_error_report(db, body.source_ref)
    if rio is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active MarRecord found for source_ref '{body.source_ref}'",
        )

    # Audit log
    write_audit(
        session=db,
        action="error_report_received",
        entity_type=rio.entity_type,
        record_id=rio.id,
        after_state={"routing": "dlq", "dlq_reason": "community_error_report"},
        actor="webhook",
    )

    return {
        "status": "accepted",
        "source_ref": body.source_ref,
        "rio_id": str(rio.id),
    }
