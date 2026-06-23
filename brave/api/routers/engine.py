"""Collection-engine control endpoints (operator start/stop of the Brave sweep).

The platform is up 24/7 but the collection engine is idle by default. These
endpoints let the dashboard start the full destinos+atrativos sweep, watch its
progress, and stop it gracefully (the orchestrator drains the in-flight UF and
returns to idle).

  GET  /api/v1/engine/status   — state + progress + pipeline counts (Bearer)
  POST /api/v1/engine/start    — start the sweep (steward or Bearer)
  POST /api/v1/engine/stop     — request graceful stop (steward or Bearer)

Start/stop are mutations → require_steward_or_bearer (T-05-07: an unauthenticated
caller must not be able to fan out expensive LLM/Places sweeps).
"""

from __future__ import annotations

import os

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, require_bearer, require_steward_or_bearer
from brave.core import engine as collection_engine
from brave.core.models import MarRecord, NascenteRecord, RioRecord

logger = structlog.get_logger(__name__)
router = APIRouter()

_ATRATIVO_SUB_STATES = [
    "discovered",
    "contacts_found",
    "signals_gathered",
    "aguardando_consulta_whatsapp",
    "whatsapp_in_progress",
]


def _pipeline_counts(db: Session) -> dict:
    """Per-layer counts for the engine progress feedback (mirrors /metrics)."""
    nascente = db.scalar(select(func.count(NascenteRecord.id))) or 0
    rio_rows = db.execute(
        select(RioRecord.routing, func.count(RioRecord.id)).group_by(RioRecord.routing)
    ).all()
    rio = {routing: n for routing, n in rio_rows}
    mar = db.scalar(select(func.count(MarRecord.id)).where(MarRecord.superseded_by_id.is_(None))) or 0

    atr_rows = db.execute(
        select(RioRecord.sub_state, func.count(RioRecord.id))
        .where(RioRecord.entity_type == "attraction")
        .group_by(RioRecord.sub_state)
    ).all()
    atr_by_state = {s: n for s, n in atr_rows if s is not None}

    return {
        "nascente": nascente,
        "rio": {
            "in_progress": rio.get("in_progress", 0),
            "mar": rio.get("mar", 0),
            "dlq": rio.get("dlq", 0),
            "descarte": rio.get("descarte", 0),
        },
        "mar": mar,
        "atrativos_by_sub_state": {s: atr_by_state.get(s, 0) for s in _ATRATIVO_SUB_STATES},
    }


@router.get("/api/v1/engine/status", dependencies=[Depends(require_bearer)])
def engine_status(
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Engine state + run progress + live pipeline counts (for the dashboard)."""
    status = collection_engine.get_status(redis)
    status["counts"] = _pipeline_counts(db)
    return status


@router.post(
    "/api/v1/engine/start",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def engine_start(
    redis: Redis = Depends(get_redis),
    body: dict = Body(default={}),
) -> dict:
    """Start the full sweep. Idempotent: 409 if a run is already active.

    Required body: { "depth": "nascente|nascente_rio|nascente_rio_mar", ... }.
    Optional: { "ufs": ["BA", ...], "lane": "destinos|atrativos|both" }.

    `depth` is the cost-checkpoint contract and is **required** — there is no
    implicit default, so the engine never silently spends.
    """
    from brave.tasks.beat_schedule import UF_LIST

    ufs = body.get("ufs") or list(UF_LIST)
    lane = body.get("lane", "both")

    # Validate depth BEFORE start_run (and before the already-running/409 branch):
    # a missing/invalid depth must return 422 even mid-run, never flipping engine
    # state nor first tripping 409 (T-10-02).
    depth = body.get("depth")
    if depth not in collection_engine._VALID_DEPTHS:
        raise HTTPException(
            status_code=422,
            detail="depth is required: nascente|nascente_rio|nascente_rio_mar",
        )

    if not collection_engine.start_run(redis, ufs_total=len(ufs)):
        raise HTTPException(
            status_code=409,
            detail="Engine already running — stop it before starting a new run.",
        )

    collection_engine.set_depth(redis, depth)

    try:
        from brave.tasks.pipeline import engine_sweep_run

        engine_sweep_run.delay(ufs=ufs, lane=lane, depth=depth)
    except Exception as exc:  # broker-down
        from brave.config.settings import AppConfig

        if AppConfig().run_real_externals:
            collection_engine.mark_idle(redis)  # revert — the run never launched
            logger.error("engine_start_dispatch_failed", error=str(exc))
            raise HTTPException(
                status_code=503,
                detail="Engine start failed (broker unavailable). Retry once reachable.",
            ) from exc
        # Offline (tests/dev): no broker — leave state running; orchestrator is exercised separately.

    logger.info("engine_started", ufs=len(ufs), lane=lane, depth=depth)
    return {"status": "started", "ufs_total": len(ufs), "lane": lane, "depth": depth}


@router.post(
    "/api/v1/engine/stop",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def engine_stop(redis: Redis = Depends(get_redis)) -> dict:
    """Request a graceful stop. The orchestrator drains the in-flight UF then idles."""
    if not collection_engine.request_stop(redis):
        return {"status": "noop", "detail": "engine is not running"}
    logger.info("engine_stop_requested")
    return {"status": "stopping"}
