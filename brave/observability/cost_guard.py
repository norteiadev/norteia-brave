"""USD cost guard — enforcing daily spend ceiling (D-20, OBS-02).

The cost guard HALTS execution before dispatching an LLM call
when the Redis daily counter >= usd_daily_budget. It does NOT
just alert — it raises CostGuardError which the Celery task must handle.

Key design choices:
  - Redis INCRBYFLOAT for atomic float increments
  - Daily key format: "brave:cost:daily:{YYYY-MM-DD}" — the date is **UTC**
  - TTL set to end of the SAME UTC day so counters auto-expire without a cron job
  - pre_dispatch_check raises BEFORE any LLM call or DB write
  - Never log the budget value or remaining amount in error messages
    that could leak cost strategy

Day boundary = UTC, deliberately (was: local date for the key, UTC midnight for the TTL —
so under America/Sao_Paulo the counter EXPIRED at 21:00 local and the daily budget silently
reset three hours early, every day). UTC over local because the counter is shared by workers
whose TZ we do not control, and because the providers meter and invoice in UTC — a "day" that
matches the bill is the one worth guarding. Consequence: the budget resets at 21:00 local
(Brasília), mid-evening rather than at local midnight. That is a visible, documented rollover,
not a silent early reset.

Deferred (batched) spend can settle on a DIFFERENT day than it was reserved on: a Message Batch
is reserved at submit and reconciled when its results arrive up to 24h later, so a batch
submitted at 23:20 UTC is collected on the next UTC day. A REFUND must therefore be pinned to
the day it was booked on — see :func:`reconcile_spend` and its ``reserved_at``. The zero floor
in _incr does NOT make cross-day refunds safe: it only stops the counter going negative, while
the damage is the erasure of OTHER lanes' spend already on today's key (150 x -$0.066 against a
day that never held that reservation zeroes desmembramento's and WhatsApp's spend and hands
every lane a second full budget). The floor is a last-resort backstop, not the mechanism.

See PITFALLS §8: cost guard is enforcing, not advisory.
"""

from datetime import UTC, datetime, timedelta

from redis import Redis

from brave.config.settings import LLMConfig
from brave.shared.exceptions import CostGuardError

# CostGuardError is defined in the central hierarchy (brave/shared/exceptions.py)
# and imported above. Re-exported here (it is raised below) so existing importers
# keep working unchanged.


def _daily_key(now: datetime | None = None) -> str:
    """Return the Redis key for the current UTC day's USD cost counter.

    ``now`` pins the instant: a test seam so key and TTL agree, and the way reconcile_spend
    names the day a reservation was BOOKED on. Production reads of "today" pass nothing.

    A naive ``now`` is read as UTC, not as local time: it arrives from a timestamp column, and
    some drivers hand back naive datetimes even for `timestamptz` — assuming local there would
    silently name the wrong day for anyone running west of UTC.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return f"brave:cost:daily:{now.astimezone(UTC).date().isoformat()}"


def _seconds_until_midnight(now: datetime | None = None) -> int:
    """Seconds until the next UTC midnight — i.e. until _daily_key() names a new key.

    Must agree with _daily_key at the same instant: TTL and key name are one decision.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


def _incr(redis_client: Redis, usd_amount: float) -> float:
    """Apply a signed USD delta to TODAY's counter, floored at zero, TTL guaranteed.

    Floor: a refund whose reservation is not on this key (Redis flushed, or a settle that
    escaped reconcile_spend's day check) would otherwise drive the counter negative and hand
    out free budget for the rest of the day. Backstop only — cross-day refunds are stopped
    upstream, in reconcile_spend.

    TTL: a negative-only delta can CREATE the key (INCRBYFLOAT on a missing key), and a key
    with no expiry never resets. Every write re-checks, so the counter always dies with its
    day.
    """
    key = _daily_key()
    new_total = float(redis_client.incrbyfloat(key, usd_amount))
    if new_total < 0.0:
        # Correct RELATIVELY (add back exactly what we saw below zero) instead of SET 0: a
        # concurrent increment landing between the two calls survives, where a SET would drop
        # it. Still two round-trips, so the read is stale — but no write is lost, which is the
        # only reason this would have needed a Lua script.
        new_total = float(redis_client.incrbyfloat(key, -new_total))

    ttl = redis_client.ttl(key)
    if ttl < 0:  # -1 = no expiry (key just created, or just SET above)
        redis_client.expire(key, _seconds_until_midnight())

    return new_total


def pre_dispatch_check(redis_client: Redis, config: LLMConfig, projected_usd: float = 0.0) -> None:
    """Check if the daily USD budget has been reached BEFORE dispatching an LLM call.

    This must be called BEFORE any LLM invocation. If the budget is exceeded,
    raises CostGuardError immediately — no LLM call is made.

    Args:
        redis_client:  Redis client (real or fakeredis).
        config:        LLMConfig with usd_daily_budget.
        projected_usd: Spend this dispatch is about to COMMIT, when the caller knows it
                       upfront and cannot stop it afterwards (a Message Batch commits N
                       requests in one irrevocable create call). Default 0.0 keeps the
                       plain "already over budget?" check for per-call dispatchers, which
                       stop on the next call anyway.

    Raises:
        CostGuardError: If the daily counter + projected_usd >= usd_daily_budget.
    """
    key = _daily_key()
    raw = redis_client.get(key)
    current = float(raw) if raw is not None else 0.0
    if current + projected_usd >= config.usd_daily_budget:
        raise CostGuardError(
            f"Daily LLM budget exceeded (limit: {config.usd_daily_budget} USD). "
            "Halting dispatch. Reset happens automatically at midnight."
        )


def record_spend(redis_client: Redis, usd_amount: float) -> float:
    """Record LLM spend (or a RESERVATION of it) in the Redis daily counter.

    Uses INCRBYFLOAT for atomic float increment.
    Sets TTL to end of the UTC day on first write so the counter auto-expires.

    Two legitimate uses:
      - the real cost of a call that already happened (per-call dispatchers);
      - a RESERVATION of projected spend, immediately before an irrevocable commit
        (``batches.create`` charges for N requests in one call). Every reservation must
        later be settled with :func:`reconcile_spend` — see its contract.

    Args:
        redis_client: Redis client (real or fakeredis).
        usd_amount:   Amount spent/reserved in USD (e.g., 0.0015). Must be >= 0 —
                      refunds go through :func:`reconcile_spend`, which floors the counter.

    Returns:
        The new total daily counter value after increment.
    """
    return _incr(redis_client, max(0.0, usd_amount))


def reconcile_spend(
    redis_client: Redis,
    reserved_usd: float,
    actual_usd: float,
    reserved_at: datetime | None = None,
) -> float:
    """Settle ONE previously reserved unit of spend against its real bill.

    Contract for the batch lane (submit reserves, collect reconciles up to 24h later):

      1. At submit: ``record_spend(redis, n_requests * projected_per_request)`` — one
         reservation covering the whole irrevocable ``batches.create``.
      2. At collect: call this ONCE PER SUBMITTED REQUEST with the SAME ``reserved_usd`` the
         reservation was sized on AND the submit timestamp as ``reserved_at``, including for
         outcomes Anthropic did not bill (expired/canceled → ``actual_usd=0.0``). A submitted
         request that is never settled leaves that day's budget overstated and the sweep
         starves for the rest of it.

    ``reserved_at`` is what makes a cross-midnight settle safe. Counters are per-UTC-day keys,
    so a reservation booked at 23:20 and collected at 00:30 belongs to a key that no longer
    exists. Refunding it against TODAY's key would subtract from spend that other lanes really
    incurred today — zeroing the counter and handing every lane a second budget. So:

      - booked today  → the full signed delta lands on today's counter (the normal case);
      - booked earlier → only a POSITIVE delta lands, on TODAY's counter. The refund half is
        dropped on purpose: the reservation expired with its key, so there is nothing left to
        give back. An overrun is different — that money was genuinely spent and never counted
        anywhere (its day's key is gone), and charging it to today is the conservative choice:
        it can only tighten the guard, never loosen it.

    Omitting ``reserved_at`` means "booked today". Correct for same-tick settles; a caller
    settling a reservation that can outlive a UTC midnight MUST pass it.

    Do NOT pass a pre-computed negative delta to :func:`record_spend`: it clamps at 0, so a
    refund would be silently dropped. This function is the only refund path.

    Args:
        reserved_usd: what was reserved for this one request at submit (>= 0).
        actual_usd:   what it really cost (>= 0; 0.0 for unbilled outcomes).
        reserved_at:  when the reservation was booked (the submit timestamp). Naive = UTC.

    Returns:
        The new total daily counter value, never below 0.
    """
    delta = actual_usd - reserved_usd
    if reserved_at is not None and _daily_key(reserved_at) != _daily_key():
        delta = max(0.0, delta)
    return _incr(redis_client, delta)
