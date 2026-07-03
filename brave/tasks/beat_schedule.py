"""Celery beat schedule definitions using celery-redbeat (D-05).

Phase 1: Basic sweep structure defined (stubs).
Phase 2: Destinos lane — sweep_uf tasks.
Phase 3: Atrativos lane — sweep_atrativos_by_uf fan-out added.
Phase D: source-gated — only the lanes in ``enabled_sources(effective config)`` are
  scheduled. The 'default' (Mtur/Discovery) lane owns the per-UF sweep_uf +
  discover_atrativo entries; the 'tripadvisor' lane owns the ta-keepalive entry (its
  only beat task — TA sweeps are start-only). Disabling a lane in ``config_settings``
  drops its entries on the NEXT beat restart (redbeat persists the schedule in Redis
  and only resyncs entry definitions on process start — see CLAUDE.md).

UF_LIST: 27 Brazilian states (2-letter codes).
Each UF gets a RedBeatSchedulerEntry that fires the appropriate sweep task.

IMPORTANT: Only one celery-redbeat beat instance should run at a time.
The schedule is stored in Redis, so multiple beat instances would conflict.

sweep_atrativos_by_uf:
  Fires discover_atrativo_task per UF on a staggered daily schedule (3 AM UTC).
  Offset by 1 hour from sweep_uf (2 AM) to avoid peak DB contention.
  All 27 UF tasks fan out independently; each UF is a separate Celery message.
"""

import os

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
# Beat schedule builder (Phase D: source-gated)
# ---------------------------------------------------------------------------

# Single-queue model: all tasks (beat and .delay) land on the default 'celery' queue.
# Dedicated lane routing (task_routes + worker pools) is deferred until HOL-blocking is
# observed. Beat entries therefore carry NO options.queue (guarded by
# tests/unit/test_celery_queue_routing.py).


def build_beat_schedule(enabled: list[str]) -> dict:
    """Build the beat schedule for exactly the ENABLED collection lanes.

    Registry-driven (Phase G STEP 3): this no longer names a source. It resolves each
    enabled lane to its :class:`~brave.domains.base.SourceDomain` via the registry and
    unions the entries the domain contributes (``beat_entries``). Adding a source's
    schedule is therefore a change in that domain only, never here.

      - ``default`` → the Mtur/Discovery domain's per-UF sweep entries
        (sweep-{uf}-daily @ 2 AM UTC + sweep-atrativos-{uf}-daily @ 3 AM UTC).
      - ``tripadvisor`` → the TA session keep-alive beat (its only scheduled task;
        TA sweeps are dispatched on-demand via /engine/start).

    Pure + import-safe: each domain's ``beat_entries`` reads only env config
    (TripAdvisorConfig is pydantic-settings, no DB). No options.queue on any entry.
    An unknown lane name (not registered) is skipped rather than raising, so a stale
    config overlay can never wedge beat import.
    """
    from brave.domains import get_domain  # noqa: PLC0415

    schedule: dict = {}
    for name in enabled:
        try:
            domain = get_domain(name)
        except KeyError:
            continue
        schedule.update(domain.beat_entries(UF_LIST))
    return schedule


def _enabled_sources_best_effort() -> list[str]:
    """The enabled lanes from the effective config, computed import-safely.

    Reads the ``config_settings`` overlay when a DB is reachable so a durably-disabled
    lane is not scheduled at beat startup (the correct resync point per the redbeat
    note above). Any failure — no ``BRAVE_DB_URL``, DB down, pytest-socket blocked at
    import — falls back to the env-only ``AppConfig()`` (both lanes enabled), so beat
    import NEVER breaks. A dedicated short-lived engine is used and disposed to avoid
    importing the FastAPI DI layer (``brave.api.deps``) into the Celery process.
    """
    from brave.config.runtime import enabled_sources, load_effective_config
    from brave.config.settings import AppConfig

    db_url = os.environ.get("BRAVE_DB_URL")
    if db_url:
        try:
            from sqlalchemy import create_engine  # noqa: PLC0415
            from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

            engine = create_engine(db_url)
            try:
                with sessionmaker(bind=engine)() as session:
                    return enabled_sources(load_effective_config(session))
            finally:
                engine.dispose()
        except Exception:
            pass
    return enabled_sources(AppConfig())


# Build once at import and apply to the Celery app config.
BRAVE_BEAT_SCHEDULE: dict = build_beat_schedule(_enabled_sources_best_effort())
app.conf.beat_schedule = BRAVE_BEAT_SCHEDULE
