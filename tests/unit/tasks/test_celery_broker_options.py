"""Broker/beat invariants for the single-queue model (F57, F68, F91) + the keep-alive's
fire-time lane gate. No broker, no DB."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import fakeredis

from brave.tasks import pipeline
from brave.tasks.celery_app import app


def _pipeline_tasks():
    return [t for name, t in app.tasks.items() if name.startswith("brave.")]


def test_visibility_timeout_exceeds_the_longest_task():
    """F91: with acks_late the Redis transport re-delivers anything unacked for
    visibility_timeout (default 3600s = the sweeps' time_limit) → duplicate sweeps."""
    longest = max(t.time_limit or app.conf.task_time_limit for t in _pipeline_tasks())
    assert app.conf.broker_transport_options["visibility_timeout"] > longest


def test_long_producers_yield_to_the_keepalive():
    """F57: one queue, but Redis pops priority 0 before 6 — a freed slot takes a queued
    ta_keepalive before the next queued UF sweep."""
    keepalive = pipeline.ta_keepalive.priority or 0
    for task in (
        pipeline.sweep_tripadvisor,
        pipeline.describe_uf,
        pipeline.discover_atrativo_task,
        pipeline.backfill_ta_atrativos,
    ):
        assert (task.priority or 0) > keepalive, task.name


def test_keepalive_is_an_unconditional_maintenance_entry_that_expires():
    """F68: no longer dropped by a DB blip at beat start; a late ping expires instead of
    bursting with the others when the queue drains."""
    from brave.tasks.beat_schedule import maintenance_beat_entries

    entry = maintenance_beat_entries()["ta-keepalive"]
    assert entry["task"] == "brave.ta_keepalive"
    assert entry["schedule"] == timedelta(seconds=600)
    assert entry["options"] == {"expires": 600}
    assert "queue" not in entry["options"]  # single-queue model


def test_zero_keepalive_interval_omits_the_entry(monkeypatch):
    """F68: a zero timedelta made the beat fire the ping on every tick."""
    from brave.tasks.beat_schedule import maintenance_beat_entries

    monkeypatch.setenv("BRAVE_TA_KEEPALIVE_INTERVAL_SECONDS", "0")
    assert "ta-keepalive" not in maintenance_beat_entries()


def test_backfill_resume_tick_is_scheduled():
    from brave.tasks.beat_schedule import BRAVE_BEAT_SCHEDULE

    entry = BRAVE_BEAT_SCHEDULE["ta-backfill-resume-15min"]
    assert entry["task"] == "brave.backfill_ta_atrativos" and entry["kwargs"] == {}


def test_beat_retries_the_db_before_dropping_lanes(monkeypatch):
    """F68: a blip at beat start used to schedule maintenance only — and redbeat deletes
    the entries a schedule omits."""
    from brave.tasks import beat_schedule

    attempts: list[int] = []

    def _flaky(url, **kw):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("pg down")
        return MagicMock()

    monkeypatch.setenv("BRAVE_DB_URL", "postgresql+psycopg://x/y")
    monkeypatch.setattr("sqlalchemy.create_engine", _flaky)
    monkeypatch.setattr("sqlalchemy.orm.sessionmaker", lambda bind: lambda: MagicMock())
    monkeypatch.setattr(
        "brave.config.runtime.load_effective_config", lambda session: MagicMock()
    )
    monkeypatch.setattr("brave.config.runtime.enabled_sources", lambda cfg: ["tripadvisor"])
    monkeypatch.setattr(beat_schedule.time, "sleep", lambda s: None)

    assert beat_schedule._enabled_sources_best_effort() == ["tripadvisor"]
    assert len(attempts) == 3


def test_keepalive_skips_when_the_lane_is_off(monkeypatch):
    """The entry is unconditional now, so the lane gate runs at fire time."""
    from brave.config.settings import AppConfig
    from brave.domains.tripadvisor.client import BRAVE_TA_SESSION_KEY

    fake = fakeredis.FakeRedis()
    fake.setex(BRAVE_TA_SESSION_KEY, 900, "{}")
    monkeypatch.setattr("redis.from_url", lambda *a, **k: fake)
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")
    monkeypatch.setattr(pipeline, "_get_session", lambda: (MagicMock(), None))
    monkeypatch.setattr(
        pipeline, "_load_config",
        lambda s: AppConfig(sources={"default": False, "tripadvisor": False}),
    )

    def _no_client(**kw):
        raise AssertionError("lane off: no ping")

    monkeypatch.setattr("brave.domains.tripadvisor.client.TripAdvisorClient", _no_client)

    pipeline.ta_keepalive.run()
