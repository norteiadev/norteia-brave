"""Integration tests for the USD cost guard (pre_dispatch_check, record_spend).

Uses fakeredis — no Redis container required.
"""

import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from brave.config.settings import LLMConfig
from brave.observability.cost_guard import (
    CostGuardError,
    _daily_key,
    _seconds_until_midnight,
    pre_dispatch_check,
    reconcile_spend,
    record_spend,
)


def test_pre_dispatch_check_passes_when_under_budget(fake_redis):
    """pre_dispatch_check passes (no raise) when counter < usd_daily_budget."""
    config = LLMConfig(usd_daily_budget=10.0)
    # Record less than the budget
    record_spend(fake_redis, 5.0)
    # Should not raise
    pre_dispatch_check(fake_redis, config)


def test_pre_dispatch_check_raises_when_at_budget(fake_redis):
    """pre_dispatch_check raises CostGuardError when counter >= usd_daily_budget."""
    config = LLMConfig(usd_daily_budget=10.0)
    # Record exactly the budget amount
    record_spend(fake_redis, 10.0)
    with pytest.raises(CostGuardError, match="budget"):
        pre_dispatch_check(fake_redis, config)


def test_pre_dispatch_check_raises_when_over_budget(fake_redis):
    """pre_dispatch_check raises CostGuardError when counter exceeds usd_daily_budget."""
    config = LLMConfig(usd_daily_budget=5.0)
    record_spend(fake_redis, 7.5)
    with pytest.raises(CostGuardError):
        pre_dispatch_check(fake_redis, config)


def test_record_spend_increments_counter(fake_redis):
    """record_spend increments Redis counter by the spent USD amount."""
    v1 = record_spend(fake_redis, 1.50)
    assert v1 == pytest.approx(1.50, abs=0.001)

    v2 = record_spend(fake_redis, 2.50)
    assert v2 == pytest.approx(4.00, abs=0.001)


def test_record_spend_returns_new_total(fake_redis):
    """record_spend returns the new total counter value."""
    record_spend(fake_redis, 3.14)
    total = record_spend(fake_redis, 1.00)
    assert total == pytest.approx(4.14, abs=0.001)


def test_pre_dispatch_check_passes_at_zero(fake_redis):
    """pre_dispatch_check passes when no spend recorded yet."""
    config = LLMConfig(usd_daily_budget=10.0)
    # No prior spend — counter is 0
    pre_dispatch_check(fake_redis, config)  # Should not raise


def test_cost_guard_error_is_exception():
    """CostGuardError is an Exception subclass."""
    err = CostGuardError("test message")
    assert isinstance(err, Exception)
    assert "test message" in str(err)


# --- day boundary (G2) -----------------------------------------------------
# The bug: key = LOCAL date, TTL = UTC midnight. In America/Sao_Paulo (UTC-3) the counter
# expired at 21:00 local while the key name only rolled at 00:00 local — the daily budget
# silently reset three hours early, every day.

_SAO_PAULO = timezone(timedelta(hours=-3))


def test_key_and_ttl_agree_at_the_sao_paulo_evening_boundary():
    """23:30 in São Paulo is already the NEXT UTC day: key rolls and TTL is nearly a full day."""
    late_local = datetime(2026, 7, 30, 23, 30, tzinfo=_SAO_PAULO)  # == 2026-07-31 02:30 UTC
    assert _daily_key(late_local).endswith("2026-07-31")
    # Not the ~30 minutes the old local-date/UTC-TTL mix implied.
    assert _seconds_until_midnight(late_local) == pytest.approx(21.5 * 3600, abs=1)


def test_ttl_expires_exactly_when_the_key_name_rolls():
    """One decision, two functions: the counter must die the instant its key name changes."""
    for now in (
        datetime(2026, 7, 30, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 7, 30, 20, 59, 59, tzinfo=UTC),  # 17:59 São Paulo, old expiry hour
        datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC),
    ):
        expiry = now + timedelta(seconds=_seconds_until_midnight(now))
        assert _daily_key(expiry) != _daily_key(now)
        assert _daily_key(expiry - timedelta(seconds=1)) == _daily_key(now)


def test_live_key_is_the_utc_date():
    assert _daily_key() == f"brave:cost:daily:{datetime.now(UTC).date().isoformat()}"


# --- reserve / reconcile (G1) ----------------------------------------------


def test_reconcile_settles_the_difference_both_ways(fake_redis):
    """Reserve N x projected at submit, settle each request against its real bill later."""
    record_spend(fake_redis, 3 * 0.10)  # reservation for a 3-request batch

    reconcile_spend(fake_redis, 0.10, 0.12)  # cost more than projected
    reconcile_spend(fake_redis, 0.10, 0.05)  # cost less
    total = reconcile_spend(fake_redis, 0.10, 0.0)  # expired — unbilled, reservation released

    assert total == pytest.approx(0.17, abs=0.001)


def test_reconcile_never_drives_the_counter_below_zero(fake_redis):
    """A refund with no matching reservation (day rolled / Redis flushed) must not free budget."""
    total = reconcile_spend(fake_redis, 5.0, 0.0)
    assert total == 0.0
    assert float(fake_redis.get(_daily_key())) == 0.0
    # A zeroed counter is not a credit: the next dispatch is sized against 0, not against -5.
    pre_dispatch_check(fake_redis, LLMConfig(usd_daily_budget=1.0), projected_usd=0.5)
    with pytest.raises(CostGuardError):
        pre_dispatch_check(fake_redis, LLMConfig(usd_daily_budget=1.0), projected_usd=1.0)


def test_refund_created_key_still_expires(fake_redis):
    """INCRBYFLOAT on a missing key creates it — without a TTL it would never reset."""
    reconcile_spend(fake_redis, 1.0, 0.0)
    assert 0 < fake_redis.ttl(_daily_key()) <= 86400


# --- cross-midnight settle (G3) --------------------------------------------
# A 23:20 UTC submit reserves on day-1's key; the batch expires and collect settles it at
# 00:30 UTC on day 2. Refunding those 150 x $0.066 against day 2 erases the spend other lanes
# really made today — the zero floor does not help, it IS the erasure.


def test_refund_of_a_rolled_over_reservation_leaves_todays_spend_alone(fake_redis):
    """The reservation died with its key; there is nothing left to give back."""
    yesterday = datetime.now(UTC) - timedelta(days=1)
    record_spend(fake_redis, 4.0)  # desmembramento + whatsapp, today, real money

    for _ in range(150):  # a whole expired batch settling with actual=0.0
        reconcile_spend(fake_redis, 0.066, 0.0, yesterday)

    assert float(fake_redis.get(_daily_key())) == pytest.approx(4.0, abs=0.001)
    with pytest.raises(CostGuardError):
        pre_dispatch_check(fake_redis, LLMConfig(usd_daily_budget=4.0))


def test_overrun_on_a_rolled_over_reservation_still_lands_on_today(fake_redis):
    """Real money nobody counted (its day's key is gone) — charge today, never refund today."""
    yesterday = datetime.now(UTC) - timedelta(days=1)
    reconcile_spend(fake_redis, 0.066, 0.10, yesterday)
    assert float(fake_redis.get(_daily_key())) == pytest.approx(0.034, abs=0.001)


def test_naive_reserved_at_is_read_as_utc(monkeypatch):
    """Timestamp columns can come back naive; reading one as LOCAL names the wrong day.

    22:00 naive is 2026-07-30 under UTC but 2026-07-31 read as São Paulo — the settle would
    then look cross-day (or not) purely because of the worker's TZ.
    """
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    time.tzset()
    try:
        assert _daily_key(datetime(2026, 7, 30, 22, 0)).endswith("2026-07-30")
    finally:
        # tzset is process-global; monkeypatch restores the env var but not the C library
        # state, so a leak here would silently move every later test's day boundary.
        monkeypatch.undo()
        time.tzset()


def test_zero_floor_does_not_drop_a_concurrent_increment(fake_redis):
    """The floor must not SET: another worker's spend can land inside the correction window."""

    class _RacingRedis:
        """Injects one concurrent +2.0 the moment _incr goes to correct a negative counter."""

        def __init__(self, inner):
            self._inner = inner
            self._raced = False

        def incrbyfloat(self, key, amount):
            if amount > 0 and not self._raced and float(self._inner.get(key) or 0) < 0:
                self._raced = True
                self._inner.incrbyfloat(key, 2.0)
            return self._inner.incrbyfloat(key, amount)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    reconcile_spend(_RacingRedis(fake_redis), 5.0, 0.0)
    assert float(fake_redis.get(_daily_key())) == pytest.approx(2.0, abs=0.001)


def test_record_spend_ignores_a_negative_amount(fake_redis):
    """record_spend is increment-only; refunds must go through reconcile_spend."""
    record_spend(fake_redis, 2.0)
    assert record_spend(fake_redis, -5.0) == pytest.approx(2.0, abs=0.001)
