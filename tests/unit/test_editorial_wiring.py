"""Editorial lane wiring that needs no DB: settings, runtime overlay keys, task registration,
beat entries and the CLI's dispatch."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from brave.config.runtime import CONFIG_KEYS
from brave.config.settings import AppConfig

TASKS = [
    "brave.editorial_crawl_uf",
    "brave.editorial_submit_batches",
    "brave.editorial_collect_batches",
    "brave.editorial_resolve",
    "brave.push_editorial_destinations",
]


def test_settings_defaults_keep_the_lane_off_and_pin_the_model() -> None:
    cfg = AppConfig()
    assert cfg.editorial_enabled is False and cfg.editorial_push_enabled is False
    assert (
        cfg.editorial_model_slug == cfg.atrativo_voice_model_slug
    )  # the Sonnet the copywriter uses
    assert cfg.editorial_batch_size > 0 and cfg.editorial_est_usd_per_post > 0


@pytest.mark.parametrize("key", ["editorial_enabled", "editorial_push_enabled"])
def test_both_switches_can_be_toggled_at_runtime_from_the_config_overlay(key) -> None:
    entry = CONFIG_KEYS[key]
    assert entry.kind == "bool" and entry.read(AppConfig()) is False


@pytest.mark.parametrize("name", TASKS)
def test_task_is_registered_via_autodiscover(name) -> None:
    from brave.tasks.celery_app import app

    app.loader.import_default_modules()
    assert name in app.tasks


def test_beat_runs_collect_and_the_destination_outbox_every_15_minutes_on_the_default_queue() -> (
    None
):
    from brave.tasks.beat_schedule import maintenance_beat_entries

    entries = maintenance_beat_entries()
    by_task = {e["task"]: e for e in entries.values()}
    for task in ("brave.editorial_collect_batches", "brave.push_editorial_destinations"):
        e = by_task[task]
        assert "*/15" in repr(e["schedule"]) and "queue" not in e.get("options", {})
    # crawl and submit are operator-dispatched (paid / slow): never on a timer
    assert (
        "brave.editorial_crawl_uf" not in by_task
        and "brave.editorial_submit_batches" not in by_task
    )


def _cli(monkeypatch, *argv):
    from brave import cli
    from brave.tasks import pipeline

    crawl, submit = MagicMock(), MagicMock()
    monkeypatch.setattr(pipeline.editorial_crawl_uf, "delay", crawl)
    monkeypatch.setattr(pipeline.editorial_submit_batches, "delay", submit)
    _cli.submit = submit
    monkeypatch.setattr(sys, "argv", ["brave", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return crawl, exc.value.code


def test_editorial_load_one_uf_dispatches_crawl_then_submit(monkeypatch) -> None:
    crawl, code = _cli(monkeypatch, "editorial-load", "es")
    assert code == 0
    crawl.assert_called_once_with("ES", submit=True, rest=[])


def test_editorial_load_all_chains_the_27_ufs_one_after_another(monkeypatch) -> None:
    crawl, code = _cli(monkeypatch, "editorial-load", "--all")
    assert code == 0 and crawl.call_count == 1  # ONE crawl at a time: the site is slow
    (first,), kwargs = crawl.call_args
    assert len([first, *kwargs["rest"]]) == 27 and kwargs["submit"] is True
    assert len(set([first, *kwargs["rest"]])) == 27


def test_editorial_load_crawl_only_skips_the_paid_submit(monkeypatch) -> None:
    crawl, _ = _cli(monkeypatch, "editorial-load", "ES", "--crawl-only")
    crawl.assert_called_once_with("ES", submit=False, rest=[])


def test_editorial_load_rejects_a_bad_uf_and_missing_target(monkeypatch) -> None:
    crawl, code = _cli(monkeypatch, "editorial-load", "XX")
    assert code == 1 and crawl.call_count == 0
    crawl, code = _cli(monkeypatch, "editorial-load")
    assert code == 1 and crawl.call_count == 0


def test_editorial_load_submit_only_pays_for_what_is_pending_without_crawling(monkeypatch) -> None:
    """Passo 7: crawl-only → the operator shows the estimate → submit-only."""
    crawl, code = _cli(monkeypatch, "editorial-load", "es", "--submit-only")
    assert code == 0 and crawl.call_count == 0
    _cli.submit.assert_called_once_with("ES")


def test_editorial_load_submit_only_for_every_uf_is_one_submit(monkeypatch) -> None:
    crawl, code = _cli(monkeypatch, "editorial-load", "--all", "--submit-only")
    assert code == 0 and crawl.call_count == 0
    _cli.submit.assert_called_once_with(None)


def test_crawl_only_and_submit_only_are_exclusive(monkeypatch) -> None:
    crawl, code = _cli(monkeypatch, "editorial-load", "ES", "--crawl-only", "--submit-only")
    assert code == 1 and crawl.call_count == 0 and _cli.submit.call_count == 0
