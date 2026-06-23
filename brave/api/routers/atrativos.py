"""Atrativos operator API — TripAdvisor mar-ready promote-override endpoints (plan 11-03, TA-05/TA-06).

Endpoints:
  GET  /api/v1/atrativos/mar-ready          — list TripAdvisor attractions ready for steward override
  PATCH /api/v1/atrativos/{rio_id}/promote  — steward promote-override single record
  POST  /api/v1/atrativos/promote-batch     — batch promote-override for a UF

Security (T-11-03-01, T-11-03-02):
  - GET /mar-ready: require_bearer (read-only)
  - PATCH + POST: require_steward_or_bearer (mutation)
  - promote_override raises PromoteNotAllowed → 409 for non-mar_ready records;
    this guard is evaluated BEFORE any DB mutation, regardless of caller identity.

Mirrors brave/api/routers/dlq.py exactly for the promote-single and promote-batch
endpoints (same broker-down 503 contract, same audit pattern, same 404/202 shape).
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, require_bearer, require_steward_or_bearer
from brave.core.models import RioRecord
from brave.core.promote.service import PromoteNotAllowed, promote_override
from brave.observability.audit import write_audit

router = APIRouter()
logger = structlog.get_logger(__name__)


def push_attraction_task_delay(rio_id: str) -> None:
    """Dispatch push_attraction_task.delay. Isolated function for easy test patching."""
    from brave.tasks.pipeline import push_attraction_task  # lazy — no Celery import at startup

    push_attraction_task.delay(rio_id)


@router.get("/api/v1/atrativos/mar-ready", dependencies=[Depends(require_bearer)])
def list_mar_ready(
    uf: str | None = Query(None, description="Optional two-letter UF filter"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List TripAdvisor attractions with mar_ready=True and routing='dlq'.

    These are the records eligible for steward promote-override. Filtered to
    TripAdvisor attractions only (canonical_key starts with 'tripadvisor:').

    Optional filter: uf (two-letter UF code).
    Default limit: 100.
    """
    query = (
        select(RioRecord)
        .where(
            RioRecord.mar_ready == True,  # noqa: E712 — SQLAlchemy filter
            RioRecord.routing == "dlq",
            RioRecord.canonical_key.like("tripadvisor:%"),
        )
    )
    if uf:
        query = query.where(RioRecord.uf == uf)
    query = query.limit(limit)

    rows = list(db.scalars(query).all())
    return [
        {
            "id": str(r.id),
            "canonical_key": r.canonical_key,
            "uf": r.uf,
            "score": float(r.score) if r.score is not None else None,
            "entity_type": r.entity_type,
            "mar_ready": r.mar_ready,
        }
        for r in rows
    ]


@router.patch(
    "/api/v1/atrativos/{rio_id}/promote",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def promote_atrativo(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward promote-override for a single TripAdvisor attraction (TA-05).

    Bypasses the ≥85 score gate for records that are mar_ready=True (set by
    route_by_score for TA attractions with sufficient atualidade + corroboracao).

    Steps:
    1. Load RioRecord; 404 if missing.
    2. Call promote_override → PromoteNotAllowed (409) if not mar_ready.
    3. Dispatch push_attraction_task (broker-down → 503 if run_real_externals).
    4. Write audit row with action='atrativo_promoted_override', actor='steward'.

    Returns 202 with {status, rio_id, routing}.
    """
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "score": float(rio.score or 0), "mar_ready": rio.mar_ready}

    try:
        promote_override(db, rio, reason="steward_override_review_validated")
    except PromoteNotAllowed as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Record is not mar_ready — promote-override not allowed: {exc}",
        ) from exc

    db.refresh(rio)

    try:
        push_attraction_task_delay(str(rio_id))
    except Exception as exc:
        # Broker-down must not silently leave a Mar record unpublished.
        # Under run_real_externals, surface it (log + 503) so the whole
        # promote rolls back — the steward retries once the broker is reachable.
        # Offline (tests/dev), no broker is expected; promote_override already
        # committed the Mar record, so the missing push is an expected no-op.
        from brave.config.settings import AppConfig

        if AppConfig().run_real_externals:
            logger.error(
                "atrativo_promote_dispatch_failed",
                rio_id=str(rio_id),
                error=str(exc),
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Promote not committed — Mar push dispatch failed "
                    "(broker unavailable). Retry once the broker is reachable."
                ),
            ) from exc

    write_audit(
        session=db,
        action="atrativo_promoted_override",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"routing": rio.routing, "score": float(rio.score or 0)},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id), "routing": rio.routing}


@router.post(
    "/api/v1/atrativos/promote-batch",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def promote_batch(
    uf: str = Query(..., description="Two-letter UF code (e.g. 'BA')"),
    source: str = Query("tripadvisor"),
    limit: int = Query(100, ge=1, le=1000, description="Max records per batch (T-11-03-04)"),
    db: Session = Depends(get_db),
) -> dict:
    """Batch promote-override all mar_ready attractions for a UF (TA-06).

    Applies the same promote-override logic to every mar_ready=True + routing='dlq'
    TripAdvisor attraction in the requested UF, up to limit.

    Security (T-11-03-04): uf is required (no wildcard). limit is capped at 1000.
    Writes individual audit rows per record.

    Returns 202 with {status, uf, promoted}.
    """
    rows = list(
        db.scalars(
            select(RioRecord)
            .where(
                RioRecord.mar_ready == True,  # noqa: E712
                RioRecord.routing == "dlq",
                RioRecord.canonical_key.like("tripadvisor:%"),
                RioRecord.uf == uf,
            )
            .limit(limit)
        ).all()
    )

    promoted = 0
    for rio in rows:
        before_state = {"routing": rio.routing, "score": float(rio.score or 0)}

        try:
            promote_override(db, rio, reason="steward_override_review_validated")
        except PromoteNotAllowed:
            # Should not happen (we filtered by mar_ready=True), but guard defensively.
            continue

        db.refresh(rio)

        try:
            push_attraction_task_delay(str(rio.id))
        except Exception as exc:
            from brave.config.settings import AppConfig

            if AppConfig().run_real_externals:
                logger.error(
                    "atrativo_promote_dispatch_failed",
                    rio_id=str(rio.id),
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Batch promote not committed — Mar push dispatch "
                        "failed (broker unavailable). Retry once the broker "
                        "is reachable."
                    ),
                ) from exc

        write_audit(
            session=db,
            action="atrativo_promoted_override",
            entity_type=rio.entity_type,
            record_id=rio.id,
            before_state=before_state,
            after_state={"routing": rio.routing, "score": float(rio.score or 0)},
            actor="steward",
        )
        promoted += 1

    return {"status": "accepted", "uf": uf, "promoted": promoted}
