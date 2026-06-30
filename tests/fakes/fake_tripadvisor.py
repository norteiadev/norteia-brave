"""Fake TripAdvisor client for offline testing (TA-01).

FakeTripAdvisorClient implements TripAdvisorClientProtocol (structural typing, D-09).
Records all method calls for assertion in unit tests and returns pre-configured
fixture data.

Usage:
    from tests.fakes.fake_tripadvisor import FakeTripAdvisorClient

    fake = FakeTripAdvisorClient(
        fixture_destinations={"BA": [{"locationId": 12345, "name": "Salvador"}]},
        fixture_attractions={303513: [{"locationId": 99999, "name": "Elevador Lacerda"}]},
        geo_ids={"BA": 303513},
    )
    results = await fake.fetch_destinations(uf="BA")
    assert fake.destinations_calls == [{"uf": "BA"}]
"""

from collections.abc import AsyncIterator
from typing import Any

from brave.clients.base import TripAdvisorClientProtocol


class FakeTripAdvisorClient:
    """Fake TripAdvisor client that returns pre-configured fixture results.

    Structurally satisfies TripAdvisorClientProtocol (D-09).
    Records all calls for assertion in tests.
    Never imports Playwright or makes any network calls.
    """

    def __init__(
        self,
        fixture_destinations: dict[str, list[dict[str, Any]]] | None = None,
        fixture_attractions: dict[int, list[dict[str, Any]]] | None = None,
        geo_ids: dict[str, int] | None = None,
        fixture_pages: dict[int, list[tuple[int, list[dict[str, Any]]]]]
        | None = None,
        fixture_details: dict[int, dict[str, Any] | None] | None = None,
        fixture_geo: dict[int, dict[str, Any] | None] | None = None,
    ) -> None:
        """Initialize with optional fixture data.

        Args:
            fixture_destinations: Dict mapping UF code -> list of location dicts.
                                  Returned by fetch_destinations().
            fixture_attractions:  Dict mapping geoId -> list of attraction dicts.
                                  Returned by fetch_attractions().
            geo_ids:              Dict mapping UF -> geoId integer.
                                  Returned by resolve_geo_id().
            fixture_pages:        Dict mapping geoId -> list of (offset, cards) tuples.
                                  Yielded one tuple at a time by
                                  fetch_attractions_paginated().
            fixture_details:      Dict mapping locationId -> detail dict (or None).
                                  Returned by fetch_attraction_detail().
                                  Keys absent from the dict return None.
            fixture_geo:          Dict mapping locationId -> geo dict (or None).
                                  Returned by fetch_attraction_geo().
                                  Keys absent from the dict return None.
        """
        self._fixture_destinations = fixture_destinations or {}
        self._fixture_attractions = fixture_attractions or {}
        self._geo_ids = geo_ids or {}
        self._fixture_pages = fixture_pages or {}
        self._fixture_details: dict[int, dict[str, Any] | None] = fixture_details or {}
        self._fixture_geo: dict[int, dict[str, Any] | None] = fixture_geo or {}

        # Call recording lists for test assertions
        self.destinations_calls: list[dict[str, Any]] = []
        self.attractions_calls: list[dict[str, Any]] = []
        self.paginated_calls: list[dict[str, Any]] = []
        self.resolve_calls: list[str] = []
        self.detail_calls: list[int] = []
        self.geo_calls: list[int] = []

    async def fetch_destinations(self, uf: str) -> list[dict[str, Any]]:
        """Return fixture destinations for the given UF.

        Args:
            uf: Two-letter state code.

        Returns:
            Fixture destinations if UF matches, empty list otherwise.
        """
        self.destinations_calls.append({"uf": uf})
        return self._fixture_destinations.get(uf, [])

    async def fetch_attractions(
        self, geo_id: int, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Return fixture attractions for the given geoId.

        Args:
            geo_id: TripAdvisor geoId.
            max_pages: Page cap (recorded but ignored by stub).

        Returns:
            Fixture attractions if geoId matches, empty list otherwise.
        """
        self.attractions_calls.append({"geo_id": geo_id, "max_pages": max_pages})
        return self._fixture_attractions.get(geo_id, [])

    async def fetch_attractions_paginated(
        self, geo_id: int, start_page: int = 1, max_pages: int = 334
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        """Record the call and yield configured fixture pages for the given geoId.

        Mirrors fetch_attractions's call-recording posture, but streams one
        (offset, cards) tuple per configured page so tests can assert per-page
        ingest + commit + progress behaviour.

        Args:
            geo_id: TripAdvisor geoId.
            start_page: Resume page (recorded but does not slice the fixture).
            max_pages: Page cap (recorded but does not slice the fixture).

        Yields:
            (offset, cards) tuples from fixture_pages[geo_id]; nothing if absent.
        """
        self.paginated_calls.append(
            {"geo_id": geo_id, "start_page": start_page, "max_pages": max_pages}
        )
        for offset, cards in self._fixture_pages.get(geo_id, []):
            yield offset, cards

    async def resolve_geo_id(self, uf: str) -> int:
        """Return configured geoId or 0 (offline default).

        Args:
            uf: Two-letter state code.

        Returns:
            Configured geoId if present, 0 otherwise.
        """
        self.resolve_calls.append(uf)
        return self._geo_ids.get(uf, 0)

    async def fetch_attraction_detail(self, location_id: int) -> dict[str, Any] | None:
        """Return fixture detail for the given locationId, or None if absent.

        Records each call in detail_calls for test assertion.

        Args:
            location_id: TripAdvisor integer locationId.

        Returns:
            Fixture detail dict if locationId is in fixture_details, else None.
        """
        self.detail_calls.append(location_id)
        return self._fixture_details.get(location_id)

    async def fetch_attraction_geo(self, location_id: int) -> dict[str, Any] | None:
        """Record call and return fixture geo dict for locationId, or None if absent.

        Args:
            location_id: TripAdvisor integer locationId.

        Returns:
            Fixture geo dict if locationId is in fixture_geo, else None.
        """
        self.geo_calls.append(location_id)
        return self._fixture_geo.get(location_id)


# Structural type check: FakeTripAdvisorClient must satisfy TripAdvisorClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: TripAdvisorClientProtocol = FakeTripAdvisorClient()  # noqa: F841
