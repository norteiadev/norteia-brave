"""TripAdvisor session cookie write-back helper (260629-p2v).

Single source of truth for merging Set-Cookie response headers back into
brave:ta:session and sliding its TTL. Called by all three fetch transports
in client.py and by the ta_keepalive beat task.

Security notes:
  T-p2v-01: Only rotated_cookie_count (int) and error_type (class name) are
             ever emitted in log calls — cookie names and values are NEVER logged.
  Best-effort contract: persist_rotated_cookies NEVER raises. A Redis or parse
                        error is caught, logged at WARNING, and swallowed so the
                        triggering data fetch is not interrupted.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

# Imported at module level to avoid a circular-import cycle — client.py
# lazy-imports THIS module INSIDE method bodies (noqa: PLC0415 pattern).
# session.py → client.BRAVE_TA_SESSION_KEY is a one-way import; client.py
# never imports from session.py at the top level.
from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY  # noqa: E402


def persist_rotated_cookies(
    redis: Any,
    response_cookies: dict[str, str],
    ta_config: Any,
) -> None:
    """Merge Set-Cookie response headers back into brave:ta:session and slide its TTL.

    Called after every successful TA HTTP response (all 3 fetch transports +
    keep-alive beat). Best-effort / non-fatal: a Redis or parse failure is
    caught and logged; the triggering fetch continues normally.

    Algorithm:
      1. Bail immediately when response_cookies is empty (no-op, no Redis write).
      2. Read brave:ta:session from Redis; bail if absent (session gone).
      3. Normalise Phase-11 list-form cookies to flat dict (backwards compat).
      4. Merge: response_cookies wins on collision with stored cookies.
      5. Re-derive session["session_id"] from TASID when it appears in response.
      6. Write back with TTL = ta_config.session_ttl (sliding window reset).
      7. Log rotated_cookie_count only — never log cookie names or values (T-p2v-01).

    All of steps 2–7 are wrapped in a single try/except so any Redis or JSON
    parse error is swallowed (best-effort contract).

    Args:
        redis:            Sync Redis client (fakeredis-compatible).
        response_cookies: Dict of {name: value} parsed from the HTTP response
                          Set-Cookie headers (typically ``dict(resp.cookies)``).
        ta_config:        TripAdvisorConfig — only ``session_ttl`` is read.
    """
    # Step 1: empty response dict → nothing to merge (early exit, no Redis write)
    if not response_cookies:
        return

    try:
        # Step 2: read stored session; bail if absent
        raw = redis.get(BRAVE_TA_SESSION_KEY)
        if raw is None:
            return
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        session: dict = json.loads(raw)

        # Step 3: normalise Phase-11 list-form cookies → flat dict
        stored_cookies = session.get("cookies", {})
        if isinstance(stored_cookies, list):
            stored_cookies = {c["name"]: c["value"] for c in stored_cookies}

        # Step 4: merge — response wins on collision
        session["cookies"] = {**stored_cookies, **response_cookies}

        # Step 5: re-derive session_id from TASID when present
        if "TASID" in response_cookies:
            session["session_id"] = response_cookies["TASID"]

        # Step 6: write back with sliding TTL
        redis.setex(BRAVE_TA_SESSION_KEY, ta_config.session_ttl, json.dumps(session))

        # Step 7: log count only — T-p2v-01: never log cookie names or values
        logger.debug(
            "ta_session_writeback",
            rotated_cookie_count=len(response_cookies),
        )

    except Exception as exc:  # noqa: BLE001
        # Best-effort: log error type only (T-p2v-01: no cookie values in logs)
        logger.warning(
            "ta_session_writeback_error",
            error_type=type(exc).__name__,
        )
        # DO NOT reraise — the triggering data fetch must not be interrupted
