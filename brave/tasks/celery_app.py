"""Celery application configuration (D-05).

Single Celery app instance for norteia-brave.
Configured with:
  - Redis broker + result backend (from DBConfig.redis_url)
  - celery-redbeat for Redis-backed beat scheduling (single-source-of-truth)
  - Operational settings for a 24/7 stateful pipeline:
      task_acks_late=True         — ack after completion (not after receipt)
      task_reject_on_worker_lost=True — re-queue on unexpected worker death
      task_time_limit=300         — hard 5-minute limit per task
      worker_prefetch_multiplier=1 — one task at a time per worker (stateful pipeline)

IMPORTANT: Only one celery beat instance should run at a time.
celery-redbeat enforces this via Redis leader election.
"""

import os

from celery import Celery

# ---------------------------------------------------------------------------
# Redis URL (resolved from environment, with fallback for testing)
# ---------------------------------------------------------------------------

REDIS_URL = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------

app = Celery("norteia_brave")

app.conf.update(
    # Broker and result backend
    broker_url=REDIS_URL,
    result_backend=REDIS_URL,
    # celery-redbeat (D-05): single-source-of-truth schedule in Redis
    redbeat_redis_url=REDIS_URL,
    redbeat_key_prefix="brave",
    beat_scheduler="redbeat.RedBeatScheduler",
    # Reliability settings for 24/7 stateful pipeline (see PITFALLS §7)
    task_acks_late=True,                    # Ack after task completes (not on receipt)
    task_reject_on_worker_lost=True,        # Re-queue if worker dies mid-task
    task_time_limit=300,                    # Hard 5-minute wall clock limit
    worker_prefetch_multiplier=1,           # One task at a time (stateful writes)
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Single-queue model: all tasks (beat and .delay) land on 'celery'. No task_routes,
    # no -Q flag on the worker. Dedicated lanes deferred until HOL-blocking is observed.
    task_default_queue="celery",  # Explicit default — matches worker start cmd and beat dispatch
)

# Auto-discover tasks in brave.tasks.pipeline.
# related_name MUST be "pipeline": the @shared_task definitions live in
# brave/tasks/pipeline.py, not the Celery default brave/tasks/tasks.py. Without
# this, autodiscover imports a non-existent module, the worker boots with an
# empty [tasks] list, and every dispatched task (engine_sweep_run/sweep_uf/…)
# silently never runs.
app.autodiscover_tasks(["brave.tasks"], related_name="pipeline")

# ---------------------------------------------------------------------------
# Structlog configuration for Celery workers (Phase ks0)
# ---------------------------------------------------------------------------

from celery.signals import worker_process_init  # noqa: E402


@worker_process_init.connect
def _configure_structlog_on_worker_init(**kwargs):
    """Wire the Redis log buffer into structlog for each Celery worker process.

    Called once per forked worker process (not on the main process). Creates a
    fresh Redis client from BRAVE_DB_REDIS_URL. Falls back to console-only when
    Redis is unavailable or BRAVE_USE_FAKEREDIS is set (offline tests).

    The fall-through to console-only is intentional: a worker that cannot reach
    Redis should still run — structlog events will appear in stdout rather than
    the dashboard log sidebar, but pipeline processing is unaffected.
    """
    import os  # noqa: PLC0415

    from brave.observability.structlog_setup import configure_structlog  # noqa: PLC0415

    if os.environ.get("BRAVE_USE_FAKEREDIS"):
        configure_structlog(redis=None)
        return
    try:
        from redis import Redis as _Redis  # noqa: PLC0415

        r = _Redis.from_url(REDIS_URL, socket_connect_timeout=2)
        r.ping()
        configure_structlog(redis=r)
    except Exception:
        configure_structlog(redis=None)
