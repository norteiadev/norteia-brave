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
    "brave.editorial_validate",
    "brave.push_editorial_destinations",
]


def test_settings_defaults_keep_the_lane_off_and_pin_the_model() -> None:
    cfg = AppConfig()
    assert cfg.editorial_enabled is False and cfg.editorial_push_enabled is False
    assert (
        cfg.editorial_model_slug == cfg.atrativo_voice_model_slug
    )  # the Sonnet the copywriter uses
    assert cfg.editorial_batch_size > 0 and cfg.editorial_est_usd_per_post > 0


def test_the_web_validation_is_off_by_default_on_gemini_flash_with_a_priced_model() -> None:
    from brave.clients.llm import gemini_is_priced

    cfg = AppConfig()
    assert cfg.editorial_validate_enabled is False
    assert cfg.editorial_validate_model_slug == "gemini-2.5-flash"
    assert gemini_is_priced(cfg.editorial_validate_model_slug)  # the cost guard can price it
    assert cfg.editorial_validate_batch_size > 0
    assert cfg.editorial_validate_max_attempts == 3  # then validation_failed: never re-paid
    # per entity: one Parallel search ($0.001) + ~5k tokens in / 0.5k out (~$0.003), rounded up
    assert cfg.editorial_validate_search == "parallel"
    assert 0.004 <= cfg.editorial_validate_est_usd_per_entity <= 0.02


@pytest.mark.parametrize(
    "key", ["editorial_enabled", "editorial_push_enabled", "editorial_validate_enabled"]
)
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
        and "brave.editorial_validate" not in by_task  # paid: chained after the resolve
    )


def _cli(monkeypatch, *argv, enabled=True):
    from brave import cli
    from brave.tasks import pipeline

    monkeypatch.setattr(cli, "_editorial_enabled", lambda: enabled)
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


def test_submit_only_with_several_ufs_submits_each_of_them(monkeypatch) -> None:
    crawl, code = _cli(monkeypatch, "editorial-load", "es", "mg", "--submit-only")
    assert code == 0 and crawl.call_count == 0
    assert [c.args for c in _cli.submit.call_args_list] == [("ES",), ("MG",)]


@pytest.mark.parametrize("flags", [(), ("--crawl-only",), ("--submit-only",)])
def test_load_and_submit_refuse_while_the_lane_is_off(monkeypatch, capsys, flags) -> None:
    """A dispatch the task would silently skip: say so and exit non-zero instead."""
    crawl, code = _cli(monkeypatch, "editorial-load", "ES", *flags, enabled=False)
    assert code != 0 and crawl.call_count == 0 and _cli.submit.call_count == 0
    assert "editorial_enabled is off" in capsys.readouterr().out


def test_an_unreadable_config_does_not_block_the_dispatch(monkeypatch, capsys) -> None:
    crawl, code = _cli(monkeypatch, "editorial-load", "ES", enabled=None)
    assert code == 0 and crawl.call_count == 1
    assert "could not read editorial_enabled" in capsys.readouterr().out


def _validate_cli(monkeypatch, *argv, enabled=True):
    from brave import cli
    from brave.tasks import pipeline

    monkeypatch.setattr(cli, "_editorial_validate_enabled", lambda: enabled)
    resolve = MagicMock()
    monkeypatch.setattr(pipeline.editorial_resolve, "delay", resolve)
    monkeypatch.setattr(sys, "argv", ["brave", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return resolve, exc.value.code


def test_editorial_validate_re_resolves_each_uf_which_hands_the_pending_facts_to_the_check(
    monkeypatch,
) -> None:
    resolve, code = _validate_cli(monkeypatch, "editorial-validate", "es", "mg")
    assert code == 0
    assert [c.args for c in resolve.call_args_list] == [("ES",), ("MG",)]


def test_editorial_validate_is_refused_while_the_paid_check_is_off(monkeypatch, capsys) -> None:
    resolve, code = _validate_cli(monkeypatch, "editorial-validate", "ES", enabled=False)
    assert code == 3 and resolve.call_count == 0
    out = capsys.readouterr().out
    assert "editorial_validate_enabled" in out and "editorial-status" in out  # where the cost is


def test_editorial_validate_rejects_a_bad_uf(monkeypatch) -> None:
    resolve, code = _validate_cli(monkeypatch, "editorial-validate", "XX")
    assert code == 1 and resolve.call_count == 0


def test_editorial_resolve_dispatches_the_free_resolve_of_each_uf_without_any_switch(
    monkeypatch,
) -> None:
    """The post-deploy step: it must work with the paid validation (and the lane) off."""
    from brave import cli
    from brave.tasks import pipeline

    resolve = MagicMock()
    monkeypatch.setattr(pipeline.editorial_resolve, "delay", resolve)
    monkeypatch.setattr(cli, "_editorial_validate_enabled", lambda: False)
    monkeypatch.setattr(cli, "_editorial_enabled", lambda: False)
    monkeypatch.setattr(sys, "argv", ["brave", "editorial-resolve", "--all"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0 and resolve.call_count == 27
