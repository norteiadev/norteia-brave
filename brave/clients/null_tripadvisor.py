"""In-package offline TripAdvisorClient stub (production-safe, TA-01).

Used when AppConfig.run_real_externals is False (local dev, CI, any environment
without the scraper optional dep group installed). Returns empty results so
producers no-op cleanly without any Playwright or network I/O.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree. Tests use tests/fakes/FakeTripAdvisorClient for call-recording assertions.

Security note (T-11-01-03): NullTripAdvisorClient never imports Playwright —
the scraper dep group is optional and never reachable from the API path.
"""

from __future__ import annotations

from typing import Any


class NullTripAdvisorClient:
    """No-network TripAdvisor stub (structural protocol match).

    Returns empty list for fetch_destinations/fetch_attractions and 0 for
    resolve_geo_id — no Playwright launch, no GraphQL call, no network I/O.
    Safe to use when RUN_REAL_EXTERNALS is unset/false or the scraper dep
    group is not installed.
    """

    async def fetch_destinations(self, uf: str) -> list[dict[str, Any]]:
        """Return empty list — offline stub performs no scraping.

        Args:
            uf: Two-letter state code (ignored).

        Returns:
            Empty list.
        """
        return []

    async def fetch_attractions(
        self, geo_id: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return empty list — offline stub performs no scraping.

        Args:
            geo_id: TripAdvisor geoId (ignored).
            offset: Pagination offset (ignored).

        Returns:
            Empty list.
        """
        return []

    async def resolve_geo_id(self, uf: str) -> int:
        """Return 0 — offline stub does not resolve geoIds.

        Args:
            uf: Two-letter state code (ignored).

        Returns:
            0 (null sentinel; real client raises ValueError on unknown UF).
        """
        return 0


# Structural type check: NullTripAdvisorClient must satisfy TripAdvisorClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import TripAdvisorClientProtocol

    _client: TripAdvisorClientProtocol = NullTripAdvisorClient()  # noqa: F841
