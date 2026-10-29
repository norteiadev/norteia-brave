"""Celery beat schedule definitions using celery-redbeat (D-05).

Phase 1: Basic sweep structure defined (stubs).
Phase 2: Destinos lane — sweep_uf tasks.
Phase 3: Atrativos lane — sweep_atrativos_by_uf fan-out added.
Phase D: source-gated — only the lanes in ``enabled_sources(effective config)`` are
  scheduled. The 'default' (Google Places) lane owns the per-UF discover_atrativo
  entries (the Mtur destino seed is retired; the lane ships dormant by default). The
  ta-keepalive entry is a maintenance entry (lane gated at fire time — TA sweeps are
  start-only). Disabling a lane in ``config_settings``
  drops its entries on the NEXT beat restart (redbeat persists the schedule in Redis
  and only resyncs entry definitions on process start — see CLAUDE.md).

UF_LIST: 27 Brazilian states (2-letter codes).
Each UF gets a RedBeatSchedulerEntry that fires the appropriate sweep task.

IMPORTANT: Only one celery-redbeat beat instance should run at a time.
The schedule is stored in Redis, so multiple beat instances would conflict.

sweep_atrativos_by_uf:
  Fires discover_atrativo_task per UF on a staggered daily schedule (3 AM UTC).
  All 27 UF tasks fan out independently; each UF is a separate Celery message.
"""

import os
import time

import structlog

from brave.tasks.celery_app import app

logger = structlog.get_logger(__name__)

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

      - ``default`` → the Google Places domain's per-UF attraction entries
        (sweep-atrativos-{uf}-daily @ 3 AM UTC).
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

    Reads the ``config_settings`` overlay so a durably-disabled lane is not scheduled
    at beat startup (the correct resync point per the redbeat note above). A DB failure
    is retried 3x with a short backoff (5s connect timeout) — redbeat DELETES the stored
    entries a schedule omits, so one blip at a beat restart used to drop lanes until the
    next restart. Only then (or with no ``BRAVE_DB_URL``) it schedules NO sweep lane
    (maintenance entries only) and logs an error: an env fallback would re-enable a lane
    the operator turned off. Beat import NEVER breaks. A dedicated short-lived engine is
    used and disposed to avoid importing the FastAPI DI layer (``brave.api.deps``) into
    the Celery process.
    """
    from sqlalchemy import create_engine  # noqa: PLC0415
    from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

    from brave.config.runtime import enabled_sources, load_effective_config

    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        logger.error("beat_enabled_sources_unavailable", error="BRAVE_DB_URL not set")
        return []
    attempts = 3
    for attempt in range(attempts):
        try:
            engine = create_engine(db_url, connect_args={"connect_timeout": 5})
            try:
                with sessionmaker(bind=engine)() as session:
                    return enabled_sources(load_effective_config(session))
            finally:
                engine.dispose()
        except Exception as exc:
            if attempt == attempts - 1:
                logger.error("beat_enabled_sources_unavailable", error=str(exc))
                return []
            time.sleep(2**attempt)
    return []


# ---------------------------------------------------------------------------
# Maintenance beat entries (lane-independent)
# ---------------------------------------------------------------------------


def maintenance_beat_entries() -> dict:
    """Beat rows that run regardless of which collection lanes are enabled.

    These are not tied to a source lane, so they are unioned in unconditionally
    (never gated by ``enabled_sources``). Single-queue model: NO options.queue —
    the entry lands on the default 'celery' queue (guarded by
    tests/unit/test_celery_queue_routing.py).

      - ``prune-record-events-daily`` → RecordEvent retention prune @ 4 AM UTC
        (offset from the 2/3 AM sweep entries to avoid DB contention). Deletes
        aged status='ok'/'skip' events; PRESERVES status='fail' (Decisão C).
      - ``collect-description-batches-15min`` → the collect half of the batched
        atrativo-description lane. SUBMIT IS DELIBERATELY NOT SCHEDULED: a description is
        only ever generated by the Painel's "describe" action (brave.describe_uf), never
        on a timer — brave.submit_description_batch stays a task an operator can dispatch
        by hand. Collect is frequent because it is a cheap retrieve() per in-flight batch
        id, and it no-ops with nothing in flight. Collect also carries the
        REAPER, which is why it is NOT gated on the batch flag — turning batch mode off
        must still unstick records. The reaper does NOT free every stale stamp on time
        alone: a stamp carrying a real batch id is kept until retrieve() 404s (freeing a
        live batch would re-bill work already paid for), and while run_real_externals is
        off there is no client to probe with, so those stay stamped. See reap_stale_claims.
      - ``repush-pending-mar-15min`` → re-dispatches the norteia-api push for Mar rows
        with pushed_at NULL (API/broker was down, retries exhausted). No-op while
        externals are off or norteia-api's health ping fails.
      - ``push-pending-media-15min`` → the photo outbox (brave.push_pending_media): sends
        entity_images whose entity is already on norteia-api. No-op offline / API down.
      - ``refresh-places-daily`` → re-fetches Place Details (by cached place_id, never a
        Text Search) for atrativos whose Places data is older than 60 days, capped by
        places_refresh_daily_limit. No-op while externals or Places enrichment are off.
      - ``ta-keepalive`` → the TA session keep-alive. Unconditional here (the task skips
        offline, without a session, or with the 'tripadvisor' lane off — read at fire
        time), so a DB blip at beat start can no longer drop it and toggling the lane
        needs no restart. Omitted when keepalive_interval_seconds <= 0 (a zero timedelta
        fired every beat tick). ``expires``: a ping queued behind long sweeps past its
        interval is dropped instead of bursting with the others when the queue drains.
      - ``ta-backfill-resume-15min`` → brave.backfill_ta_atrativos with no run: restarts
        a backfill our daily cap paused once the UTC day turns (see _backfill_tick);
        a no-op otherwise.
    """
    from datetime import timedelta  # noqa: PLC0415

    from celery.schedules import crontab  # noqa: PLC0415

    from brave.config.settings import TripAdvisorConfig  # noqa: PLC0415

    entries: dict = {}
    keepalive = TripAdvisorConfig().keepalive_interval_seconds
    if keepalive > 0:
        entries["ta-keepalive"] = {
            "task": "brave.ta_keepalive",
            "schedule": timedelta(seconds=keepalive),
            "options": {"expires": keepalive},
        }
    return entries | {
        "ta-backfill-resume-15min": {
            "task": "brave.backfill_ta_atrativos",
            "schedule": crontab(minute="*/15"),
            "args": (),
            "kwargs": {},
            "options": {"expires": 900},
        },
        "prune-record-events-daily": {
            "task": "brave.prune_record_events",
            "schedule": crontab(hour=4, minute=0),  # 4 AM UTC daily
            "args": (),
            "kwargs": {},
        },
        "collect-description-batches-15min": {
            "task": "brave.collect_description_batches",
            "schedule": crontab(minute="*/15"),
            "args": (),
            "kwargs": {},
        },
        "repush-pending-mar-15min": {
            "task": "brave.repush_pending_mar",
            "schedule": crontab(minute="*/15"),
            "args": (),
            "kwargs": {},
        },
        "push-pending-media-15min": {
            "task": "brave.push_pending_media",
            "schedule": crontab(minute="*/15"),
            "args": (),
            "kwargs": {},
        },        "refresh-places-daily": {
            "task": "brave.refresh_places",
            "schedule": crontab(hour=5, minute=0),  # 5 AM UTC, after the 4 AM prune
            "args": (),
            "kwargs": {},
        },
    }


# Build once at import and apply to the Celery app config.
BRAVE_BEAT_SCHEDULE: dict = build_beat_schedule(_enabled_sources_best_effort())
# ta-keepalive is a maintenance entry now: drop the TA domain's copy, whose presence hung
# on the DB read above (and which a zero interval turned into a fire-every-tick loop).
BRAVE_BEAT_SCHEDULE.pop("ta-keepalive", None)
BRAVE_BEAT_SCHEDULE.update(maintenance_beat_entries())
app.conf.beat_schedule = BRAVE_BEAT_SCHEDULE
