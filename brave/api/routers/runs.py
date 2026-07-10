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
        Re-dispatch the run's (ufs, source, depth/lane) scope, audited. v1 re-runs
        the SCOPE (not a precise failed-record replay — per-record replay would
        need a runs_failures join table, DEFERRED). Reuses the prod-vs-offline
        broker fallback (mirrors sweep.py:31-54) so the offline suite runs inline.

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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, require_bearer, require_steward_or_bearer
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
# Broker-down dispatch fallback (prod-vs-offline) — verbatim from sweep.py:31-54
# ---------------------------------------------------------------------------


def _dispatch(task, uf: str, *, task_label: str) -> None:
    """Dispatch a producer task with the prod-vs-offline fallback (sweep.py:31-54).

    Swallow the broker error only when run_real_externals is False (offline: the
    task is exercised inline / by tests). In a real environment a dispatch failure
    surfaces as a 503 rather than silently dropping the reprocess fan-out.
    """
    try:
        task.delay(uf)
    except Exception as exc:
        from brave.config.settings import AppConfig

        if AppConfig().run_real_externals:
            logger.error("run_reprocess_dispatch_failed", task=task_label, uf=uf, error=str(exc))
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Reprocess dispatch failed for {task_label} (broker unavailable). "
                    "Retry once the broker is reachable."
                ),
            ) from exc
        # Offline (tests/dev): no broker — run the real task inline against fakes.
        task.run(uf)


# ---------------------------------------------------------------------------
# On-read window aggregation (synced / failed)
# ---------------------------------------------------------------------------


def _window_counts(db: Session, started_at: datetime, ended_at: datetime | None) -> tuple[int, int]:
    """Count synced / failed over a run's [started_at, ended_at] window (A4).

    synced = MarRecord rows published in the window (models.py:224).
    failed = RioRecord rows routed dlq/descarte with processed_at in the window
             (models.py:147) PLUS PoisonQuarantine rows in the window (models.py:310).

    When ended_at is None (run still finalizing) the window's upper bound is now().
    This is a deliberate time-window approximation — see the module docstring (A4).
    """
    upper = ended_at or datetime.now(timezone.utc)

    synced = (
        db.scalar(
            select(func.count(MarRecord.id)).where(
                MarRecord.published_at >= started_at,
                MarRecord.published_at <= upper,
            )
        )
        or 0
    )
    failed_rio = (
        db.scalar(
            select(func.count(RioRecord.id)).where(
                RioRecord.routing.in_(("dlq", "descarte")),
                RioRecord.processed_at.isnot(None),
                RioRecord.processed_at >= started_at,
                RioRecord.processed_at <= upper,
            )
        )
        or 0
    )
    failed_poison = (
        db.scalar(
            select(func.count(PoisonQuarantine.id)).where(
                PoisonQuarantine.quarantined_at >= started_at,
                PoisonQuarantine.quarantined_at <= upper,
            )
        )
        or 0
    )
    return synced, failed_rio + failed_poison


def _to_item(db: Session, run: RunHistory) -> RunItem:
    """Build a RunItem, recomputing synced/failed/total on-read for this run."""
    synced, failed = _window_counts(db, run.started_at, run.ended_at)
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

    runs = list(db.scalars(stmt).all())
    if uf:
        runs = [r for r in runs if uf in (r.ufs or [])]

    total = len(runs)
    page = runs[offset : offset + limit]
    items = [_to_item(db, run) for run in page]

    return RunsResponse(items=items, total=total, offset=offset, limit=limit).model_dump()


@router.patch(
    "/api/v1/runs/{run_id}/reprocess",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def reprocess_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Re-dispatch a run's scope (ufs × source × lane), audited (T-17.1-02-04).

    v1 re-runs the SCOPE, not a precise failed-record replay (documented above).
    Dispatch reuses the prod-vs-offline broker fallback so the offline suite runs
    the producers inline against fakes.
    """
    run = db.get(RunHistory, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="RunHistory not found")

    ufs = list(run.ufs or [])

    if run.source == "tripadvisor":
        from brave.tasks.pipeline import sweep_tripadvisor

        for uf in ufs:
            _dispatch(sweep_tripadvisor, uf, task_label="sweep_tripadvisor")
    else:
        from brave.tasks.pipeline import discover_atrativo_task

        # destinos has no producer (Mtur seed retired; destinos come from DB tables) —
        # only the atrativos lane re-dispatches for the default (Places) source.
        for uf in ufs:
            if run.lane in ("atrativos", "both"):
                _dispatch(discover_atrativo_task, uf, task_label="discover_atrativo_task")

    write_audit(
        session=db,
        action="run_reprocessed",
        entity_type="run",
        record_id=run.id,
        before_state={"status": run.status},
        after_state={"reprocessed_ufs": ufs, "source": run.source, "lane": run.lane},
        actor="steward",
    )
    db.commit()

    return {"status": "accepted", "run_id": str(run.id), "ufs": ufs}
