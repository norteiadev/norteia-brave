"""Apify cost-guard counters (places_provider="hybrid") — fakeredis, offline."""

import pytest

from brave.observability.cost_guard import (
    _seconds_until_midnight,
    apify_daily_spend,
    apify_total_spend,
    daily_spend,
    pre_dispatch_check_apify,
    record_apify_spend,
    settle_apify_spend,
)
from brave.shared.exceptions import CostGuardError, ProviderBalanceError


def test_record_lands_on_daily_and_total_keys(fake_redis):
    record_apify_spend(fake_redis, 0.0065)
    record_apify_spend(fake_redis, 0.0065)
    record_apify_spend(fake_redis, -1.0)  # never a refund

    assert apify_daily_spend(fake_redis) == pytest.approx(0.013)
    assert apify_total_spend(fake_redis) == pytest.approx(0.013)
    assert daily_spend(fake_redis) == 0.0, "not the LLM counter"
    (daily_key,) = fake_redis.keys("brave:cost:apify:daily:*")
    assert 0 < fake_redis.ttl(daily_key) <= _seconds_until_midnight()
    assert fake_redis.ttl("brave:cost:apify:total") == -1, "the total never expires"


def test_check_projects_the_run_cost(fake_redis):
    record_apify_spend(fake_redis, 0.99)
    pre_dispatch_check_apify(fake_redis, 0.01, daily_cap_usd=1.0, total_cap_usd=1.0)  # == cap ok
    with pytest.raises(CostGuardError):
        pre_dispatch_check_apify(fake_redis, 0.02, daily_cap_usd=1.0, total_cap_usd=9.0)


def test_total_cap_wins_and_survives_the_day_turn(fake_redis):
    record_apify_spend(fake_redis, 4.99)
    fake_redis.delete(*fake_redis.keys("brave:cost:apify:daily:*"))  # a new UTC day
    with pytest.raises(ProviderBalanceError) as exc:
        pre_dispatch_check_apify(fake_redis, 0.02, daily_cap_usd=0.01, total_cap_usd=5.0)
    assert exc.value.provider == "apify"


def test_settle_moves_a_reservation_to_the_real_cost(fake_redis):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    record_apify_spend(fake_redis, 1.0)  # reservation
    settle_apify_spend(fake_redis, 1.0, 0.25, now)  # cheaper than projected → refund
    assert apify_daily_spend(fake_redis) == pytest.approx(0.25)
    assert apify_total_spend(fake_redis) == pytest.approx(0.25)

    # Booked yesterday: today's counter never gets the refund (it was never on it)…
    settle_apify_spend(fake_redis, 1.0, 0.0, now - timedelta(days=1))
    assert apify_daily_spend(fake_redis) == pytest.approx(0.25)
    # …while the total (no day) does, floored at 0.
    assert apify_total_spend(fake_redis) == 0.0


def test_default_daily_cap_sits_below_the_total():
    """Total is checked first and holds every day's spend: daily >= total never trips, so
    no Apify stop would ever be the auto-resumed daily_budget."""
    from brave.config.settings import AppConfig

    fields = AppConfig.model_fields  # the defaults, not this machine's .env
    assert fields["apify_daily_usd_cap"].default < fields["apify_total_usd_cap"].default
