"""TripAdvisor bulk sweep auto-resume helper (plan 260628-m1n).

Single idempotent helper called by two independent triggers:
  1. Inject hook (POST /api/v1/tripadvisor/session) — fires after canary passes
  2. Beat task (brave.ta_resume_watch, 60s) — covers worker restarts + API-bypass paths

Race-safety: claim_resume uses a state-check-then-SETNX protocol so exactly one
caller dispatches, even if both triggers fire simultaneously.

Self-heal: if sweep_tripadvisor.delay raises (broker down, serialization error), the
state resets to stopped_needs_bootstrap and the claim key is deleted so the next
trigger can retry. No stuck RESUMING state.

TA_NEEDS_BOOTSTRAP_KEY must stay in sync with:
  - brave/tasks/pipeline.py:_TA_NEEDS_BOOTSTRAP_KEY
  - brave/api/routers/tripadvisor_session.py:_TA_NEEDS_BOOTSTRAP_KEY
(three definitions; all must match "brave:ta:needs_bootstrap")
"""

from __future__ import annotations

from typing import Any

import structlog

from brave.lanes.tripadvisor import sweep_progress
from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY

logger = structlog.get_logger(__name__)

# Mirrors pipeline.py:_TA_NEEDS_BOOTSTRAP_KEY and tripadvisor_session.py:_TA_NEEDS_BOOTSTRAP_KEY.
# All three definitions MUST stay in sync.
TA_NEEDS_BOOTSTRAP_KEY = "brave:ta:needs_bootstrap"


def maybe_resume_bulk_sweep(redis: Any) -> bool:
    """Dispatch a bulk TA sweep resume if conditions are met. Idempotent and race-safe.

    Returns True if a sweep was dispatched, False if preconditions weren't met
    (not paused, no session, or lost the race to another caller).

    Raises on dispatch failure AFTER self-healing state (so caller's try/except
    can log it without the exception being swallowed silently here).

    Preconditions (all must hold):
      1. Sweep is in stopped_needs_bootstrap state
      2. A fresh session (BRAVE_TA_SESSION_KEY) is present in Redis
      3. This caller wins the claim_resume atomic gate (SETNX)
    """
    # 1. Quick pre-check before any writes
    if not sweep_progress.is_paused_needs_bootstrap(redis):
        return False

    # 2. Session must be present (operator has injected a fresh one)
    if not redis.exists(BRAVE_TA_SESSION_KEY):
        return False

    # 3. Atomic gate — exactly one concurrent caller wins
    if not sweep_progress.claim_resume(redis):
        return False  # Lost the race; another caller is dispatching

    # 4. Clear the bootstrap marker (best-effort)
    try:
        redis.delete(TA_NEEDS_BOOTSTRAP_KEY)
    except Exception:
        pass  # Non-fatal; the sweep will still run

    # 5. Fetch stored run params for the resume dispatch
    params = sweep_progress.get_resume_params(redis)

    # 6. Lazy import — avoids circular import at module load time.
    #    brave.tasks.pipeline imports brave.lanes.tripadvisor.*; a top-level import
    #    here would create a cycle. The lazy import resolves to the same task object
    #    that tests can monkeypatch via `brave.tasks.pipeline.sweep_tripadvisor.delay`.
    from brave.tasks.pipeline import sweep_tripadvisor  # noqa: PLC0415

    # 7. Dispatch with self-heal on failure
    try:
        sweep_tripadvisor.delay(
            "BR",
            params["depth"],
            bulk_national=True,
            max_pages=params["max_pages"],
            geo_id=params["geo_id"],
        )
    except Exception:
        # Broker unreachable or task serialization error.
        # Reset to stopped_needs_bootstrap so the next inject hook or 60s beat can retry.
        # Release the claim key so SETNX can be re-acquired by the next caller.
        sweep_progress.stop_needs_bootstrap(redis)
        try:
            redis.delete(sweep_progress._RESUME_CLAIM_KEY)
        except Exception:
            pass
        raise  # Re-raise for observability; callers' own try/except handles logging

    logger.info(
        "ta_bulk_sweep_auto_resumed",
        geo_id=params["geo_id"],
        max_pages=params["max_pages"],
    )
    return True
