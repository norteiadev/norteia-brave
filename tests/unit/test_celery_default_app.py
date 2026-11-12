"""Importing the task module is enough to publish through the configured broker.

Bug: ``python -m brave.cli editorial-load ES`` inside the worker container failed with kombu
"Connection refused": the CLI imports ``brave.tasks.pipeline`` (the ``@shared_task``s) but
never ``brave.tasks.celery_app``, so ``.delay`` resolved to Celery's stock default app
(amqp://localhost) instead of the Redis one. Only ``brave.api.main`` had bound the configured
app (``set_default``). The binding now lives in ``celery_app`` and ``pipeline`` imports it,
so EVERY caller of a task (CLI, scripts, API, beat, worker) gets the Redis app.

Runs in a fresh interpreter: this suite has long imported ``celery_app``, which would hide it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SENTINEL = "redis://sentinel-broker.invalid:6379/3"

PROBE = """
import threading
from brave.tasks.pipeline import editorial_crawl_uf  # nothing else: this is the CLI's import

seen = {}
t = threading.Thread(target=lambda: seen.update(broker=editorial_crawl_uf.app.conf.broker_url))
t.start()
t.join()
print(editorial_crawl_uf.app.conf.broker_url, seen["broker"])
"""


def test_importing_the_tasks_binds_the_redis_broker_in_every_thread() -> None:
    env = {
        "PATH": "",
        "BRAVE_DB_REDIS_URL": SENTINEL,
        "BRAVE_USE_FAKEREDIS": "1",
    }
    out = subprocess.run(  # noqa: S603 — our own interpreter and a constant probe
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.split() == [SENTINEL, SENTINEL]
