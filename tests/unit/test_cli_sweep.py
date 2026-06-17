"""Unit tests for the `brave.cli sweep` ops-trigger subcommand (ORCH-03, D-05).

The sweep command kicks an on-demand UF sweep without waiting for the 2/3 AM
beat. It dispatches the existing producer/chain tasks:

  --lane destinos   → sweep_uf(uf)
  --lane atrativos  → discover_atrativo_task(uf)   (auto-chains to the gate)
  --lane both       → both (default)

Dispatch-then-inline-fallback (mirrors dlq.py:104-114): when .delay raises
(no Celery broker), the command falls back to .run(uf) synchronously.

These are PURE-UNIT tests: both .delay AND .run are monkeypatched with spies,
so no broker and no DB are touched (D-06 / ORCH-04, 100% offline / keyless).
"""

import sys

import pytest

from brave import cli
from brave.tasks import pipeline


@pytest.fixture
def spies(monkeypatch):
    """Replace sweep_uf/discover_atrativo_task .delay and .run with call-recording spies."""
    calls = {
        "sweep_delay": [],
        "sweep_run": [],
        "atrativo_delay": [],
        "atrativo_run": [],
    }

    monkeypatch.setattr(pipeline.sweep_uf, "delay", lambda uf: calls["sweep_delay"].append(uf))
    monkeypatch.setattr(pipeline.sweep_uf, "run", lambda uf: calls["sweep_run"].append(uf))
    monkeypatch.setattr(
        pipeline.discover_atrativo_task, "delay", lambda uf: calls["atrativo_delay"].append(uf)
    )
    monkeypatch.setattr(
        pipeline.discover_atrativo_task, "run", lambda uf: calls["atrativo_run"].append(uf)
    )
    return calls


def _run_cli(monkeypatch, argv):
    """Drive cli.main() with a patched sys.argv."""
    monkeypatch.setattr(sys, "argv", ["brave", *argv])
    cli.main()


def test_cli_sweep_dispatches_both(monkeypatch, spies):
    """`sweep BA` (default lane=both) dispatches BOTH sweep_uf and discover_atrativo_task."""
    _run_cli(monkeypatch, ["sweep", "BA"])

    assert spies["sweep_delay"] == ["BA"]
    assert spies["atrativo_delay"] == ["BA"]
    # No inline fallback fired (delay succeeded)
    assert spies["sweep_run"] == []
    assert spies["atrativo_run"] == []


def test_cli_sweep_uppercases_uf(monkeypatch, spies):
    """A lowercase UF is normalized to uppercase before dispatch."""
    _run_cli(monkeypatch, ["sweep", "ba"])

    assert spies["sweep_delay"] == ["BA"]
    assert spies["atrativo_delay"] == ["BA"]


def test_cli_sweep_lane_destinos_only(monkeypatch, spies):
    """`--lane destinos` dispatches ONLY sweep_uf (no atrativos)."""
    _run_cli(monkeypatch, ["sweep", "BA", "--lane", "destinos"])

    assert spies["sweep_delay"] == ["BA"]
    assert spies["atrativo_delay"] == []


def test_cli_sweep_lane_atrativos_only(monkeypatch, spies):
    """`--lane atrativos` dispatches ONLY discover_atrativo_task (no destinos)."""
    _run_cli(monkeypatch, ["sweep", "BA", "--lane", "atrativos"])

    assert spies["sweep_delay"] == []
    assert spies["atrativo_delay"] == ["BA"]


def test_cli_sweep_inline_fallback(monkeypatch, spies):
    """When .delay raises (no broker), the command falls back to .run(uf) inline.

    BRAVE_DB_URL is set so the inline path is reached (the real inline run needs a
    DB URL; .run is spied here so no DB is actually touched — pure offline).
    """
    monkeypatch.setenv("BRAVE_DB_URL", "postgresql+psycopg://x:x@localhost:5432/x")

    def _raise(uf):
        raise RuntimeError("no broker")

    monkeypatch.setattr(pipeline.sweep_uf, "delay", _raise)
    monkeypatch.setattr(pipeline.discover_atrativo_task, "delay", _raise)

    _run_cli(monkeypatch, ["sweep", "BA"])

    # delay failed → inline .run fired for both lanes
    assert spies["sweep_run"] == ["BA"]
    assert spies["atrativo_run"] == ["BA"]


def test_cli_sweep_inline_fallback_no_db_url_degrades_gracefully(monkeypatch, spies, capsys):
    """With BRAVE_DB_URL unset, the inline fallback degrades gracefully (no .run, no crash)."""
    monkeypatch.delenv("BRAVE_DB_URL", raising=False)

    def _raise(uf):
        raise RuntimeError("no broker")

    monkeypatch.setattr(pipeline.sweep_uf, "delay", _raise)
    monkeypatch.setattr(pipeline.discover_atrativo_task, "delay", _raise)

    _run_cli(monkeypatch, ["sweep", "BA"])

    # Did NOT crash and did NOT run inline (no DB URL); printed a clear message instead.
    assert spies["sweep_run"] == []
    assert spies["atrativo_run"] == []
    out = capsys.readouterr().out
    assert "BRAVE_DB_URL not set" in out


def test_cli_sweep_unknown_lane_exits_nonzero(monkeypatch, spies):
    """An unknown --lane value exits non-zero with a usage hint, dispatching nothing."""
    with pytest.raises(SystemExit) as exc:
        _run_cli(monkeypatch, ["sweep", "BA", "--lane", "bogus"])

    assert exc.value.code != 0
    assert spies["sweep_delay"] == []
    assert spies["atrativo_delay"] == []


def test_cli_sweep_missing_uf_exits_nonzero(monkeypatch, spies):
    """`sweep` with no UF argument exits non-zero, dispatching nothing."""
    with pytest.raises(SystemExit) as exc:
        _run_cli(monkeypatch, ["sweep"])

    assert exc.value.code != 0
    assert spies["sweep_delay"] == []
    assert spies["atrativo_delay"] == []
