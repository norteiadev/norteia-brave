"""Dashboard read-aggregation surface (D-01, DASH-01..05).

A thin, read-only FastAPI router the operations dashboard (Territorial CMS) reads
through its BFF — so the UI never touches the database directly (D-01). Every
endpoint is Bearer-guarded (require_bearer, D-02) and performs no pipeline logic:
it only reads existing medallion + observability tables.

Endpoints:
  GET /api/v1/dlq/{rio_id} — full DLQ detail (DASH-01): the per-criterion §7.6
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

    Surfaces what the list endpoint omits: the §7.6 per-criterion score_breakdown
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
        # §7.6 per-criterion breakdown — the DASH-01 explainability panel source.
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
    failures = db.scalar(select(func.count(PoisonQuarantine.id))) or 0
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
