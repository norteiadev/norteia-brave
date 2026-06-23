"""Regression guard: the Celery app must register the production pipeline tasks
WITHOUT a manual `--include brave.tasks.pipeline` on the worker command line.

Root cause this guards against (quick task 260623-j78): `autodiscover_tasks`
defaults `related_name="tasks"`, so `autodiscover_tasks(["brave.tasks"])` imported
the non-existent `brave.tasks.tasks` module. The real @shared_task definitions
live in `brave/tasks/pipeline.py`, so the worker booted with an empty [tasks]
list and silently processed nothing — a dispatched `engine_sweep_run` hung in
`running` forever. The fix passes `related_name="pipeline"`.

Offline: imports only, no Redis/Postgres, no RUN_REAL_EXTERNALS.
"""

from __future__ import annotations

import pytest

EXPECTED_TASKS = [
    "brave.engine_sweep_run",
    "brave.sweep_uf",
    "brave.process_nascente",
    "brave.push_mar",
]


@pytest.mark.parametrize("task_name", EXPECTED_TASKS)
def test_pipeline_task_is_registered_via_autodiscover(task_name: str) -> None:
    """Each production task is discoverable from the Celery app's own config.

    Triggering `import_default_modules()` runs the autodiscover machinery exactly
    as a real worker does at boot. If `related_name` is wrong, the import is a
    no-op and the task is absent — failing this test.
    """
    from brave.tasks.celery_app import app

    # Drive the same discovery a worker performs on startup.
    app.loader.import_default_modules()

    assert task_name in app.tasks, (
        f"{task_name!r} not registered — Celery autodiscover did not import "
        "brave.tasks.pipeline. Check autodiscover_tasks(related_name=...)."
    )
