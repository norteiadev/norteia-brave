"""Unit tests for the WhatsApp quality-rating auto-pause flag (CR-02, D-11).

Covers:
  - set/clear of the wa:quality_red flag (RED/GREEN/YELLOW)
  - is_quality_red fail-closed behavior when Redis is unreachable (CR-02)
  - get_redis() never silently falls back to fakeredis in production (CR-02)
"""

from __future__ import annotations

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from brave.compliance.quality_rating import (
    QUALITY_RED_KEY,
    is_quality_red,
    set_quality_flag,
)


def test_red_sets_flag_and_blocks() -> None:
    redis = fakeredis.FakeRedis()
    set_quality_flag(redis, "RED")
    assert is_quality_red(redis) is True


@pytest.mark.parametrize("rating", ["GREEN", "YELLOW"])
def test_green_yellow_clears_flag(rating: str) -> None:
    redis = fakeredis.FakeRedis()
    redis.set(QUALITY_RED_KEY, "1")
    set_quality_flag(redis, rating)
    assert is_quality_red(redis) is False


def test_is_quality_red_fail_closed_on_redis_error() -> None:
    """CR-02: if Redis cannot be reached, is_quality_red must return True (block)."""

    class _BrokenRedis:
        def exists(self, *_args, **_kwargs):
            raise RedisConnectionError("redis down")

    assert is_quality_red(_BrokenRedis()) is True


def test_get_redis_no_fakeredis_fallback_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """CR-02: a Redis ping failure must raise — never silently use fakeredis."""
    import brave.api.deps as deps

    monkeypatch.setattr(deps, "_redis_client", None, raising=False)
    monkeypatch.delenv("BRAVE_USE_FAKEREDIS", raising=False)
    # Point at an unroutable port so ping() fails fast.
    monkeypatch.setenv("BRAVE_DB_REDIS_URL", "redis://127.0.0.1:1/0")

    with pytest.raises(Exception):  # noqa: B017 — any redis connection error is acceptable
        deps.get_redis()

    # Confirm no client was cached on failure (so a later real connection works).
    assert deps._redis_client is None
    monkeypatch.setattr(deps, "_redis_client", None, raising=False)


def test_get_redis_explicit_fakeredis_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """CR-02: fakeredis is selectable ONLY by the explicit dev/test flag."""
    import brave.api.deps as deps

    monkeypatch.setattr(deps, "_redis_client", None, raising=False)
    monkeypatch.setenv("BRAVE_USE_FAKEREDIS", "1")
    try:
        client = deps.get_redis()
        assert isinstance(client, fakeredis.FakeRedis)
    finally:
        monkeypatch.setattr(deps, "_redis_client", None, raising=False)
