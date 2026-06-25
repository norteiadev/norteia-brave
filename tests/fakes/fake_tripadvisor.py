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
    ) -> None:
        """Initialize with optional fixture data.

        Args:
            fixture_destinations: Dict mapping UF code → list of location dicts.
                                  Returned by fetch_destinations().
            fixture_attractions:  Dict mapping geoId → list of attraction dicts.
                                  Returned by fetch_attractions().
            geo_ids:              Dict mapping UF → geoId integer.
                                  Returned by resolve_geo_id().
        """
        self._fixture_destinations = fixture_destinations or {}
        self._fixture_attractions = fixture_attractions or {}
        self._geo_ids = geo_ids or {}

        # Call recording lists for test assertions
        self.destinations_calls: list[dict[str, Any]] = []
        self.attractions_calls: list[dict[str, Any]] = []
        self.resolve_calls: list[str] = []

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

    async def resolve_geo_id(self, uf: str) -> int:
        """Return configured geoId or 0 (offline default).

        Args:
            uf: Two-letter state code.

        Returns:
            Configured geoId if present, 0 otherwise.
        """
        self.resolve_calls.append(uf)
        return self._geo_ids.get(uf, 0)


# Structural type check: FakeTripAdvisorClient must satisfy TripAdvisorClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: TripAdvisorClientProtocol = FakeTripAdvisorClient()  # noqa: F841
