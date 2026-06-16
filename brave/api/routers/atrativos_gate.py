"""WhatsApp gate endpoints for Atrativos lane (D-06, ATR-05, COMP-01/02).

Five endpoints:
  GET  /api/v1/atrativos/gate                              — list aguardando queue
  PATCH /api/v1/atrativos/gate/{rio_id}/approve            — approve (steward auth req.)
  PATCH /api/v1/atrativos/gate/{rio_id}/reject             — reject to DLQ (steward auth req.)
  POST  /api/v1/atrativos/whatsapp/quality-rating-webhook  — Twilio quality-rating event
  POST  /api/v1/atrativos/whatsapp/inbound                 — relay inbound reply to task

Structural template: brave/api/routers/dlq.py (Phase 2 DLQ steward pattern, D-06).
require_steward copied verbatim from dlq.py (T-02-06-01 / CR-01 carried forward as T-03-03-01).

Security:
  - /approve and /reject require X-Steward-Secret (fail-closed — T-03-03-01)
  - /quality-rating-webhook and /inbound: no steward auth in this plan
    (Twilio signature verification is a production add-on — documented in docstring
    per T-03-03-05; endpoint is behind FastAPI, not public-facing without infra auth)

D-06 gate pattern:
  - GET lists sub_state=aguardando_consulta_whatsapp + entity_type=attraction
  - PATCH /approve: flip sub_state → whatsapp_in_progress, dispatch outreach_task
  - PATCH /reject: set routing=dlq, sub_state=None

D-07 ramp counter: enforced by send_path_gate (outreach_task layer), not here.
  The approve endpoint only authorizes the dispatch — the gate runs in outreach_task.
"""

import hmac
import uuid

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.deps import (
    get_config,
    get_db,
    get_redis,
    get_steward_config,
    get_webhook_config,
    require_bearer,
    require_steward_or_bearer,
)
from brave.compliance.gate import ramp_key
from brave.compliance.quality_rating import is_quality_red
from brave.config.settings import AppConfig, StewardConfig, WebhookConfig
from brave.core.models import RioRecord, mask_phone
from brave.observability.audit import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth dependency — copied verbatim from dlq.py (T-03-03-01)
# ---------------------------------------------------------------------------


def require_steward(
    x_steward_secret: str | None = Header(None, alias="X-Steward-Secret"),
    steward_config: StewardConfig = Depends(get_steward_config),
) -> None:
    """Authenticate a steward on the mutating WhatsApp gate endpoints (T-03-03-01).

    Mirrors dlq.py require_steward exactly (Phase 2 steward pattern carried forward):
    constant-time hmac.compare_digest, fail-closed (unset secret rejects all callers),
    401 before any DB work, secret never logged.

    /approve and /reject are write-to-production operations (advance sub_state,
    dispatch outreach Celery task) — same trust boundary as DLQ validate.
    """
    expected = steward_config.secret
    if not x_steward_secret or not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Steward-Secret header required",
        )
    if not hmac.compare_digest(x_steward_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Steward-Secret",
        )


def require_webhook(
    x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
    webhook_config: WebhookConfig = Depends(get_webhook_config),
) -> None:
    """Authenticate the mutating WhatsApp webhook endpoints (WR-03).

    The quality-rating and inbound webhooks drive the compliance gate (the global
    RED send-pause flag) and the promotion path (an injected inbound body can
    trigger owner_confirmed → Mar or a forced opt-out). They MUST NOT be
    unauthenticated: an attacker who can reach the service could clear a
    legitimate RED pause (resume sends during a quality incident), set a spurious
    RED (DoS the pipeline), or spoof inbound replies into a victim's conversation.

    Enforces a static shared secret (X-Webhook-Secret) with the same fail-closed,
    constant-time discipline as require_steward and the error-report webhook
    (T-02-01). Production should additionally layer Twilio RequestValidator
    signature verification; this is the enforced minimum, not a deferred TODO.
    """
    expected = webhook_config.secret
    if not x_webhook_secret or not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Webhook-Secret header required",
        )
    if not hmac.compare_digest(x_webhook_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Webhook-Secret",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_int(raw) -> int:
    """Parse a Redis counter value to int, returning 0 on any non-int payload (WR-01).

    A corrupted or manually-set ramp/quality counter (non-numeric, wrong type)
    must NOT 500 the read-only advisory endpoints — the operator needs the gate
    panel readable during exactly the incident window the bad value might appear in.
    """
    try:
        return int(raw) if raw is not None else 0
    except (ValueError, TypeError):
        return 0


def _safe_normalized(normalized: dict | None) -> dict:
    """Return a copy of the Rio `normalized` dict with the raw phone_e164 masked (CR-01).

    The Rio `normalized` payload stores the owner's raw E.164 number at
    normalized["contacts"]["phone_e164"]. This endpoint MUST NEVER emit that raw
    number (LGPD, R3). Replace it with a masked form so the dashboard receives only
    `phone_masked`, matching gate-api.ts's documented contract.
    """
    n = dict(normalized or {})
    contacts = n.get("contacts")
    if isinstance(contacts, dict) and "phone_e164" in contacts:
        contacts = dict(contacts)
        contacts["phone_masked"] = mask_phone(contacts.pop("phone_e164", None))
        n["contacts"] = contacts
    return n


# ---------------------------------------------------------------------------
# GET /api/v1/atrativos/gate — list aguardando_consulta_whatsapp queue
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/atrativos/gate",
    dependencies=[Depends(require_bearer)],
)
def list_whatsapp_gate_queue(
    uf: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List atrativos awaiting human WhatsApp gate approval.

    Returns RioRecord rows where:
      - entity_type = "attraction"
      - sub_state   = "aguardando_consulta_whatsapp"

    Filtered by optional uf query param.

    Bearer-guarded (require_bearer, CR-01): the 401 fires before any DB work —
    same trust boundary as every other dashboard read endpoint (dashboard.py).
    The Phase 4 dashboard drives the human review UX from this endpoint.

    LGPD (R3, CR-01): the raw phone_e164 in `normalized["contacts"]` is NEVER
    emitted — it is masked to `phone_masked` via `_safe_normalized`, and a
    top-level `phone_masked` field mirrors gate-api.ts's documented contract.

    Args:
        uf:    Optional two-letter UF code to filter by state.
        limit: Max records to return (default 50, max 500).
        db:    SQLAlchemy Session (injected by FastAPI).

    Returns:
        List of dicts with rio_id, uf, entity_type, sub_state, dlq_reason,
        normalized (phone-masked), phone_masked, score, score_version fields.
    """
    query = select(RioRecord).where(
        RioRecord.entity_type == "attraction",
        RioRecord.sub_state == "aguardando_consulta_whatsapp",
    )
    if uf:
        query = query.where(RioRecord.uf == uf)
    query = query.limit(limit)

    rows = list(db.scalars(query).all())
    return [
        {
            "rio_id": str(r.id),
            "nascente_id": str(r.nascente_id),
            "entity_type": r.entity_type,
            "uf": r.uf,
            "sub_state": r.sub_state,
            "routing": r.routing,
            "dlq_reason": r.dlq_reason,
            "score": float(r.score) if r.score is not None else None,
            "score_version": r.score_version,
            "canonical_key": r.canonical_key,
            # CR-01: never emit the raw normalized dict — mask phone_e164.
            "normalized": _safe_normalized(r.normalized),
            "phone_masked": mask_phone(
                ((r.normalized or {}).get("contacts") or {}).get("phone_e164")
            ),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /api/v1/atrativos/whatsapp/ramp-context — read-only ramp + quality context
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/atrativos/whatsapp/ramp-context",
    dependencies=[Depends(require_bearer)],
)
def get_ramp_context(
    uf: str | None = Query(None),
    redis: Redis = Depends(get_redis),
    config: AppConfig = Depends(get_config),
) -> dict:
    """Return the WhatsApp send-path ramp + quality-rating context (DASH-03, D-01).

    The thin read-only context the dashboard gate panel (RampContext.tsx) shows
    beside the gate queue: how much of today's volume-ramp cap remains and whether
    the WhatsApp quality rating is RED (sends auto-paused server-side).

    READ-ONLY — this endpoint NEVER mutates the ramp counter. It only reads the
    SAME Redis key the compliance gate's INCR path writes (via the shared
    `ramp_key` helper, so the read and write paths can never drift onto divergent
    key formats). No INCR/DECR here; the ramp is enforced exclusively in the Phase 3
    send path (send_path_gate, condition 7) — this view is advisory display only
    (T-04-20: no UI bypass possible).

    Reads:
      - `wa:ramp:{today-UTC}` (global daily counter) → `used` (0 if key absent).
      - optionally `wa:ramp:{UF}:{today-UTC}` when ?uf= is supplied → `per_uf`.
      - the `wa:quality_red` flag (is_quality_red) → `quality` RED|GREEN.

    Cap comes from RampConfig.daily_cap (BRAVE_WA_RAMP_DAILY_CAP, default 50).

    Bearer-guarded (require_bearer): the 401 fires before any Redis read. Returns
    aggregate counters only — no PII, no record-level data, no secrets.

    Args:
        uf:     Optional UF code to additionally report the per-state counter.
        redis:  Redis client (injected; overridable with fakeredis in tests).
        config: AppConfig (injected) — RampConfig.daily_cap source.

    Returns:
        {
          daily_cap, used, remaining, quality: "RED"|"GREEN",
          # frontend RampQualityContext compatibility (gate-api.ts):
          quality_rating, ramp_cap, ramp_used, ramp_remaining, paused,
          # optional, only when ?uf= supplied:
          per_uf: {uf, used, remaining}
        }
    """
    daily_cap = config.ramp.daily_cap

    # Read-only GET of the global daily counter — absent/corrupt key → used = 0 (WR-01).
    raw = redis.get(ramp_key(None))
    used = _safe_int(raw)
    remaining = max(0, daily_cap - used)

    is_red = is_quality_red(redis)
    quality = "RED" if is_red else "GREEN"

    body: dict = {
        # Requirement contract field names.
        "daily_cap": daily_cap,
        "used": used,
        "remaining": remaining,
        "quality": quality,
        # Frontend RampQualityContext compatibility (gate-api.ts / RampContext.tsx)
        # — same numbers under the names the panel already consumes, so the panel
        # renders real data on the happy path instead of its "indisponível" fallback.
        "quality_rating": quality,
        "ramp_cap": daily_cap,
        "ramp_used": used,
        "ramp_remaining": remaining,
        "paused": is_red,
    }

    if uf:
        uf_raw = redis.get(ramp_key(uf))
        uf_used = _safe_int(uf_raw)  # WR-01: corrupt counter → 0, never 500
        body["per_uf"] = {
            "uf": uf,
            "used": uf_used,
            "remaining": max(0, daily_cap - uf_used),
        }

    return body


# ---------------------------------------------------------------------------
# PATCH /api/v1/atrativos/gate/{rio_id}/approve
# ---------------------------------------------------------------------------


@router.patch(
    "/api/v1/atrativos/gate/{rio_id}/approve",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def approve_whatsapp_gate(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward approves an atrativo for WhatsApp outreach.

    Advances sub_state from 'aguardando_consulta_whatsapp' → 'whatsapp_in_progress',
    then dispatches outreach_task (Celery) with sync fallback (no broker in tests/dev).

    Returns 409 if sub_state is already past 'aguardando_consulta_whatsapp'
    (idempotency guard — prevent double-dispatch on concurrent requests).

    The compliance gate (D-11) runs INSIDE outreach_task — this endpoint only
    authorizes the dispatch. The gate will block the actual send if conditions fail.

    Writes audit row: action='whatsapp_gate_approved', actor='steward'.

    Args:
        rio_id: UUID of the RioRecord to approve.
        db:     SQLAlchemy Session (injected by FastAPI).

    Returns:
        {"status": "accepted", "rio_id": str(rio_id)}, HTTP 202.

    Raises:
        HTTPException 404: RioRecord not found.
        HTTPException 409: Already processed (sub_state != aguardando_consulta_whatsapp).
    """
    # CR-04: lock the row (SELECT ... FOR UPDATE) so two concurrent approve
    # requests for the same rio_id cannot both pass the 409 guard and both
    # dispatch an outreach_task. The second blocks on the lock, re-reads the
    # advanced sub_state, and gets a 409.
    rio = db.get(RioRecord, rio_id, with_for_update=True)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    if rio.sub_state != "aguardando_consulta_whatsapp":
        raise HTTPException(
            status_code=409,
            detail=(
                f"RioRecord sub_state is '{rio.sub_state}', "
                "expected 'aguardando_consulta_whatsapp'. Already processed?"
            ),
        )

    before_state = {"sub_state": rio.sub_state}
    rio.sub_state = "whatsapp_in_progress"

    # Dispatch Celery task. WR-02: a "no broker in tests/dev" failure is benign,
    # but a broker-down failure in production leaves the record stuck in
    # whatsapp_in_progress with no outreach ever dispatched and invisible to the
    # gate queue. Distinguish the two: swallow only when run_real_externals is
    # False (offline), surface (error log + 503) in a real environment.
    try:
        from brave.tasks.pipeline import outreach_task
        outreach_task.delay(str(rio_id))
    except Exception as exc:
        from brave.config.settings import AppConfig

        if AppConfig().run_real_externals:
            logger.error(
                "outreach_dispatch_failed",
                rio_id=str(rio_id),
                error=str(exc),
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Outreach dispatch failed (broker unavailable). "
                    "Approval not committed — retry once the broker is reachable."
                ),
            ) from exc
        # Offline (tests/dev): no broker is expected — outreach logic is exercised
        # by invoking the task directly in tests.

    write_audit(
        session=db,
        action="whatsapp_gate_approved",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"sub_state": "whatsapp_in_progress"},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id)}


# ---------------------------------------------------------------------------
# PATCH /api/v1/atrativos/gate/{rio_id}/reject
# ---------------------------------------------------------------------------


@router.patch(
    "/api/v1/atrativos/gate/{rio_id}/reject",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
def reject_whatsapp_gate(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward rejects an atrativo from the WhatsApp gate (routes to DLQ).

    Sets:
      rio.sub_state = None       (removed from gate queue)
      rio.routing   = "dlq"     (routed for DLQ review)
      rio.dlq_reason = "steward_rejected_gate"

    Writes audit row: action='whatsapp_gate_rejected', actor='steward'.

    Args:
        rio_id: UUID of the RioRecord to reject.
        db:     SQLAlchemy Session (injected by FastAPI).

    Returns:
        {"status": "ok", "routing": "dlq", "rio_id": str(rio_id)}, HTTP 200.

    Raises:
        HTTPException 404: RioRecord not found.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"sub_state": rio.sub_state, "routing": rio.routing}
    rio.sub_state = None
    rio.routing = "dlq"
    rio.dlq_reason = "steward_rejected_gate"

    write_audit(
        session=db,
        action="whatsapp_gate_rejected",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"sub_state": None, "routing": "dlq", "dlq_reason": "steward_rejected_gate"},
        actor="steward",
    )
    return {"status": "ok", "routing": "dlq", "rio_id": str(rio_id)}


# ---------------------------------------------------------------------------
# POST /api/v1/atrativos/whatsapp/quality-rating-webhook
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/atrativos/whatsapp/quality-rating-webhook",
    dependencies=[Depends(require_webhook)],
)
def quality_rating_webhook(
    payload: dict,
    db: Session = Depends(get_db),
) -> dict:
    """Receive quality-rating change event from Twilio/Meta (T-03-03-05).

    Sets or clears the wa:quality_red Redis flag based on the rating:
      RED    → set flag  (pause all sends — compliance gate condition 8)
      GREEN  → clear flag (resume sends)
      YELLOW → clear flag (throttle ramp cap externally; no code pause)

    Production TODO (T-03-03-05): Add Twilio signature validation
    (from twilio.request_validator import RequestValidator) to prevent
    spoofed quality-rating events. This is a production hardening item —
    the endpoint is not public-facing without infra auth at launch.

    Writes audit row for every quality-rating event.

    Args:
        payload: Webhook payload dict; expected key: "quality_rating" (str).
        db:      SQLAlchemy Session (injected by FastAPI for audit write).

    Returns:
        {"status": "ok", "rating": str}, HTTP 200.
    """
    from brave.compliance.quality_rating import set_quality_flag

    rating = payload.get("quality_rating", "GREEN").upper()
    redis = get_redis()
    set_quality_flag(redis, rating)

    write_audit(
        session=db,
        action="quality_rating_updated",
        after_state={"rating": rating},
        actor="webhook",
    )
    return {"status": "ok", "rating": rating}


# ---------------------------------------------------------------------------
# POST /api/v1/atrativos/whatsapp/inbound
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/atrativos/whatsapp/inbound",
    dependencies=[Depends(require_webhook)],
)
def inbound_whatsapp_reply(
    payload: dict,
    db: Session = Depends(get_db),
) -> dict:
    """Relay inbound WhatsApp reply to the conversation task (n8n thin transport, D-08).

    n8n (or Twilio webhook) relays inbound messages here. This endpoint:
      1. Extracts from_number and body from the payload.
      2. Looks up the active rio_id from consent_log (T-03-03-08: only rio_id forwarded).
      3. Dispatches resume_conversation_task(rio_id, message_text) with Celery.

    If no active conversation is found for this phone number, returns {"status": "ignored"}.

    PII minimization (T-03-03-08): only (rio_id, message_text) is passed to the task.
    The phone number is looked up locally and NOT forwarded to the LLM or task args.

    Production TODO: Add Twilio signature validation before parsing payload
    (same note as quality-rating-webhook — not public-facing without infra auth).

    Args:
        payload: Webhook payload dict; expected keys: "from" (E.164), "body" (str).
        db:      SQLAlchemy Session (injected by FastAPI for consent_log lookup).

    Returns:
        {"status": "accepted"} if conversation found, or
        {"status": "ignored", "reason": "no_active_conversation"} if not.
    """
    from brave.compliance.consent_log import lookup_rio_id_by_phone

    from_number: str = payload.get("from", "")
    message_text: str = payload.get("body", "")

    # Lookup active conversation by phone (T-03-03-08: phone not forwarded to task)
    rio_id = lookup_rio_id_by_phone(db, from_number)
    if rio_id is None:
        return {"status": "ignored", "reason": "no_active_conversation"}

    # Dispatch Celery task. WR-02: swallow only the expected "no broker offline"
    # case; surface a real broker-down failure so an inbound reply is never
    # silently dropped (the conversation would stall forever otherwise).
    try:
        from brave.tasks.pipeline import resume_conversation_task
        resume_conversation_task.delay(str(rio_id), message_text)
    except Exception as exc:
        from brave.config.settings import AppConfig

        if AppConfig().run_real_externals:
            logger.error(
                "resume_dispatch_failed",
                rio_id=str(rio_id),
                error=str(exc),
            )
            raise HTTPException(
                status_code=503,
                detail="Inbound dispatch failed (broker unavailable). Retry delivery.",
            ) from exc
        # Offline (tests/dev): no broker is expected.

    return {"status": "accepted"}
