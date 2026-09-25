"""brave.core.beat_health + its wiring into the maintenance beat tasks (fakeredis, no DB)."""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest

from brave.core import beat_health
from brave.tasks import pipeline


def test_record_list_and_clear():
    fake = fakeredis.FakeStrictRedis()
    beat_health.record_error(fake, "brave.b", ValueError("cpf 123.456.789-00"))
    beat_health.record_error(fake, "brave.a", KeyError("x"))

    errors = beat_health.beat_errors(fake)

    assert [(e["task"], e["error_type"]) for e in errors] == [
        ("brave.a", "KeyError"), ("brave.b", "ValueError"),
    ]
    assert all(e["at"] for e in errors)
    assert b"cpf" not in fake.get("brave:beat:last_error:brave.b")  # type only, never the message
    assert 0 < fake.ttl("brave:beat:last_error:brave.b") <= 7 * 24 * 3600

    beat_health.clear_error(fake, "brave.b")
    assert [e["task"] for e in beat_health.beat_errors(fake)] == ["brave.a"]


@pytest.fixture
def fake(monkeypatch):
    fake = fakeredis.FakeStrictRedis()
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake)
    return fake


def test_prune_records_then_clears(monkeypatch, fake):
    session = MagicMock()
    session.execute.side_effect = RuntimeError("db down")
    monkeypatch.setattr(pipeline, "_get_session", lambda: (session, None))

    assert pipeline.prune_record_events_task.run() == 0
    assert [(e["task"], e["error_type"]) for e in beat_health.beat_errors(fake)] == [
        ("brave.prune_record_events", "RuntimeError"),
    ]

    session.execute.side_effect = None
    session.execute.return_value = MagicMock(rowcount=2)
    assert pipeline.prune_record_events_task.run() == 2
    assert beat_health.beat_errors(fake) == []


def test_sweeper_failure_is_recorded_and_still_raises(monkeypatch, fake):
    def boom():
        raise ConnectionError("no db")

    monkeypatch.setattr(pipeline, "_get_session", boom)

    with pytest.raises(ConnectionError):
        pipeline.redispatch_stalled_chain.run()

    assert [e["task"] for e in beat_health.beat_errors(fake)] == ["brave.redispatch_stalled_chain"]
