"""brave.tasks.failure_policy against Celery's REAL retry semantics.

Every task runs through ``task.apply`` (eager): ``retries=3`` makes the next retry() the
exhausting one, where Celery re-raises the original exception (never
MaxRetriesExceededError when exc= is given). Nothing here mocks ``retry``.

The failure is injected at each task's first touch: ``_load_config`` and the task
session's ``get`` both raise.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import fakeredis
import pytest
from celery import states
from sqlalchemy import delete, select

from brave.core import engine as collection_engine
from brave.core.models import PoisonQuarantine
from brave.shared.exceptions import ComplianceError, ProviderBalanceError
from brave.tasks import pipeline

_RIO = str(uuid.uuid4())

# (task attribute, args, quarantine payload, pause action)
F1 = [
    ("process_nascente", (_RIO,), None, None),
    ("discover_atrativo_task", ("ZZ",), {"uf": "ZZ"}, "sweep"),
    ("sweep_tripadvisor", ("ZZ",), {"uf": "ZZ"}, "sweep"),
    ("find_contacts_task", (_RIO,), {"rio_id": _RIO}, None),
    ("gather_signals_task", (_RIO,), {"rio_id": _RIO}, None),
    ("enrich_places_task", (_RIO,), {"rio_id": _RIO}, None),
    ("outreach_task", (_RIO,), {"rio_id": _RIO}, None),
    ("resume_conversation_task", (_RIO, "oi"), {"rio_id": _RIO}, None),
    ("discover_whatsapp_number_task", (_RIO,), {"rio_id": _RIO}, None),
]
_IDS = [t[0] for t in F1]
_REAL_PRODUCER_DONE = pipeline._producer_done


def _fail_with(monkeypatch, exc: Exception, real_session_after_first: bool = False):
    """Make every task fail with ``exc`` at its first step; returns (boom, calls, redis).

    The task's own session is a mock whose ``get`` raises. With
    ``real_session_after_first`` the later _get_session calls (the quarantine write)
    get the real test DB session.
    """
    calls = []

    def boom(*_a, **_k):
        calls.append(1)
        raise exc

    task_session = MagicMock()
    task_session.get.side_effect = boom
    real = pipeline._get_session
    opened = []

    def get_session():
        opened.append(1)
        if len(opened) == 1 or not real_session_after_first:
            return task_session, None
        return real()

    monkeypatch.setattr(pipeline, "_get_session", get_session)
    monkeypatch.setattr(pipeline, "_load_config", boom)
    monkeypatch.setattr(pipeline, "_producer_done", MagicMock())
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    return boom, calls, fake


# 1. Exhausted → poison_quarantine row + the original exception re-raised (FAILURE).
@pytest.mark.integration
@pytest.mark.parametrize(("attr", "args", "payload", "_action"), F1, ids=_IDS)
def test_exhausted_retries_quarantine_and_reraise(
    monkeypatch, db_session, attr, args, payload, _action
):
    token = f"boom-{uuid.uuid4()}"
    _fail_with(monkeypatch, RuntimeError(token), real_session_after_first=True)
    task = getattr(pipeline, attr)
    try:
        result = task.apply(args=args, retries=task.max_retries)

        assert result.state == states.FAILURE
        assert isinstance(result.result, RuntimeError) and str(result.result) == token
        rows = db_session.scalars(
            select(PoisonQuarantine).where(PoisonQuarantine.error_message == token)
        ).all()
        assert [(r.task_name, r.payload or None) for r in rows] == [(task.name, payload)]
        if attr == "process_nascente":
            assert str(rows[0].nascente_id) == _RIO
    finally:
        db_session.execute(delete(PoisonQuarantine).where(PoisonQuarantine.error_message == token))
        db_session.commit()


# 2. ProviderBalanceError → pause with the task's action; no retry, no quarantine.
@pytest.mark.parametrize(("attr", "args", "_payload", "action"), F1, ids=_IDS)
def test_provider_balance_pauses_without_retry_or_quarantine(
    monkeypatch, attr, args, _payload, action
):
    _boom, calls, fake = _fail_with(monkeypatch, ProviderBalanceError("tavily"))
    quarantine = MagicMock()
    monkeypatch.setattr("brave.core.quarantine.quarantine_poison", quarantine)

    result = getattr(pipeline, attr).apply(args=args)

    assert result.state == states.SUCCESS
    assert len(calls) == 1  # no retry
    quarantine.assert_not_called()
    reason = collection_engine.get_pause_reason(fake)
    assert (reason["reason"], reason["provider"], reason["action"]) == (
        "provider_balance", "tavily", action,
    )
    assert collection_engine.get_mode(fake) == collection_engine.PAUSADO


# 1b. Inline .run() (no broker): not exhausted — the caller decides, no quarantine here.
def test_inline_run_failure_reraises_without_quarantine(monkeypatch):
    _fail_with(monkeypatch, RuntimeError("boom"))
    quarantine = MagicMock()
    monkeypatch.setattr("brave.core.quarantine.quarantine_poison", quarantine)

    with pytest.raises(RuntimeError, match="boom"):
        pipeline.gather_signals_task.run(_RIO)

    quarantine.assert_not_called()


# 3. ComplianceError → blocked, no retry, no quarantine, no pause.
@pytest.mark.parametrize(("attr", "args", "_payload", "_action"), F1, ids=_IDS)
def test_compliance_error_ends_without_retry_or_quarantine(
    monkeypatch, attr, args, _payload, _action
):
    _boom, calls, fake = _fail_with(monkeypatch, ComplianceError("opt-out"))
    quarantine = MagicMock()
    monkeypatch.setattr("brave.core.quarantine.quarantine_poison", quarantine)

    result = getattr(pipeline, attr).apply(args=args)

    assert result.state == states.SUCCESS
    assert len(calls) == 1
    quarantine.assert_not_called()
    assert collection_engine.get_pause_reason(fake) is None


# 5. Celery's Retry still passes through the policy, and _producer_done skips it: a
# producer retried 3 times completes its run ONCE, on the terminal (exhausted) run.
def test_producer_done_ignores_retry_and_fires_once_on_the_terminal_run(monkeypatch):
    _boom, calls, _fake = _fail_with(monkeypatch, RuntimeError("flap"))
    monkeypatch.setattr(pipeline, "_producer_done", _REAL_PRODUCER_DONE)
    lifecycle = MagicMock(return_value=False)
    monkeypatch.setattr(pipeline, "_lifecycle", lifecycle)
    quarantine = MagicMock()
    monkeypatch.setattr("brave.core.quarantine.quarantine_poison", quarantine)

    result = pipeline.discover_atrativo_task.apply(args=("ZZ",), kwargs={"run_id": "r1"})

    assert len(calls) == 4  # first run + 3 eager retries
    assert result.state == states.FAILURE
    lifecycle.assert_called_once()
    assert lifecycle.call_args.args[2] == "r1"
    quarantine.assert_called_once()



# 6. F2 — publish_mar / reprocess_record exhaust → FAILURE, no quarantine.
@pytest.mark.parametrize("attr", ["publish_mar", "reprocess_record_task"])
def test_f2_exhausted_fails_without_quarantine(monkeypatch, attr):
    boom, calls, _fake = _fail_with(monkeypatch, RuntimeError("api 500"))
    monkeypatch.setattr(pipeline, "publish", boom)
    monkeypatch.setattr(pipeline, "clients_for", lambda *_a, **_k: MagicMock())
    quarantine = MagicMock()
    monkeypatch.setattr("brave.core.quarantine.quarantine_poison", quarantine)
    task = getattr(pipeline, attr)

    result = task.apply(args=(_RIO,), retries=task.max_retries)

    assert result.state == states.FAILURE
    assert len(calls) == 1
    quarantine.assert_not_called()
