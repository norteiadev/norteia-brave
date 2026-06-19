"""Brave collection engine — a Redis-backed start/stop control for the sweep.

The platform runs 24/7 but the *collection engine* is idle by default: no
destinos/atrativos sweep fans out until an operator starts it from the dashboard.
Stopping is graceful — the orchestrator finishes dispatching the current UF and
then drains: already-enqueued UF tasks complete on the workers, no new UFs are
fanned out, and the engine returns to `idle`.

State lives in Redis (shared between the FastAPI control endpoints and the Celery
orchestrator task):

  brave:engine:state      idle | running | stopping
  brave:engine:current_uf the UF currently being fanned out (for visual feedback)
  brave:engine:ufs_done   how many UFs the current run has dispatched
  brave:engine:ufs_total  how many UFs the current run will dispatch

This module is pure state — it performs no dispatch. The orchestrator task
(brave.tasks.pipeline.engine_sweep_run) reads `state` between UFs and breaks the
loop when it is no longer `running`, which is what makes Stop graceful.
"""

from __future__ import annotations

from typing import Any

IDLE = "idle"
RUNNING = "running"
STOPPING = "stopping"
_VALID = {IDLE, RUNNING, STOPPING}

_STATE_KEY = "brave:engine:state"
_CURRENT_UF_KEY = "brave:engine:current_uf"
_UFS_DONE_KEY = "brave:engine:ufs_done"
_UFS_TOTAL_KEY = "brave:engine:ufs_total"


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def get_state(redis: Any) -> str:
    """Current engine state. Absent key → idle (idle by default in production)."""
    raw = _decode(redis.get(_STATE_KEY))
    return raw if raw in _VALID else IDLE


def is_running(redis: Any) -> bool:
    return get_state(redis) == RUNNING


def start_run(redis: Any, ufs_total: int) -> bool:
    """Mark the engine running for a fresh run.

    Returns False (no-op) if a run is already active — Start is idempotent and
    never stacks two orchestrators. Resets the progress counters.
    """
    if get_state(redis) in (RUNNING, STOPPING):
        return False
    redis.set(_STATE_KEY, RUNNING)
    redis.set(_UFS_TOTAL_KEY, int(ufs_total))
    redis.set(_UFS_DONE_KEY, 0)
    redis.delete(_CURRENT_UF_KEY)
    return True


def request_stop(redis: Any) -> bool:
    """Ask a running engine to stop after the current UF. Returns False if idle."""
    if get_state(redis) != RUNNING:
        return False
    redis.set(_STATE_KEY, STOPPING)
    return True


def mark_idle(redis: Any) -> None:
    """Orchestrator calls this when the loop exits (completed or drained)."""
    redis.set(_STATE_KEY, IDLE)
    redis.delete(_CURRENT_UF_KEY)


def mark_uf_dispatched(redis: Any, uf: str) -> None:
    """Record that one UF was fanned out (for the progress feedback)."""
    redis.set(_CURRENT_UF_KEY, uf)
    redis.incr(_UFS_DONE_KEY)


def get_status(redis: Any) -> dict[str, Any]:
    """Engine status snapshot for the dashboard."""
    return {
        "state": get_state(redis),
        "current_uf": _decode(redis.get(_CURRENT_UF_KEY)) or None,
        "ufs_done": int(_decode(redis.get(_UFS_DONE_KEY)) or 0),
        "ufs_total": int(_decode(redis.get(_UFS_TOTAL_KEY)) or 0),
    }
