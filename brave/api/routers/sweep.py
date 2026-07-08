"""On-demand ops-trigger endpoint (ORCH-03, D-05, security T-05-07/T-05-09).

POST /api/v1/sweep  — kick a UF sweep for destinos and/or atrativos without
                      waiting for the 2/3 AM beat.

This is the OPTIONAL D-05 nice-to-have surface (the `brave.cli sweep` subcommand
is the required one). It MUST be Bearer-guarded so an unauthenticated caller
cannot fan out expensive LLM/Places sweeps (T-05-07). It only kicks the existing
producer/chain tasks — sweep_uf (producer-only) and discover_atrativo_task (which
auto-chains and STOPS at the WhatsApp gate). It never auto-validates, never
bypasses the reliability gate, and never reaches the WhatsApp send path (T-05-09, D-02/D-07).

Dispatch uses the prod-vs-offline fallback variant (mirrors atrativos_gate.py:376-396):
swallow the broker error ONLY when run_real_externals is False (offline tests/dev,
where the task is exercised inline); surface a 503 in a real environment so a
broker-down fan-out is not silently lost.
"""

from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from brave.api.deps import require_steward_or_bearer

logger = structlog.get_logger(__name__)

router = APIRouter()


def _dispatch(task, uf: str, *, task_label: str) -> None:
    """Dispatch a producer task with the prod-vs-offline fallback (atrativos_gate.py:376-396).

    Swallow the broker error only when run_real_externals is False (offline:
    the task is exercised inline / by tests). In a real environment a dispatch
    failure surfaces as a 503 rather than silently dropping the fan-out.
    """
    try:
        task.delay(uf)
    except Exception as exc:
        from brave.config.settings import AppConfig

        if AppConfig().run_real_externals:
            logger.error("sweep_dispatch_failed", task=task_label, uf=uf, error=str(exc))
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Sweep dispatch failed for {task_label} (broker unavailable). "
                    "Retry once the broker is reachable."
                ),
            ) from exc
        # Offline (tests/dev): no broker is expected — run the real task inline so
        # the sweep still executes against fakes (run_real_externals=False).
        task.run(uf)


@router.post(
    "/api/v1/sweep",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def trigger_sweep(
    uf: str = Query(..., description="Two-letter Brazilian state code (e.g. BA)"),
    lane: Literal["destinos", "atrativos", "both"] = Query("both"),
) -> dict:
    """Kick an on-demand UF sweep (ORCH-03, D-05).

    Bearer-guarded (require_steward_or_bearer) so an unauthenticated caller cannot
    fan out expensive LLM/Places sweeps (T-05-07). Returns 202 Accepted.

    - lane=destinos  → sweep_uf (producer-only; records land via reliability scoring / DLQ).
    - lane=atrativos → discover_atrativo_task (auto-chains, STOPS at the gate).
    - lane=both      → both (default).

    Never dispatches outreach / reaches the WhatsApp send path (T-05-09, D-02/D-07).
    """
    uf = uf.upper()

    if lane in ("destinos", "both"):
        from brave.tasks.pipeline import sweep_uf

        _dispatch(sweep_uf, uf, task_label="sweep_uf")

    if lane in ("atrativos", "both"):
        from brave.tasks.pipeline import discover_atrativo_task

        _dispatch(discover_atrativo_task, uf, task_label="discover_atrativo_task")

    return {"status": "accepted", "uf": uf, "lane": lane}
