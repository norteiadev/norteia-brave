"""Varreduras endpoints — durable engine-run trail + reprocess (UI-PAINEL-2).

Two surfaces for the Painel "Varreduras" view, backed by the runs_history table
(written at engine-start, finalized when the sweep idles — see engine.py /
pipeline.py write points):

  - GET  /api/v1/runs                         (require_bearer)
        Paginated list of runs filtered by uf/source/depth. synced/failed/total
        are computed ON-READ over each run's [started_at, ended_at] window
        (RESEARCH #2) — the async producer tasks never return counts, so the
        envelope recomputes them from the medallion tables at read time.

  - PATCH /api/v1/runs/{run_id}/reprocess     (require_steward_or_bearer) → 202
        Re-run the run's scope (ufs, source, depth, lane, per-UF cap) as a NEW engine
        run, audited. v1 re-runs the SCOPE (not a precise failed-record replay —
        per-record replay would need a runs_failures join table, DEFERRED).

On-read approximation (A4): a record from a prior run re-touched inside this run's
window could be counted. Acceptable for an ops dashboard.

Security: reads → require_bearer; reprocess (mutation) → require_steward_or_bearer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from redis import Redis
from sqlalchemy import DateTime, Integer, column, func, select, values
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, require_bearer, require_steward_or_bearer
from brave.core.models import MarRecord, PoisonQuarantine, RioRecord, RunHistory
from brave.observability.audit import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models (A5 typed contract — extra="forbid", mirrored by the MSW handler)
# ---------------------------------------------------------------------------


class RunItem(BaseModel):
    """A single engine run with on-read synced/failed/total."""

    model_config = {"extra": "forbid"}

    id: str
    started_at: str
    ended_at: str | None
    ufs: list[str]
    source: str
    depth: str
    total: int
    synced: int
    failed: int
    status: str


class RunsResponse(BaseModel):
    """Paginated envelope for GET /api/v1/runs."""

    model_config = {"extra": "forbid"}

    items: list[RunItem]
    total: int
    offset: int
    limit: int


# ---------------------------------------------------------------------------
# On-read window aggregation (synced / failed)
# ---------------------------------------------------------------------------


def _window_counts(db: Session, runs: list[RunHistory]) -> dict[uuid.UUID, tuple[int, int]]:
    """Count synced / failed over each run's [started_at, ended_at] window (A4).

    synced = MarRecord rows published in the window (models.py:224).
    failed = RioRecord rows routed dlq/descarte with processed_at in the window
             (models.py:147) PLUS PoisonQuarantine rows in the window (models.py:310).

    One grouped query per table for the WHOLE page (the run windows travel as a
    VALUES list), instead of three COUNTs per run.

    When ended_at is None (run still finalizing) the window's upper bound is now().
    This is a deliberate time-window approximation — see the module docstring (A4).
    """
    if not runs:
        return {}
    now = datetime.now(timezone.utc)
    w = values(
        column("i", Integer),
        column("lo", DateTime(timezone=True)),
        column("hi", DateTime(timezone=True)),
        name="w",
    ).data([(i, r.started_at, r.ended_at or now) for i, r in enumerate(runs)])

    def _grouped(model, ts, *where) -> dict[int, int]:
        stmt = (
            select(w.c.i, func.count())
            .select_from(w)
            .join(model, ts.between(w.c.lo, w.c.hi))
            .where(*where)
            .group_by(w.c.i)
        )
        return {i: n for i, n in db.execute(stmt).all()}

    synced = _grouped(MarRecord, MarRecord.published_at)
    failed_rio = _grouped(
        RioRecord, RioRecord.processed_at, RioRecord.routing.in_(("dlq", "descarte"))
    )
    failed_poison = _grouped(PoisonQuarantine, PoisonQuarantine.quarantined_at)
    return {
        r.id: (synced.get(i, 0), failed_rio.get(i, 0) + failed_poison.get(i, 0))
        for i, r in enumerate(runs)
    }


def _to_item(run: RunHistory, synced: int, failed: int) -> RunItem:
    """Build a RunItem from a run + its on-read synced/failed counts."""
    return RunItem(
        id=str(run.id),
        started_at=run.started_at.isoformat() if run.started_at else "",
        ended_at=run.ended_at.isoformat() if run.ended_at else None,
        ufs=list(run.ufs or []),
        source=run.source,
        depth=run.depth,
        # total recomputed on-read: records that reached a terminal state in-window.
        total=synced + failed,
        synced=synced,
        failed=failed,
        status=run.status,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/v1/runs", dependencies=[Depends(require_bearer)])
def list_runs(
    uf: str | None = Query(None),
    source: str | None = Query(None),
    depth: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """List engine runs (newest first) with on-read synced/failed aggregation.

    source/depth are scalar columns filtered in SQL. uf is a member of the JSON
    `ufs` array, so it is filtered in Python over the (bounded, ops-scale) run set
    — this keeps the read path portable (no JSONB operator) and offline-testable.
    """
    stmt = select(RunHistory)
    if source:
        stmt = stmt.where(RunHistory.source == source)
    if depth:
        stmt = stmt.where(RunHistory.depth == depth)
    stmt = stmt.order_by(RunHistory.started_at.desc())

    if uf:
        runs = [r for r in db.scalars(stmt).all() if uf in (r.ufs or [])]
        total = len(runs)
        page = runs[offset : offset + limit]
    else:
        # No JSON-array filter → page in SQL instead of loading every run.
        total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        page = list(db.scalars(stmt.offset(offset).limit(limit)).all())

    counts = _window_counts(db, page)
    items = [_to_item(run, *counts[run.id]) for run in page]

    return RunsResponse(items=items, total=total, offset=offset, limit=limit).model_dump()


@router.patch(
    "/api/v1/runs/{run_id}/reprocess",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def reprocess_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Re-run a run's scope as a NEW engine run, audited (T-17.1-02-04).

    Goes through the same path as POST /engine/start: collection_engine.start (409 while
    a run is active — never a second fan-out beside it) + engine_sweep_run with the run's
    own depth, lane, source and per-UF cap, so the producers carry a run_id and honor the
    motor's mode. A describe run re-describes, a backfill run restarts the backfill. The
    old loose ``.delay(uf)`` dropped depth/cap/run_id: a capped ``nascente`` test run came
    back as a full sweep with paid Places per card.
    """
    from brave.config.runtime import enabled_sources, load_effective_config
    from brave.config.settings import AppConfig
    from brave.core import engine as collection_engine
    from brave.tasks.pipeline import engine_sweep_run, start_backfill_run

    run = db.get(RunHistory, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="RunHistory not found")

    ufs = list(run.ufs or [])
    busy = HTTPException(
        status_code=409, detail="Engine already running — stop it before reprocessing."
    )

    if run.source == "ta_backfill":
        try:
            new_id = start_backfill_run(redis, db)
        except Exception as exc:
            logger.error("run_reprocess_dispatch_failed", error_type=type(exc).__name__)
            raise HTTPException(status_code=503, detail="Reprocess dispatch failed.") from exc
        if new_id is None:
            raise busy
    else:
        action = {"descricao": "describe", "imagens": "images"}.get(run.source, "sweep")
        valid_sources = None
        if action == "sweep":
            if run.depth not in collection_engine.VALID_DEPTHS:
                raise HTTPException(status_code=422, detail=f"run depth {run.depth!r} not replayable")
            valid_sources = enabled_sources(load_effective_config(db))
        try:
            new_id = collection_engine.start(
                redis,
                db,
                action=action,
                depth=run.depth,
                source=run.source,
                ufs=ufs,
                lane=run.lane,
                valid_sources=valid_sources,
            )
        except ValueError as exc:  # the run's source is disabled/unknown now
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if new_id is None:
            raise busy
        kwargs = dict(ufs=ufs, lane=run.lane, run_id=new_id, max_per_uf=run.max_per_uf)
        if action != "sweep":
            kwargs["action"] = action
        else:
            kwargs.update(depth=run.depth, source=run.source)
        try:
            engine_sweep_run.delay(**kwargs)
        except Exception as exc:  # broker down — same contract as POST /engine/start
            if AppConfig().run_real_externals:
                collection_engine.abort(redis, db, new_id)
                logger.error("run_reprocess_dispatch_failed", error_type=type(exc).__name__)
                raise HTTPException(
                    status_code=503,
                    detail="Reprocess dispatch failed (broker unavailable). Retry once reachable.",
                ) from exc

    write_audit(
        session=db,
        action="run_reprocessed",
        entity_type="run",
        record_id=run.id,
        before_state={"status": run.status},
        after_state={
            "reprocessed_ufs": ufs,
            "source": run.source,
            "lane": run.lane,
            "depth": run.depth,
            "max_per_uf": run.max_per_uf,
            "new_run_id": new_id,
        },
        actor="steward",
    )
    db.commit()

    return {"status": "accepted", "run_id": str(run.id), "new_run_id": new_id, "ufs": ufs}
