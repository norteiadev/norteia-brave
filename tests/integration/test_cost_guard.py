"""Integration tests for the USD cost guard (pre_dispatch_check, record_spend).

Uses fakeredis — no Redis container required.
"""

import pytest

from brave.config.settings import LLMConfig
from brave.observability.cost_guard import CostGuardError, pre_dispatch_check, record_spend


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
    config = LLMConfig(usd_daily_budget=100.0)

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
