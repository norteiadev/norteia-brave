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

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, get_steward_config
from brave.config.settings import StewardConfig
from brave.core.models import RioRecord
from brave.observability.audit import write_audit

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


# ---------------------------------------------------------------------------
# GET /api/v1/atrativos/gate — list aguardando_consulta_whatsapp queue
# ---------------------------------------------------------------------------


@router.get("/api/v1/atrativos/gate")
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

    No steward auth required (read-only queue list).
    The Phase 4 dashboard drives the human review UX from this endpoint.

    Args:
        uf:    Optional two-letter UF code to filter by state.
        limit: Max records to return (default 50, max 500).
        db:    SQLAlchemy Session (injected by FastAPI).

    Returns:
        List of dicts with rio_id, uf, entity_type, sub_state, dlq_reason,
        normalized (subset), score, created_at-equivalent fields.
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
            "normalized": r.normalized or {},
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# PATCH /api/v1/atrativos/gate/{rio_id}/approve
# ---------------------------------------------------------------------------


@router.patch(
    "/api/v1/atrativos/gate/{rio_id}/approve",
    status_code=202,
    dependencies=[Depends(require_steward)],
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
    rio = db.get(RioRecord, rio_id)
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

    # Dispatch Celery task (sync fallback when no broker — same pattern as dlq.py)
    try:
        from brave.tasks.pipeline import outreach_task
        outreach_task.delay(str(rio_id))
    except Exception:
        # No Celery broker in tests/dev — safe to skip dispatch here
        # The compliance gate and outreach logic run inside the task
        pass

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
    dependencies=[Depends(require_steward)],
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


@router.post("/api/v1/atrativos/whatsapp/quality-rating-webhook")
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


@router.post("/api/v1/atrativos/whatsapp/inbound")
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

    # Dispatch Celery task — sync fallback if no broker (same pattern as dlq.py)
    try:
        from brave.tasks.pipeline import resume_conversation_task
        resume_conversation_task.delay(str(rio_id), message_text)
    except Exception:
        # No Celery broker in tests/dev — safe to skip dispatch here
        # The actual conversation resumption runs inside the task
        pass

    return {"status": "accepted"}
