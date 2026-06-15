"""D-11 compliance send-path gate — hard block before every WhatsApp send (COMP-01/02).

This module is the single enforcement point for LGPD + BSP compliance.
send_path_gate MUST be called immediately before every WhatsAppClientProtocol.send_template.
No WhatsApp message may be sent without passing all 8 gate conditions.

Architecture invariant (Pitfall 3 — PATTERNS.md):
  send_template is ONLY called through send_path_gate. Calling it directly bypasses
  the compliance gate and is an LGPD/BSP violation with no code-time protection.
  The gate function is the single allowed caller path.

Gate conditions (D-11, in order):
  1. legal_basis_recorded    — consent_log has a row for contact_phone
  2. norteia_identified      — "Norteia" in params["body"]
  3. opt_out_honored         — consent_log.opted_out is False for this phone
  4. approved_template_used  — template_name in settings.approved_templates
  5. 24h_window_respected    — if window_open=False, blocks (template requires open window)
  6. human_gate_approved     — rio.sub_state == "whatsapp_in_progress"
  7. ramp_not_exceeded       — Redis INCR check (CR-04 atomic reserve-before-call)
  8. quality_not_red         — Redis flag wa:quality_red not set

Design mirrors brave/observability/cost_guard.py (same Redis-counter + raise-or-pass shape).
Ramp counter uses CR-04 reserve-before-call hardening (D-07):
  INCR first → check → DECR on cap breach (undo reserve).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog
from redis import Redis
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from brave.core.models import RioRecord

from brave.compliance.consent_log import is_opted_out
from brave.compliance.quality_rating import is_quality_red

logger = structlog.get_logger(__name__)


class ComplianceError(Exception):
    """Raised when any D-11 compliance gate condition fails.

    Always blocks the send — never advisory. The Celery task or endpoint that
    calls send_path_gate must catch ComplianceError and abort the send operation.
    Do NOT catch ComplianceError and proceed anyway — that defeats the gate.

    The error message always identifies which condition failed (for audit/debugging).
    """


def _next_utc_midnight() -> datetime:
    """Return the next UTC midnight as a datetime (for EXPIREAT TTL on ramp counter)."""
    now = datetime.now(timezone.utc)
    # Replace time components with midnight, then advance to next day
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if tomorrow <= now:
        # Already past midnight of today — advance one full day
        from datetime import timedelta
        tomorrow = tomorrow.replace(day=tomorrow.day + 1)
    return tomorrow


def _seconds_until_midnight() -> int:
    """Return seconds until next UTC midnight (for TTL fallback if EXPIREAT not available)."""
    now = time.time()
    tomorrow = (int(now) // 86400 + 1) * 86400
    return max(1, int(tomorrow - now))


def check_and_increment_ramp(
    redis_client: Redis,
    cap: int,
    uf: str | None = None,
) -> None:
    """Atomic reserve-before-call ramp counter (CR-04 hardening, D-07).

    Implements the WhatsApp volume ramp enforcer. Increments the daily counter
    atomically (Redis INCR) BEFORE any send. If the increment pushes the counter
    above cap, decrements back (undo reserve) and raises ComplianceError.

    Key format:
      Global:  wa:ramp:{YYYY-MM-DD}
      Per-UF:  wa:ramp:{UF}:{YYYY-MM-DD}

    TTL: set to UTC midnight on first write (EXPIREAT) so counters auto-expire
    without a cron job. This is the same crash-safe pattern as cost_guard.record_spend.

    CR-04 lesson: INCR first, then check. A crash after INCR but before send means
    the counter is slightly over-counted (conservative; safe). This prevents a
    race condition where two workers both read the count before incrementing and
    both see "under cap" — the INCR atomicity handles that correctly.

    Args:
        redis_client: Redis client (real or fakeredis).
        cap:          Daily cap (from RampConfig.daily_cap).
        uf:           Optional UF code for per-state ramp key (None = global key).

    Raises:
        ComplianceError: If the incremented counter exceeds cap (DECR undo before raising).
    """
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"wa:ramp:{date_key}" if uf is None else f"wa:ramp:{uf}:{date_key}"

    # Atomic INCR — reserve-before-call (CR-04)
    count = redis_client.incr(key)

    # Set TTL to UTC midnight on first write (crash-safe: key auto-expires daily)
    if count == 1:
        redis_client.expireat(key, _next_utc_midnight())

    if count > cap:
        # Undo the reserve — DECR to restore the pre-call counter value
        redis_client.decr(key)
        raise ComplianceError(
            f"Ramp cap {cap} exceeded for {date_key}. "
            "Counter decremented back. Approve additional sends after daily reset."
        )

    logger.info("ramp_incremented", key=key, count=count, cap=cap)


def send_path_gate(
    session: Session,
    redis_client: Redis,
    rio: "RioRecord",
    contact_phone: str,
    template_name: str,
    params: dict[str, Any],
    settings: Any,
) -> None:
    """Synchronous D-11 compliance gate. Raises ComplianceError on any condition failure.

    ARCHITECTURE INVARIANT: This function is the ONLY allowed caller path for
    WhatsAppClientProtocol.send_template. Bypassing this gate (calling send_template
    directly) is an LGPD/BSP violation. There is no approved "trusted caller" path
    that skips the gate.

    Checks are performed in order (1–8). The first failed condition raises immediately;
    subsequent conditions are not evaluated. This ensures the cheapest checks (in-memory)
    run first and the Redis checks (conditions 7–8) are only reached when all prior
    conditions pass.

    The gate is pure code: no LLM calls, no external network, no I/O beyond Redis reads
    and a single Postgres SELECT per condition that uses the session. 100% offline-testable
    with fakeredis + mock session.

    Args:
        session:        SQLAlchemy synchronous Session (for consent_log lookup).
        redis_client:   Redis client (real or fakeredis) for ramp counter + quality flag.
        rio:            RioRecord being processed (for sub_state + uf + normalized).
        contact_phone:  Contact phone number in E.164 format (+5511...).
        template_name:  Name of the WhatsApp BSP template to send.
        params:         Template parameters dict; must include "body" key (condition 2).
        settings:       WhatsApp settings object with approved_templates: list[str]
                        and ramp_cap: int attributes.

    Returns:
        None (implicit) on success — all 8 conditions passed.

    Raises:
        ComplianceError: If any gate condition fails. Message identifies the condition.
    """
    # ------------------------------------------------------------------
    # Condition 1: Legal basis recorded
    # consent_log must have at least one row for this contact_phone
    # ------------------------------------------------------------------
    from sqlalchemy import select
    from brave.core.models import ConsentLog

    legal_basis_row = session.scalar(
        select(ConsentLog).where(ConsentLog.phone_e164 == contact_phone)
    )
    if legal_basis_row is None:
        raise ComplianceError(
            f"legal_basis: no consent_log record found for contact_phone "
            f"(prefix: {contact_phone[:5]}). "
            "Write a consent record before sending (COMP-01)."
        )

    # ------------------------------------------------------------------
    # Condition 2: Norteia identification in message
    # "Norteia" must appear in params["body"] (LGPD Art. 7 transparency requirement)
    # ------------------------------------------------------------------
    message_body = params.get("body", "")
    if "Norteia" not in message_body:
        raise ComplianceError(
            "Norteia: sender identification 'Norteia' is missing from params['body']. "
            "LGPD requires the sender to identify themselves in every outreach message."
        )

    # ------------------------------------------------------------------
    # Condition 3: Opt-out honored
    # consent_log.opted_out must be False for this contact
    # ------------------------------------------------------------------
    if is_opted_out(session, contact_phone):
        raise ComplianceError(
            f"opted_out: contact (prefix: {contact_phone[:5]}) has opted out of outreach. "
            "Sending to an opted-out contact is an LGPD + BSP violation."
        )

    # ------------------------------------------------------------------
    # Condition 4: Approved BSP template used
    # template_name must be in the allowlist (settings.approved_templates)
    # ------------------------------------------------------------------
    approved_templates: list[str] = getattr(settings, "approved_templates", [])
    if template_name not in approved_templates:
        raise ComplianceError(
            f"template: '{template_name}' is not in the approved BSP template allowlist. "
            f"Approved templates: {approved_templates}. "
            "Register and add the template to WhatsAppConfig.approved_templates first."
        )

    # ------------------------------------------------------------------
    # Condition 5: 24h customer-service window respected
    # If window_open=False in rio.normalized, the 24h window is closed.
    # Only utility/auth templates that don't require the window are allowed.
    # All outbound outreach (marketing/utility-first-contact) requires an open window.
    # For this implementation: any closed window blocks the send.
    # (The gate treats ALL templates as requiring an open window for outreach safety.
    #  Future: add a template-category allowlist for auth/utility non-window messages.)
    # ------------------------------------------------------------------
    normalized = rio.normalized or {}
    window_open: bool = normalized.get("window_open", True)  # default True for initial sends
    if not window_open:
        raise ComplianceError(
            "24h window: customer service window is closed (window_open=False in rio.normalized). "
            "BSP policy: outreach templates require an open 24h window. "
            "Wait for an inbound message from the contact to reopen the window."
        )

    # ------------------------------------------------------------------
    # Condition 6: Human gate approved
    # rio.sub_state must be "whatsapp_in_progress" (set by gate approve endpoint)
    # ------------------------------------------------------------------
    if rio.sub_state != "whatsapp_in_progress":
        raise ComplianceError(
            f"sub_state: rio.sub_state is '{rio.sub_state}', expected 'whatsapp_in_progress'. "
            "Human gate must be approved (PATCH /api/v1/atrativos/gate/{rio_id}/approve) "
            "before dispatching outreach (D-06)."
        )

    # ------------------------------------------------------------------
    # Condition 7: Ramp cap not exceeded
    # Atomic INCR + DECR-on-breach (CR-04 reserve-before-call hardening)
    # Uses per-UF key if rio.uf is available; falls back to global key
    # ------------------------------------------------------------------
    ramp_cap: int = getattr(settings, "ramp_cap", 50)
    uf = getattr(rio, "uf", None)
    check_and_increment_ramp(redis_client, cap=ramp_cap, uf=None)
    # Note: per D-07 and RESEARCH.md Pitfall 4, the GLOBAL cap is checked (uf=None)
    # because WhatsApp portfolio limits are portfolio-wide (post Oct 2025).
    # Per-UF cap is an optional additional layer; not enforced here by default.
    # The uf variable is captured for future per-UF layering.
    _ = uf  # used for context/logging; global gate runs above

    # ------------------------------------------------------------------
    # Condition 8: Quality rating not RED
    # Redis flag wa:quality_red must not be set (RESEARCH.md §Quality Rating Auto-Pause)
    # ------------------------------------------------------------------
    if is_quality_red(redis_client):
        raise ComplianceError(
            "quality: WhatsApp quality rating is RED (wa:quality_red flag set in Redis). "
            "All sends are auto-paused until rating recovers to GREEN or YELLOW. "
            "Check Twilio/Meta quality dashboard and clear the flag when resolved."
        )

    # ------------------------------------------------------------------
    # All 8 conditions passed — gate open
    # ------------------------------------------------------------------
    logger.info(
        "send_path_gate_passed",
        phone_prefix=contact_phone[:5],
        template=template_name,
        sub_state=rio.sub_state,
        uf=getattr(rio, "uf", None),
    )
    return None
