"""Regression guard: beat-scheduled tasks must not pin a non-default queue.

Root cause this guards against (quick task 260630-mb4): BRAVE_BEAT_SCHEDULE had
`options={"queue": "brave.sweep"}` on all 3 entry types (sweep-{uf}-daily x27,
sweep-atrativos-{uf}-daily x27, ta-keepalive). No running worker consumed
`brave.sweep` — every beat-dispatched task was silently dropped. Confirmed root
cause of SPIKE-1 "ta-keepalive never fired". The fix: remove options.queue from
every entry (so tasks land on the default 'celery' queue) and set
task_default_queue="celery" explicitly in celery_app.py.

Offline: pure import + attribute checks. No Redis, no Postgres, no externals.
"""

from __future__ import annotations


def test_no_beat_entry_pins_unconsumed_queue() -> None:
    """Assert that no BRAVE_BEAT_SCHEDULE entry pins a non-default queue.

    Passes when options.queue is either absent (None) or explicitly "celery".
    Fails if any entry introduces a non-default queue (e.g. "brave.sweep"),
    which would cause beat tasks to be silently dropped by the default worker.
    """
    from brave.tasks.beat_schedule import BRAVE_BEAT_SCHEDULE

    for name, entry in BRAVE_BEAT_SCHEDULE.items():
        queue = entry.get("options", {}).get("queue")
        if queue is not None:
            assert queue == "celery", (
                f"Beat entry {name!r} pins queue={queue!r} — "
                "use the default 'celery' queue or omit options.queue"
            )


def test_celery_app_default_queue_is_celery() -> None:
    """Assert that task_default_queue is explicitly set to 'celery' in celery_app.

    Guards against removing or renaming the explicit pin added in 260630-mb4.
    """
    from brave.tasks.celery_app import app

    assert app.conf.task_default_queue == "celery", (
        "task_default_queue must be explicitly 'celery' — "
        "see celery_app.py comment for rationale"
    )
