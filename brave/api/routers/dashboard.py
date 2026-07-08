"""Dashboard read-aggregation surface (D-01, DASH-01..05).

A thin, read-only FastAPI router the operations dashboard (Territorial CMS) reads
through its BFF — so the UI never touches the database directly (D-01). Every
endpoint is Bearer-guarded (require_bearer, D-02) and performs no pipeline logic:
it only reads existing medallion + observability tables.

Endpoints:
  GET /api/v1/dlq/{rio_id} — full DLQ detail (DASH-01): the per-criterion reliability
      score_breakdown + Rio normalized + Nascente raw payload + signals + the
      per-record WhatsApp/steward event log. The existing GET /api/v1/dlq list
      (dlq.py) deliberately omits these heavier fields; this surfaces them.

Later plans accrete the monitor/cost/funnels/conversations read endpoints onto
this same router.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, require_bearer
from brave.compliance.quality_rating import is_quality_red
from brave.core.models import (
    AuditLog,
    ConversationMessage,
    LLMGeneration,
    MarRecord,
    NascenteRecord,
    PoisonQuarantine,
    RioRecord,
)

router = APIRouter()

# Routing values, kept in sync with metrics.py so the monitor `volume` block
# mirrors the existing /metrics per-layer shape (pre-seeded, never missing keys).
ROUTING_VALUES = ["in_progress", "mar", "dlq", "descarte"]

# The audit actions whose windowed counts become the DASH-02 approval/rejection/
# DLQ rates. THIS is the DASH-02 "audit" coverage — derived from AuditLog, not a
# separate raw audit feed (see plan INFO note).
RATE_ACTIONS = ["dlq_validated", "dlq_rejected", "dlq_reprocessed"]


def _extract_signals(
    normalized: dict | None, payload: dict | None
) -> dict:
    """Pull the SignalAgent signals block from the Rio normalized or Nascente payload.

    Read-only best-effort: the SignalAgent stores its signals under a "signals"
    key. Prefer the normalized (post-processing) view, fall back to the raw
    Nascente payload, default to an empty dict. No PII is surfaced here — this
    lane (destinos/atrativos pre-contact) carries no phone PII in the DLQ detail
    (T-04-10); phone masking is enforced in the plan-07 conversation/gate reads.
    """
    for source in (normalized, payload):
        if isinstance(source, dict):
            signals = source.get("signals")
            if isinstance(signals, dict):
                return signals
    return {}


@router.get("/api/v1/dlq/{rio_id}", dependencies=[Depends(require_bearer)])
def get_dlq_detail(rio_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Return the full DLQ detail for a single Rio record (DASH-01, D-01).

    Surfaces what the list endpoint omits: the reliability per-criterion score_breakdown
    (the explainability panel source), the Rio normalized view, the joined raw
    Nascente payload, the extracted signals, and the per-record WhatsApp/steward
    event log (AuditLog rows for this rio_id, oldest-first).

    Read-only (db.get + select; no writes, no pipeline mutation). Bearer-guarded:
    the 401 fires before any DB work. Unknown rio_id → 404 (dlq.py idiom).
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="RioRecord not found"
        )

    nascente = db.get(NascenteRecord, rio.nascente_id)
    nascente_payload = nascente.payload if nascente else {}

    whatsapp_rows = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.record_id == rio.id)
            .order_by(AuditLog.created_at.asc())
        ).all()
    )

    return {
        "id": str(rio.id),
        "routing": rio.routing,
        "sub_state": rio.sub_state,
        "dlq_reason": rio.dlq_reason,
        "score": float(rio.score) if rio.score is not None else None,
        "score_version": rio.score_version,
        # reliability per-criterion breakdown — the DASH-01 explainability panel source.
        "score_breakdown": rio.score_breakdown or {},
        "normalized": rio.normalized or {},
        "nascente_payload": nascente_payload,
        "signals": _extract_signals(rio.normalized, nascente_payload),
        "whatsapp_log": [
            {
                "id": str(row.id),
                "action": row.action,
                "actor": row.actor,
                "before_state": row.before_state,
                "after_state": row.after_state,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in whatsapp_rows
        ],
    }


@router.get("/api/v1/monitor", dependencies=[Depends(require_bearer)])
def get_monitor(
    since_hours: int = Query(24, ge=0, le=24 * 30),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Return the Brave monitor aggregate (DASH-02, §15.7, D-01).

    Read-only operational read for the ops dashboard. Composes four blocks over a
    rolling window (default 24h, capped at 30 days):

      volume     — per-layer record counts (same shape as /metrics: nascente_count,
                   rio_count grouped by routing pre-seeded to 0, mar_count).
      rates      — approval/rejection/DLQ proportions derived from AuditLog action
                   counts (dlq_validated / dlq_rejected / dlq_reprocessed) over the
                   window. THIS is the DASH-02 "audit" coverage — folded into rates
                   rather than shipped as a separate raw audit feed (plan INFO).
                   Each value is the action's share of the windowed action total in
                   [0, 1]; pre-seeded to 0.0 so a key is never missing.
      throughput — count of RioRecord rows whose processed_at falls in the window.
      alerts     — {failures: PoisonQuarantine row count, quality: RED WhatsApp
                   quality flag from Redis}. Surfaces operational failure signals.

    Bearer-guarded (require_bearer): the 401 fires before any DB work. No pipeline
    logic, no writes (D-01) — only reads of existing medallion + observability
    tables plus the Redis quality flag. Returns aggregate counts/rates only; no PII,
    no record-level data (threat T-04-16 accept).
    """
    window_start = datetime.now(UTC) - timedelta(hours=since_hours)

    # --- volume: per-layer counts (mirror metrics.py) ---------------------
    nascente_count = db.scalar(select(func.count(NascenteRecord.id))) or 0
    rio_count: dict[str, int] = {v: 0 for v in ROUTING_VALUES}
    for routing, count in db.execute(
        select(RioRecord.routing, func.count(RioRecord.id)).group_by(RioRecord.routing)
    ).fetchall():
        if routing in rio_count:
            rio_count[routing] = count
    mar_count = db.scalar(select(func.count(MarRecord.id))) or 0

    # --- rates: AuditLog-derived proportions over the window (DASH-02 audit) ---
    action_counts: dict[str, int] = {a: 0 for a in RATE_ACTIONS}
    for action, count in db.execute(
        select(AuditLog.action, func.count(AuditLog.id))
        .where(
            AuditLog.action.in_(RATE_ACTIONS),
            AuditLog.created_at >= window_start,
        )
        .group_by(AuditLog.action)
    ).fetchall():
        if action in action_counts:
            action_counts[action] = count
    total_actions = sum(action_counts.values())
    rates: dict[str, float] = {
        a: (action_counts[a] / total_actions if total_actions else 0.0)
        for a in RATE_ACTIONS
    }

    # --- throughput: Rio records processed in the window ------------------
    throughput = (
        db.scalar(
            select(func.count(RioRecord.id)).where(
                RioRecord.processed_at >= window_start
            )
        )
        or 0
    )

    # --- alerts: poison-quarantine failures + RED WhatsApp quality flag --
    # WR-02: window the failures count on quarantined_at >= window_start so the
    # alert reflects "is something failing right now" (the selected since_hours
    # window) instead of a monotonic all-time count behind a windowed block.
    failures = (
        db.scalar(
            select(func.count(PoisonQuarantine.id)).where(
                PoisonQuarantine.quarantined_at >= window_start
            )
        )
        or 0
    )
    quality_red = is_quality_red(redis)

    return {
        "since_hours": since_hours,
        "window_start": window_start.isoformat(),
        "volume": {
            "nascente_count": nascente_count,
            "rio_count": rio_count,
            "mar_count": mar_count,
        },
        "rates": rates,
        "rate_counts": action_counts,
        "throughput": throughput,
        "alerts": {
            "failures": failures,
            "quality": quality_red,
        },
    }


# Valid group_by columns for the cost aggregation — lane or model_slug. Anything
# else falls back to model_slug (defensive; the UI only sends "lane"/"model").
_COST_GROUP_COLUMNS = {
    "lane": LLMGeneration.lane,
    "model": LLMGeneration.model_slug,
}


@router.get("/api/v1/cost", dependencies=[Depends(require_bearer)])
def get_cost(
    group_by: str = Query("lane"),
    since: datetime | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Return LLM spend aggregated by lane or model over llm_generations (DASH-04, D-01).

    A straight GROUP BY — no pipeline logic, no writes (D-01). For each group it
    returns the summed USD cost, the summed token usage (prompt + completion), and
    the call count, optionally restricted to rows whose `created_at >= since`:

      group_by  — "lane" groups by LLMGeneration.lane; anything else (e.g. "model")
                  groups by LLMGeneration.model_slug. The cost view drives the
                  spend-per-lane and spend-per-model charts off these two modes.
      since     — optional ISO timestamp; when present, only rows created at/after
                  it are aggregated (the windowed cost view). Absent → all rows.

    Bearer-guarded (require_bearer): the 401 fires before any DB work. Returns
    aggregate USD/token sums grouped by lane/model only — no per-record content, no
    PII, no secrets (threat T-04-22 accept). An empty window yields rows == [].
    """
    col = _COST_GROUP_COLUMNS.get(group_by, LLMGeneration.model_slug)

    stmt = select(
        col,
        func.sum(LLMGeneration.usd_cost),
        func.sum(LLMGeneration.prompt_tokens + LLMGeneration.completion_tokens),
        func.count(LLMGeneration.id),
    ).group_by(col)
    if since is not None:
        stmt = stmt.where(LLMGeneration.created_at >= since)

    rows = db.execute(stmt).fetchall()

    return {
        "group_by": group_by,
        "rows": [
            {
                "key": key,
                "usd_cost": float(cost or 0),
                "tokens": int(tok or 0),
                "count": int(n),
            }
            for key, cost, tok, n in rows
        ],
    }


@router.get("/api/v1/funnels", dependencies=[Depends(require_bearer)])
def get_funnels(
    entity_type: str | None = Query(None),
    uf: str | None = Query(None),
    source: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Return destinos/atrativos funnel counts by UF/source across pipeline stages (DASH-05, D-01).

    A pure GROUP BY aggregation over the medallion layers — no pipeline logic, no
    writes (D-01). It composes three stage blocks, each honoring the optional
    entity_type/uf/source filters via the `if uf: query = query.where(...)` idiom
    from dlq.py:

      ingested    — NascenteRecord counts grouped by (source, uf, entity_type):
                    the top of the funnel (every raw record that entered).
      routing     — RioRecord counts grouped by (routing, uf): the working-area
                    distribution across in_progress / mar / dlq / descarte — the
                    in_progress → mar/dlq/descarte split the funnel view renders.
      published   — MarRecord count (entity_type-filterable): the bottom of the
                    funnel (canonical records that reached Mar).

    Bearer-guarded (require_bearer): the 401 fires before any DB work. Returns
    aggregate counts by UF/source/entity_type only — no PII, no record content
    (threat T-04-27 accept).
    """
    # --- ingested: NascenteRecord grouped by (source, uf, entity_type) --------
    ing_stmt = select(
        NascenteRecord.source,
        NascenteRecord.uf,
        NascenteRecord.entity_type,
        func.count(NascenteRecord.id),
    ).group_by(
        NascenteRecord.source, NascenteRecord.uf, NascenteRecord.entity_type
    )
    if source:
        ing_stmt = ing_stmt.where(NascenteRecord.source == source)
    if uf:
        ing_stmt = ing_stmt.where(NascenteRecord.uf == uf)
    if entity_type:
        ing_stmt = ing_stmt.where(NascenteRecord.entity_type == entity_type)
    ingested = [
        {
            "source": s,
            "uf": u,
            "entity_type": et,
            "count": int(n),
        }
        for s, u, et, n in db.execute(ing_stmt).fetchall()
    ]

    # --- routing: RioRecord grouped by (routing, uf) --------------------------
    # routing pre-seeded per uf so a stage key is never missing for a present uf.
    rio_stmt = select(
        RioRecord.routing,
        RioRecord.uf,
        func.count(RioRecord.id),
    ).group_by(RioRecord.routing, RioRecord.uf)
    if uf:
        rio_stmt = rio_stmt.where(RioRecord.uf == uf)
    if entity_type:
        rio_stmt = rio_stmt.where(RioRecord.entity_type == entity_type)
    routing = [
        {"routing": r, "uf": u, "count": int(n)}
        for r, u, n in db.execute(rio_stmt).fetchall()
    ]

    # --- published: MarRecord terminal count ----------------------------------
    mar_stmt = select(func.count(MarRecord.id))
    if entity_type:
        mar_stmt = mar_stmt.where(MarRecord.entity_type == entity_type)
    published = db.scalar(mar_stmt) or 0

    return {
        "filters": {"entity_type": entity_type, "uf": uf, "source": source},
        "ingested": ingested,
        "routing": routing,
        "published": int(published),
    }


@router.get("/api/v1/conversations", dependencies=[Depends(require_bearer)])
def get_conversations(db: Session = Depends(get_db)) -> dict:
    """List WhatsApp conversations from the append-only conversation_message log (DASH-05, D-01).

    One entry per rio_id: the masked phone, message count, and the last message
    (direction + content + timestamp). Read-only trivial aggregation over
    conversation_message (R2 Option B) — decoupled from LangGraph checkpoints.

    LGPD (R3, T-04-24): returns only the already-masked `phone_masked` column —
    the raw E.164 number is NEVER queried or emitted here. Bearer-guarded: the 401
    fires before any DB work.
    """
    # Per-rio aggregate: count + latest created_at.
    agg = (
        select(
            ConversationMessage.rio_id,
            func.count(ConversationMessage.id).label("n"),
            func.max(ConversationMessage.created_at).label("last_at"),
        )
        .group_by(ConversationMessage.rio_id)
        .subquery()
    )

    rows = db.execute(
        select(agg.c.rio_id, agg.c.n, agg.c.last_at).order_by(agg.c.last_at.desc())
    ).fetchall()

    conversations = []
    for rio_id, n, _last_at in rows:
        last_msg = db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.rio_id == rio_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        ).first()
        conversations.append(
            {
                "rio_id": str(rio_id),
                # Masked phone only — never the raw E.164 (R3, T-04-24).
                "phone_masked": last_msg.phone_masked if last_msg else "***",
                "message_count": int(n),
                "last_message": (
                    {
                        "direction": last_msg.direction,
                        "content": last_msg.content,
                        "created_at": last_msg.created_at.isoformat()
                        if last_msg.created_at
                        else None,
                    }
                    if last_msg
                    else None
                ),
            }
        )

    return {"conversations": conversations}


@router.get(
    "/api/v1/conversations/{rio_id}", dependencies=[Depends(require_bearer)]
)
def get_conversation_detail(
    rio_id: uuid.UUID, db: Session = Depends(get_db)
) -> dict:
    """Return the full transcript for one conversation, oldest-first (DASH-05, D-01).

    The R2 Option B trivial SELECT: read conversation_message rows for the rio_id,
    ordered by created_at, and emit them with the masked phone. Unknown rio_id (no
    rows) → 404 (dlq.py idiom).

    LGPD (R3, T-04-24): every emitted row carries only the masked phone — the raw
    E.164 number is NEVER queried or returned. Bearer-guarded: the 401 fires before
    any DB work.
    """
    rows = list(
        db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.rio_id == rio_id)
            .order_by(ConversationMessage.created_at.asc())
        ).all()
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No conversation found for rio_id",
        )

    return {
        "rio_id": str(rio_id),
        # The conversation's masked phone (from the log — never raw PII, R3).
        "phone_masked": rows[0].phone_masked,
        "messages": [
            {
                "id": str(r.id),
                "direction": r.direction,
                "role": r.role,
                "content": r.content,
                "extracted": r.extracted,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
