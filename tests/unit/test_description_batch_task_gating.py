"""collect_description_batches_task gating: the reaper survives the master externals switch.

The task's own docstring promises the reaper is deliberately un-gated so that turning batch
mode off still unsticks stamped records. That was true for atrativo_description_batch_enabled
and false for run_real_externals: the early `if not run_real_externals: return` sat ABOVE
collect_batches, and reap_stale_claims lives inside it. An operator hitting the master switch
to stop all spend therefore froze every stamped record out of the eligibility query — no
description, ever, until externals came back.

100% offline: the task's session, config and copy_batch entry points are all patched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _run_task(*, run_real_externals: bool) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    from brave.tasks.pipeline import collect_description_batches_task

    raw_fn = collect_description_batches_task.__wrapped__.__func__
    session = MagicMock()

    with (
        patch("brave.tasks.pipeline._get_session", return_value=(session, MagicMock())),
        patch("brave.tasks.pipeline.AppConfig") as app_config,
        patch("brave.tasks.pipeline.load_effective_config"),
        patch("brave.tasks.pipeline._batch_deps", return_value=(MagicMock(), MagicMock())) as deps,
        patch("brave.lanes.atrativos.copy_batch.reap_stale_claims") as reap,
        patch("brave.lanes.atrativos.copy_batch.collect_batches") as collect,
    ):
        app_config.return_value.run_real_externals = run_real_externals
        raw_fn(SimpleNamespace())

    return session, reap, collect, deps


def test_reaper_runs_with_externals_off() -> None:
    """Master switch off: no Anthropic client is built and nothing is collected, but the
    claim placeholders are still freed — they need no probe, only the 25h cutoff."""
    session, reap, collect, deps = _run_task(run_real_externals=False)

    reap.assert_called_once_with(session, None)  # None client → every probe fails closed
    collect.assert_not_called()
    deps.assert_not_called()  # building the client is the first step toward calling it


def test_normal_path_still_reaps_inside_collect() -> None:
    """Externals on: collect_batches owns the reap (it must run BEFORE the worklist read),
    so the task must not reap a second time."""
    _session, reap, collect, _deps = _run_task(run_real_externals=True)

    collect.assert_called_once()
    reap.assert_not_called()
