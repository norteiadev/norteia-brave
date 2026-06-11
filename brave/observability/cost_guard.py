"""USD cost guard — enforcing daily spend ceiling (D-20, OBS-02).

The cost guard HALTS execution before dispatching an LLM call
when the Redis daily counter >= usd_daily_budget. It does NOT
just alert — it raises CostGuardError which the Celery task must handle.

Key design choices:
  - Redis INCRBYFLOAT for atomic float increments
  - Daily key format: "brave:cost:daily:{YYYY-MM-DD}"
  - TTL set to end-of-day so counters auto-expire without a cron job
  - pre_dispatch_check raises BEFORE any LLM call or DB write
  - Never log the budget value or remaining amount in error messages
    that could leak cost strategy

See PITFALLS §8: cost guard is enforcing, not advisory.
"""

import time
from datetime import date

from redis import Redis

from brave.config.settings import LLMConfig


class CostGuardError(Exception):
    """Raised by pre_dispatch_check when daily USD budget is exceeded.

    This is an operational halt, not a bug. The Celery task should catch this,
    log appropriately (without leaking budget details), and abort the LLM call.
    """


def _daily_key() -> str:
    """Return the Redis key for today's daily USD cost counter."""
    return f"brave:cost:daily:{date.today().isoformat()}"


def _seconds_until_midnight() -> int:
    """Return the number of seconds until midnight UTC."""
    now = time.time()
    # Get start of tomorrow in UTC (approximate — uses local midnight, good enough for budget)
    tomorrow = (int(now) // 86400 + 1) * 86400
    return max(1, int(tomorrow - now))


def pre_dispatch_check(redis_client: Redis, config: LLMConfig) -> None:
    """Check if the daily USD budget has been reached BEFORE dispatching an LLM call.

    This must be called BEFORE any LLM invocation. If the budget is exceeded,
    raises CostGuardError immediately — no LLM call is made.

    Args:
        redis_client: Redis client (real or fakeredis).
        config:       LLMConfig with usd_daily_budget.

    Raises:
        CostGuardError: If the daily counter >= usd_daily_budget.
    """
    key = _daily_key()
    raw = redis_client.get(key)
    current = float(raw) if raw is not None else 0.0
    if current >= config.usd_daily_budget:
        raise CostGuardError(
            f"Daily LLM budget exceeded (limit: {config.usd_daily_budget} USD). "
            "Halting dispatch. Reset happens automatically at midnight."
        )


def record_spend(redis_client: Redis, usd_amount: float) -> float:
    """Record LLM spend in the Redis daily counter.

    Uses INCRBYFLOAT for atomic float increment.
    Sets TTL to end-of-day on first write so the counter auto-expires.

    Args:
        redis_client: Redis client (real or fakeredis).
        usd_amount:   Amount spent in USD (e.g., 0.0015).

    Returns:
        The new total daily counter value after increment.
    """
    key = _daily_key()
    new_total = float(redis_client.incrbyfloat(key, usd_amount))

    # Set TTL to end-of-day on first increment (TTL=1 means key is new)
    # Using a conditional set: if TTL is -1 (no expiry) or key was just created
    ttl = redis_client.ttl(key)
    if ttl < 0:  # -1 = no expiry, -2 = key doesn't exist (just created)
        redis_client.expire(key, _seconds_until_midnight())

    return new_total
