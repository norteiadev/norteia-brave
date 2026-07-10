"""Regression test for the API Celery broker binding (engine/start dispatch).

Bug: `brave/api/main.py` did not bind the Redis-configured Celery app as the
process global-default app, so every `@shared_task` (engine_sweep_run,
discover_atrativo, …) resolved to Celery's stock DEFAULT app — broker amqp://localhost:5672
(RabbitMQ). Under RUN_REAL_EXTERNALS, `engine_sweep_run.delay()` then failed with
"[Errno 61] Connection refused" and engine/start returned 503 "broker
unavailable", even though the configured broker is Redis and reachable.

`set_as_current=True` (Celery default) only sets the THREAD-LOCAL current app —
present on the import thread but absent in FastAPI's sync-handler threadpool
worker, where `current_app` falls back to the unset global default → amqp.
`main.py` must call `app.set_default()` so shared tasks resolve to Redis from any
thread.

This test asserts the global default Celery app's broker is the configured
BRAVE_DB_REDIS_URL after importing the API app — the invariant that, if broken,
silently routes real dispatch to the wrong broker.
"""

from __future__ import annotations

import os


def test_api_import_sets_redis_as_default_celery_app() -> None:
    # Import the API app (runs brave/api/main.py, which must bind the Celery app).
    import brave.api.main  # noqa: F401
    from celery import _state

    from brave.tasks.celery_app import app as configured_app

    expected = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")

    # The GLOBAL default app — what a threadpool worker's shared_task resolves to —
    # must be the Redis-configured app, not Celery's stock amqp default.
    assert _state.default_app is not None, (
        "no global default Celery app set — shared tasks will fall back to the "
        "stock amqp broker in FastAPI's threadpool handlers"
    )
    assert _state.default_app.conf.broker_url == expected
    assert configured_app.conf.broker_url == expected


def test_engine_sweep_run_binds_to_redis_broker() -> None:
    import brave.api.main  # noqa: F401

    from brave.tasks.pipeline import engine_sweep_run

    expected = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
    # The shared task must publish through the Redis broker, not amqp.
    assert engine_sweep_run.app.conf.broker_url == expected
    assert engine_sweep_run.app.conf.broker_url.startswith("redis://")
