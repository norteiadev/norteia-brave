"""Phase D: beat schedule is gated by enabled_sources.

Pure, fully-offline unit tests over ``build_beat_schedule(enabled)`` — no Redis, no
Postgres, no broker. The 'default' (Google Places) lane owns the per-UF
discover_atrativo entries; the 'tripadvisor' lane owns the ta-keepalive beat.
Disabling a lane drops its entries.

Also re-asserts the single-queue invariant (no options.queue) on the gated schedule,
complementing tests/unit/test_celery_queue_routing.py.
"""

from __future__ import annotations

from brave.tasks.beat_schedule import UF_LIST, build_beat_schedule


def _sweep_keys(sched: dict) -> list[str]:
    return [k for k in sched if k.startswith("sweep-")]


def test_default_only_has_uf_sweeps_no_keepalive():
    sched = build_beat_schedule(["default"])
    # 1 entry per UF (discover_atrativo only — the Mtur sweep_uf seed is retired).
    assert len(_sweep_keys(sched)) == len(UF_LIST)
    assert f"sweep-{UF_LIST[0].lower()}-daily" not in sched  # no destino sweep anymore
    assert f"sweep-atrativos-{UF_LIST[0].lower()}-daily" in sched
    assert "ta-keepalive" not in sched  # tripadvisor lane disabled


def test_tripadvisor_only_has_keepalive_no_sweeps():
    sched = build_beat_schedule(["tripadvisor"])
    assert "ta-keepalive" in sched
    assert _sweep_keys(sched) == []  # default lane disabled → no UF sweeps


def test_both_enabled_has_everything():
    sched = build_beat_schedule(["default", "tripadvisor"])
    assert "ta-keepalive" in sched
    assert len(_sweep_keys(sched)) == len(UF_LIST)


def test_no_lane_enabled_is_empty():
    assert build_beat_schedule([]) == {}


def test_gated_schedule_pins_no_nondefault_queue():
    sched = build_beat_schedule(["default", "tripadvisor"])
    for name, entry in sched.items():
        queue = entry.get("options", {}).get("queue")
        assert queue in (None, "celery"), f"{name!r} pins queue={queue!r}"


def test_module_schedule_is_built_from_enabled_sources():
    # The module-level schedule is build_beat_schedule(enabled lanes) + the maintenance
    # entries; with no readable overlay it is maintenance-only, never empty. This guards
    # the wiring, not a hardcoded count (WR-04 / test_workers_endpoints reads it dynamically).
    from brave.tasks.beat_schedule import BRAVE_BEAT_SCHEDULE

    assert isinstance(BRAVE_BEAT_SCHEDULE, dict)
    assert "prune-record-events-daily" in BRAVE_BEAT_SCHEDULE


def test_unreadable_config_store_schedules_no_lane(monkeypatch):
    """DB unreachable at beat import → no sweep lane (fail-closed), import still succeeds."""
    from brave.tasks.beat_schedule import _enabled_sources_best_effort

    monkeypatch.setenv("BRAVE_DB_URL", "postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    assert _enabled_sources_best_effort() == []
