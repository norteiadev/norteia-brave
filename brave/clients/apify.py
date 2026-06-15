"""RealApifyClient — Apify IG/X scraping implementation.

Uses apify-client 3.0.x SDK.
Implements ApifyClientProtocol:
  - scrape_ig(handle) → dict

Best-effort: the caller (SignalAgent) handles all exceptions from scrape_ig.
This client uses tenacity for transient errors (rate limits, connection failures).

Guard: raises RuntimeError if AppConfig().run_real_externals is False.

D-05: Apify is best-effort and non-blocking — failure degrades corroboração
signal but never fails the record. This client doesn't need to be defensive;
the SignalAgent wraps the call in try/except.

Meta ToS note (D-05): Read-only signal only. No automated DM from this client.
Documented per CLAUDE.md compliance constraint.

Usage (production — only when run_real_externals=True):
    from brave.clients.apify import RealApifyClient
    client = RealApifyClient(api_key="...")
    data = await client.scrape_ig("@praiadobonito")
"""

from __future__ import annotations

from typing import Any

import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Apify Actor IDs for IG scraping
# ---------------------------------------------------------------------------

# Instagram scraper Actor — reads business profile metadata (best-effort, read-only)
APIFY_IG_ACTOR_ID = "apify/instagram-profile-scraper"


# ---------------------------------------------------------------------------
# RealApifyClient
# ---------------------------------------------------------------------------


class RealApifyClient:
    """Real Apify client for IG/X scraping using apify-client 3.0.x SDK.

    Guard: raises RuntimeError if AppConfig().run_real_externals is False.
    Best-effort: SignalAgent wraps calls in try/except for graceful degradation (D-05).

    D-05 / Meta ToS: Read-only signal; no automated DM from this client.

    Args:
        api_key: Apify API key (required when run_real_externals=True).
    """

    def __init__(self, api_key: str) -> None:
        from brave.config.settings import AppConfig

        if not AppConfig().run_real_externals:
            raise RuntimeError(
                "RealApifyClient: run_real_externals=False — "
                "use FakeApifyClient in default test suite. "
                "Set BRAVE_RUN_REAL_EXTERNALS=true to enable real API calls."
            )

        if not api_key:
            raise RuntimeError(
                "RealApifyClient: api_key is empty — "
                "set BRAVE_APIFY_API_KEY environment variable."
            )

        self._api_key = api_key

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def scrape_ig(self, handle: str) -> dict[str, Any]:
        """Scrape Instagram business profile for activity signals.

        Uses Apify's instagram-profile-scraper Actor to fetch business
        metadata: follower count, last post date, post frequency.

        Best-effort: SignalAgent catches any exception from this method.
        tenacity retries transient errors up to 3 times.

        Args:
            handle: IG handle (e.g. "@praiadobonito" or "praiadobonito").

        Returns:
            Dict with profile data: {"followers": int, "last_post": str, ...}.
            Returns empty dict if profile not found or not a business account.

        Raises:
            Exception: On persistent failures (after 3 retries). SignalAgent
                       catches these and degrades corroboração signal to 0.
        """
        try:
            from apify_client import ApifyClient  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "apify-client not installed. "
                "Add apify-client>=3.0.0 to pyproject.toml."
            ) from exc

        # Strip leading @ if present
        clean_handle = handle.lstrip("@")

        client = ApifyClient(self._api_key)

        # Run the IG profile scraper Actor
        run = client.actor(APIFY_IG_ACTOR_ID).call(
            run_input={
                "usernames": [clean_handle],
                "resultsLimit": 1,
            },
            timeout_secs=60,
        )

        if run is None or run.get("status") != "SUCCEEDED":
            logger.warning(
                "apify_ig_scrape_failed",
                handle=handle,
                status=run.get("status") if run else "None",
            )
            return {}

        # Fetch results from the Actor's default dataset
        items = list(
            client.dataset(run["defaultDatasetId"]).iterate_items(limit=1)
        )

        if not items:
            return {}

        profile = items[0]

        result = {
            "handle": handle,
            "followers": profile.get("followersCount", 0),
            "following": profile.get("followsCount", 0),
            "posts_count": profile.get("postsCount", 0),
            "last_post": profile.get("latestIgtvVideoDate") or profile.get("latestPostDate"),
            "is_business": profile.get("isBusinessAccount", False),
            "bio": (profile.get("biography") or "")[:200],  # Truncate for storage
        }

        logger.info(
            "apify_ig_scrape_ok",
            handle=handle,
            followers=result["followers"],
        )
        return result


# ---------------------------------------------------------------------------
# Protocol compliance check
# ---------------------------------------------------------------------------


def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime).

    RealApifyClient raises RuntimeError if run_real_externals=False,
    so we cannot instantiate it here. The structural check is verified by
    the type annotations on scrape_ig matching ApifyClientProtocol.
    """
    pass
