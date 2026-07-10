"""Engine depth-gating tests (plan 10-02).

These lock the cost-checkpoint contract: the operator-selected *depth* (read once
at the /start edge in plan 10-01 and threaded down as an explicit task arg) decides
which producers fan out and how far the pipeline flows.

  nascente          — the default (Google Places) lane has NO free producer (the Mtur
                      destino seed is retired; Places always costs), so nothing is
                      dispatched for it. Zero external cost.
  nascente_rio      — producers + Rio routing, but the atrativos WhatsApp-gate FSM
                      chain is NOT kicked (neither find_contacts_task.delay nor its
                      .run inline fallback fires).
  nascente_rio_mar  — full pipeline as today (atrativos chain runs to the gate).

The recurring sweep auto-promotes to Mar under NO depth — Mar push stays on the
unchanged human DLQ gate + WhatsApp finalize path. We assert promote_to_mar /
push_mar are never invoked by the sweep under any of the three depths.

All tests are 100% offline: fakeredis, monkeypatched dispatch, fake/mocked clients
and sessions, no broker, RUN_REAL_EXTERNALS unset.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from brave.core import engine as collection_engine
from brave.tasks import pipeline


# ===========================================================================
# depth threaded through the orchestrator + per-depth gating
# ===========================================================================


@pytest.fixture
def running_engine(monkeypatch):
    """Fakeredis with the engine marked RUNNING and zero per-UF pacing delay."""
    fake = fakeredis.FakeStrictRedis()
    fake.set(collection_engine._STATE_KEY, collection_engine.RUNNING)
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    monkeypatch.setenv("BRAVE_ENGINE_UF_DELAY_SECONDS", "0")
    return fake


class _FakeTask:
    """Stand-in for a Celery task that records every .delay call.

    The orchestrator references producer tasks as module globals and calls
    `task.delay(...)`. Celery resolves `.delay` through a proxy such that a
    per-instance attribute patch is bypassed inside a running task, so we swap
    the whole task object on the module to capture dispatch deterministically.
    """

    def __init__(self, sink):
        self._sink = sink

    def delay(self, *args, **kwargs):
        self._sink.append((args, kwargs))


@pytest.fixture
def dispatch_spy(monkeypatch):
    """Record discover_atrativo_task.delay calls (no broker).

    The default (Places) lane fans out ONLY discover_atrativo_task — the Mtur
    sweep_uf destino seed is retired.
    """
    calls = {"discover": []}
    monkeypatch.setattr(pipeline, "discover_atrativo_task", _FakeTask(calls["discover"]))
    return calls


# --- engine_sweep_run fan-out per depth ------------------------------------


def test_sweep_run_accepts_depth_kwarg():
    """engine_sweep_run signature exposes a depth kwarg (10-01 dispatches it)."""
    import inspect

    sig = inspect.signature(pipeline.engine_sweep_run)
    assert "depth" in sig.parameters


def test_nascente_dispatches_nothing_for_default_lane(running_engine, dispatch_spy):
    """depth=nascente → the default (Places) lane has no free producer → nothing dispatched."""
    pipeline.engine_sweep_run.run(
        ufs=["BA"], lane="both", depth=collection_engine.NASCENTE
    )

    assert dispatch_spy["discover"] == []  # atrativos have no free source


@pytest.mark.parametrize(
    "depth", [collection_engine.NASCENTE_RIO, collection_engine.NASCENTE_RIO_MAR]
)
def test_rio_depths_dispatch_atrativos(running_engine, dispatch_spy, depth):
    """nascente_rio / nascente_rio_mar with lane=both → discover_atrativo, depth threaded."""
    pipeline.engine_sweep_run.run(ufs=["BA"], lane="both", depth=depth)

    assert len(dispatch_spy["discover"]) == 1
    assert dispatch_spy["discover"][0][1].get("depth") == depth


def test_sweep_run_defaults_to_full_depth_when_none(running_engine, dispatch_spy):
    """A legacy/direct call with depth=None preserves prior full behavior."""
    pipeline.engine_sweep_run.run(ufs=["BA"], lane="both", depth=None)

    assert len(dispatch_spy["discover"]) == 1
    assert dispatch_spy["discover"][0][1].get("depth") == collection_engine.NASCENTE_RIO_MAR


def test_sweep_run_returns_depth(running_engine, dispatch_spy):
    """The result dict echoes the effective depth."""
    result = pipeline.engine_sweep_run.run(
        ufs=["BA"], lane="both", depth=collection_engine.NASCENTE_RIO
    )
    assert result["depth"] == collection_engine.NASCENTE_RIO


# --- discover_atrativo_task: WhatsApp-gate chain kickoff gating ------------


@contextmanager
def _patched_discover(discovered_ids=("rio-1", "rio-2")):
    """Patch discover_atrativo_task internals: fake DiscoveryAgent + scalars query."""
    fc_calls = {"delay": 0, "run": 0}
    fake_session = MagicMock()
    fake_session.scalars.return_value.all.return_value = list(discovered_ids)

    class _FakeDiscovery:
        def __init__(self, *_a, **_k):
            pass

        async def produce(self, uf):
            return None

    class _SpyFindContacts:
        def delay(self, *_a, **_k):
            fc_calls["delay"] += 1

        def run(self, *_a, **_k):
            fc_calls["run"] += 1

    with patch.object(pipeline, "_get_session", return_value=(fake_session, MagicMock())), patch(
        "brave.lanes.atrativos.discovery_agent.DiscoveryAgent", _FakeDiscovery
    ), patch.object(pipeline, "find_contacts_task", _SpyFindContacts()):
        yield fc_calls


def test_discover_nascente_rio_does_not_kick_contacts_chain():
    """depth=nascente_rio → NEITHER find_contacts_task.delay NOR .run fires."""
    with _patched_discover() as fc_calls:
        pipeline.discover_atrativo_task.run("BA", depth=collection_engine.NASCENTE_RIO)

    assert fc_calls["delay"] == 0
    assert fc_calls["run"] == 0  # inline fallback also suppressed


def test_discover_nascente_rio_mar_kicks_contacts_chain():
    """depth=nascente_rio_mar → find_contacts_task.delay fires for each discovered row."""
    with _patched_discover(discovered_ids=("rio-1", "rio-2")) as fc_calls:
        pipeline.discover_atrativo_task.run("BA", depth=collection_engine.NASCENTE_RIO_MAR)

    assert fc_calls["delay"] == 2
    assert fc_calls["run"] == 0  # broker present (delay succeeds), no fallback


# --- No automated Mar push under ANY depth ---------------------------------


@pytest.mark.parametrize(
    "depth",
    [
        collection_engine.NASCENTE,
        collection_engine.NASCENTE_RIO,
        collection_engine.NASCENTE_RIO_MAR,
    ],
)
def test_sweep_never_auto_promotes_to_mar(running_engine, dispatch_spy, depth, monkeypatch):
    """Under EVERY depth, the orchestrator invokes no promote_to_mar / push_mar.

    Mar push stays on the unchanged human DLQ gate + WhatsApp finalize path; the
    recurring sweep must never auto-promote. Locks ENG-05.
    """
    promote_spy = MagicMock()
    monkeypatch.setattr("brave.core.mar.service.promote_to_mar", promote_spy)
    push_calls = []
    monkeypatch.setattr(pipeline, "push_mar", _FakeTask(push_calls))

    pipeline.engine_sweep_run.run(ufs=["BA"], lane="both", depth=depth)

    assert promote_spy.call_count == 0
    assert push_calls == []
