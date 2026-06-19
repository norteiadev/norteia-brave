"""In-package offline ApifyClient stub (production-safe).

Used when AppConfig.run_real_externals is False (local dev, CI, any environment
without an Apify API key). Returns an empty dict so SignalAgent no-ops cleanly
without making any network call.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree. Tests use tests/fakes/FakeApifyClient for call-recording assertions.
"""

from __future__ import annotations

from typing import Any


class NullApifyClient:
    """No-network ApifyClient stub (structural protocol match).

    Returns empty dict for scrape_ig — no Apify actor run, no network I/O.
    Safe to use when RUN_REAL_EXTERNALS is unset/false.

    Apify is a best-effort signal (D-05); returning {} reproduces the same
    graceful-degradation path that FakeApifyClient exercises by default.
    """

    async def scrape_ig(self, handle: str) -> dict[str, Any]:
        """Return empty dict — offline stub performs no IG scrape.

        Args:
            handle: Instagram handle (ignored).

        Returns:
            Empty dict.
        """
        return {}


# Structural type check: NullApifyClient must satisfy ApifyClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import ApifyClientProtocol

    _client: ApifyClientProtocol = NullApifyClient()  # noqa: F841
