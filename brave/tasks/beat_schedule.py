"""Celery beat schedule definitions using celery-redbeat (D-05).

In Phase 1, the sweep_uf structure is defined but tasks are stubs.
Phase 2 (Destinos lane) will fill in the actual UF sweep producers.

UF_LIST: 27 Brazilian states (2-letter codes).
Each UF gets a RedBeatSchedulerEntry that fires a sweep_uf task.

IMPORTANT: Only one celery-redbeat beat instance should run at a time.
The schedule is stored in Redis, so multiple beat instances would conflict.
"""

from celery.schedules import crontab

from brave.tasks.celery_app import app

# ---------------------------------------------------------------------------
# Brazilian states (D-05 fan-out by UF)
# ---------------------------------------------------------------------------

UF_LIST = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO",
    "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
    "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
]

# ---------------------------------------------------------------------------
# Beat schedule entries
# Phase 1: Stub — actual sweep tasks defined in Phase 2
# ---------------------------------------------------------------------------

# Define the schedule structure for Phase 1
# Real UF sweep tasks arrive with the Destinos lane in Phase 2
BRAVE_BEAT_SCHEDULE: dict = {}

for _uf in UF_LIST:
    BRAVE_BEAT_SCHEDULE[f"sweep-{_uf.lower()}-daily"] = {
        "task": "brave.sweep_uf",  # Phase 2 task; stub in Phase 1
        "schedule": crontab(hour=2, minute=0),  # 2 AM UTC daily
        "args": (_uf,),
        "kwargs": {},
        "options": {"queue": "brave.sweep"},
    }

# Apply to app config
app.conf.beat_schedule = BRAVE_BEAT_SCHEDULE
