"""Phase D: operator mode is durably persisted to config_settings.

Phase C kept the operator mode Redis-only, so a Redis flush silently reset it to
LIGADO (re-locking the Kanban cards + resuming auto-collection). Phase D upserts the
mode into ``config_settings`` on write and self-heals Redis from that row on read.

Fully offline: fakeredis + an in-memory config_settings fake session (no Postgres).
"""

from __future__ import annotations

import fakeredis
import pytest

from brave.config.runtime import load_effective_config
from brave.core import engine as ce
from brave.core.models import AuditLog, ConfigSetting


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return [(k, r.value) for k, r in self._rows.items()]

    def scalars(self):
        return list(self._rows.keys())


class FakeSession:
    """In-memory config_settings store (get/add/execute/flush)."""

    def __init__(self):
        self.rows: dict[str, ConfigSetting] = {}

    def get(self, _model, key):
        return self.rows.get(key)

    def add(self, obj):
        if isinstance(obj, ConfigSetting):
            self.rows[obj.key] = obj
        elif isinstance(obj, AuditLog):  # pragma: no cover - not used here
            pass

    def execute(self, _stmt):
        return _FakeResult(self.rows)

    def flush(self):
        pass


@pytest.fixture
def redis():
    return fakeredis.FakeStrictRedis()


@pytest.fixture
def session():
    return FakeSession()


def test_set_mode_with_session_upserts_config_row(redis, session):
    ce.set_mode(redis, ce.PAUSADO, session=session)
    # Durable row written under the {"v": ...} wrapper.
    assert session.rows["engine.mode"].value == {"v": "PAUSADO"}
    # Redis is the fast path — still reflects the live mode.
    assert ce.get_mode(redis) == ce.PAUSADO


def test_mode_survives_a_redis_flush(redis, session):
    ce.set_mode(redis, ce.PAUSADO, session=session)

    # Simulate a Redis flush — the live mode key is gone.
    redis.flushall()
    assert redis.get(ce._MODE_KEY) is None

    # Phase-C behavior (no session) still defaults to LIGADO on a miss.
    assert ce.get_mode(redis) == ce.LIGADO

    # Phase D: with the durable session, get_mode heals from config_settings AND
    # re-seeds the Redis fast path.
    assert ce.get_mode(redis, session=session) == ce.PAUSADO
    assert ce.get_mode(redis) == ce.PAUSADO  # re-seeded → subsequent reads need no session


def test_desligado_persists_and_survives_flush(redis, session):
    ce.set_mode(redis, ce.DESLIGADO, session=session)
    assert session.rows["engine.mode"].value == {"v": "DESLIGADO"}
    # DESLIGADO side effects still ran (hard off).
    assert ce.get_state(redis) == ce.IDLE
    assert ce.is_enabled(redis) is False

    redis.flushall()
    assert ce.get_mode(redis, session=session) == ce.DESLIGADO


def test_load_effective_config_reflects_persisted_mode(redis, session):
    ce.set_mode(redis, ce.PAUSADO, session=session)
    cfg = load_effective_config(session)
    assert cfg.engine.mode == "PAUSADO"


def test_set_mode_without_session_is_redis_only(redis, session):
    # No session → no durable row (exact Phase-C behavior preserved).
    ce.set_mode(redis, ce.PAUSADO)
    assert session.rows == {}
    assert ce.get_mode(redis) == ce.PAUSADO
