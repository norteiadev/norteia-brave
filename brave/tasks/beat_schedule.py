"""Celery beat schedule definitions using celery-redbeat (D-05).

Phase 1: Basic sweep structure defined (stubs).
Phase 2: Destinos lane — sweep_uf tasks.
Phase 3: Atrativos lane — sweep_atrativos_by_uf fan-out added.

UF_LIST: 27 Brazilian states (2-letter codes).
Each UF gets a RedBeatSchedulerEntry that fires the appropriate sweep task.

IMPORTANT: Only one celery-redbeat beat instance should run at a time.
The schedule is stored in Redis, so multiple beat instances would conflict.

sweep_atrativos_by_uf:
  Fires discover_atrativo_task per UF on a staggered daily schedule (3 AM UTC).
  Offset by 1 hour from sweep_uf (2 AM) to avoid peak DB contention.
  All 27 UF tasks fan out independently; each UF is a separate Celery message.
"""

from datetime import timedelta

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

# Single-queue model: all tasks (beat and .delay) land on the default 'celery' queue.
# Dedicated lane routing (task_routes + worker pools) is deferred until HOL-blocking is observed.

# Define the schedule structure for Phase 1
# Real UF sweep tasks arrive with the Destinos lane in Phase 2
BRAVE_BEAT_SCHEDULE: dict = {}

for _uf in UF_LIST:
    BRAVE_BEAT_SCHEDULE[f"sweep-{_uf.lower()}-daily"] = {
        "task": "brave.sweep_uf",  # Phase 2 task; stub in Phase 1
        "schedule": crontab(hour=2, minute=0),  # 2 AM UTC daily
        "args": (_uf,),
        "kwargs": {},
    }

# ---------------------------------------------------------------------------
# Phase 3: Atrativos discovery sweep — fan-out per UF
# discover_atrativo_task runs per UF at 3 AM UTC daily (staggered from sweep_uf)
# ---------------------------------------------------------------------------

for _uf in UF_LIST:
    BRAVE_BEAT_SCHEDULE[f"sweep-atrativos-{_uf.lower()}-daily"] = {
        "task": "brave.discover_atrativo",
        "schedule": crontab(hour=3, minute=0),  # 3 AM UTC daily — 1h after sweep_uf
        "args": (_uf,),
        "kwargs": {},
    }

# Apply to app config
app.conf.beat_schedule = BRAVE_BEAT_SCHEDULE

# ---------------------------------------------------------------------------
# Keep-alive beat — maintains sliding TTL on active TA sessions (260629-p2v)
# Fires every BRAVE_TA_KEEPALIVE_INTERVAL_SECONDS (default 600s / 10 min).
# TripAdvisorConfig() is safe at import time: pydantic-settings env-only, no DB.
# ---------------------------------------------------------------------------
from brave.config.settings import TripAdvisorConfig as _TripAdvisorConfig  # noqa: E402, PLC0415

_ta_beat_interval = _TripAdvisorConfig().keepalive_interval_seconds
BRAVE_BEAT_SCHEDULE["ta-keepalive"] = {
    "task": "brave.ta_keepalive",
    "schedule": timedelta(seconds=_ta_beat_interval),
}
app.conf.beat_schedule = BRAVE_BEAT_SCHEDULE  # re-apply after adding keepalive entry
