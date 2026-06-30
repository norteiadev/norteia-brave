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

from collections.abc import AsyncIterator
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
        self, geo_id: int, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Return empty list — offline stub performs no scraping.

        Args:
            geo_id: TripAdvisor geoId (ignored).
            max_pages: Page cap (ignored).

        Returns:
            Empty list.
        """
        return []

    async def fetch_attractions_paginated(
        self, geo_id: int, start_page: int = 1, max_pages: int = 334
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        """Yield nothing — offline stub performs no scraping (T-11-01-03).

        Implemented as an async generator (the unreachable ``yield`` after ``return``
        makes this a generator function) so it structurally matches the protocol's
        async-iterator contract while launching no Playwright and crossing no network.

        Args:
            geo_id: TripAdvisor geoId (ignored).
            start_page: Resume page (ignored).
            max_pages: Page cap (ignored).

        Yields:
            Nothing — the iterator is empty.
        """
        return
        yield  # pragma: no cover  (unreachable; marks this an async generator)

    async def resolve_geo_id(self, uf: str) -> int:
        """Return 0 — offline stub does not resolve geoIds.

        Args:
            uf: Two-letter state code (ignored).

        Returns:
            0 (null sentinel; real client raises ValueError on unknown UF).
        """
        return 0

    async def fetch_attraction_detail(self, location_id: int) -> dict | None:
        """Return None — offline stub performs no detail lookup.

        Args:
            location_id: TripAdvisor locationId (ignored).

        Returns:
            None.
        """
        return None

    async def fetch_attraction_geo(self, location_id: int) -> dict | None:
        """Return None — offline stub performs no geo lookup.

        Args:
            location_id: TripAdvisor locationId (ignored).

        Returns:
            None.
        """
        return None


# Structural type check: NullTripAdvisorClient must satisfy TripAdvisorClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import TripAdvisorClientProtocol

    _client: TripAdvisorClientProtocol = NullTripAdvisorClient()  # noqa: F841
