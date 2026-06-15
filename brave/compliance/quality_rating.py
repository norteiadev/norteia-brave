"""WhatsApp quality-rating Redis flag — auto-pause gate (D-11, COMP-02).

The quality-rating flag controls the compliance gate's condition 8:
if the WhatsApp quality rating is RED, all sends are paused until the
rating recovers to GREEN or YELLOW.

Mirrors brave/observability/cost_guard.py structure:
  - QUALITY_RED_KEY constant (mirrors _daily_key() constant)
  - is_quality_red (mirrors pre_dispatch_check — reads flag, raises-or-passes)
  - set_quality_flag (mirrors record_spend — writes flag)

No TTL is set on the RED flag: it persists until explicitly cleared by a
GREEN or YELLOW rating event. This is intentional — a RED rating means the
pipeline must pause until the operator explicitly receives a recovery signal
from Twilio/Meta.

Design (RESEARCH.md §Quality Rating Auto-Pause):
  GREEN  → delete key (clear pause flag, normal operation)
  YELLOW → delete key (clear pause, but throttle ramp cap separately)
  RED    → set key (pause all sends immediately)
"""

import structlog
from redis import Redis
from redis.exceptions import RedisError

QUALITY_RED_KEY = "wa:quality_red"

logger = structlog.get_logger(__name__)


def is_quality_red(redis_client: Redis) -> bool:
    """Return True if WhatsApp quality rating is RED (auto-pause active).

    Called as gate condition 8 in send_path_gate. Pure Redis read — no network,
    no DB. Fully offline-testable with fakeredis.

    CR-02 fail-closed: if Redis cannot be reached to read the flag, treat the
    rating as RED (return True) so the send is BLOCKED. A RED auto-pause that
    cannot be verified must never be assumed clear — silently passing would let
    sends continue during a quality incident (BSP violation).

    Args:
        redis_client: Redis client (real or fakeredis).

    Returns:
        True if wa:quality_red key exists OR Redis is unreachable (fail-closed);
        False only when Redis confirms the flag is absent.
    """
    try:
        return redis_client.exists(QUALITY_RED_KEY) > 0
    except RedisError as exc:
        # Fail-closed: cannot confirm quality is OK → block the send.
        logger.error("quality_flag_check_unreachable_fail_closed", error=str(exc))
        return True


def set_quality_flag(redis_client: Redis, rating: str) -> None:
    """Set or clear the quality-red auto-pause flag based on a rating event.

    Called by the quality-rating-webhook endpoint when Twilio/Meta signals
    a quality-rating change. No TTL — flag persists until GREEN/YELLOW clears it.

    Args:
        redis_client: Redis client (real or fakeredis).
        rating:       Quality rating string: "RED", "GREEN", or "YELLOW".
                      Case-insensitive (caller should .upper() before calling).

    Behavior:
        RED    → redis.set(QUALITY_RED_KEY, "1") — pause flag active
        GREEN  → redis.delete(QUALITY_RED_KEY)   — pause cleared
        YELLOW → redis.delete(QUALITY_RED_KEY)   — pause cleared
                 (YELLOW throttles ramp cap instead; flag stays clear)
        Other  → no-op (unexpected values silently ignored)
    """
    if rating == "RED":
        redis_client.set(QUALITY_RED_KEY, "1")
    elif rating in ("GREEN", "YELLOW"):
        redis_client.delete(QUALITY_RED_KEY)
    # Other values: no-op (defensive — unexpected quality levels ignored)
