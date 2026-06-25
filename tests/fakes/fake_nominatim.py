"""Fake geocoder client for offline testing (TA-14).

FakeGeocoderClient implements GeocoderClientProtocol (structural typing, D-09).
Records all calls for assertion in unit tests and returns pre-configured
fixture data.

Usage:
    from tests.fakes.fake_nominatim import FakeGeocoderClient

    fake = FakeGeocoderClient(
        fixture_results={"312332": {"lat": -19.047, "lon": -43.426,
                                     "osm_id": 123, "municipio_name": "Conceição do Mato Dentro"}},
    )
    result = await fake.geocode("312332", "Cachoeira do Tabuleiro", "MG")
    assert fake.geocode_calls == [{"location_id": "312332", "name": "Cachoeira do Tabuleiro", "uf": "MG"}]
"""

from typing import Any

from brave.clients.base import GeocoderClientProtocol


class FakeGeocoderClient:
    """Fake geocoder that returns pre-configured fixture results.

    Structurally satisfies GeocoderClientProtocol (D-09).
    Records all calls for assertion in tests.
    Never makes network calls or writes to Redis.
    """

    def __init__(
        self,
        fixture_results: dict[str, dict[str, Any] | None] | None = None,
    ) -> None:
        """Initialize with optional fixture data.

        Args:
            fixture_results: Dict mapping location_id → geo dict or None.
                             Returned by geocode().
        """
        self._fixture_results = fixture_results or {}
        # Call recording list for test assertions (analog: fake_tripadvisor.py line 53-55)
        self.geocode_calls: list[dict[str, Any]] = []

    async def geocode(
        self, location_id: str, name: str, uf: str
    ) -> dict[str, Any] | None:
        """Return fixture result for the given location_id.

        Args:
            location_id: TripAdvisor attraction location id.
            name:        Attraction name (recorded, not used for lookup).
            uf:          Two-letter state code (recorded, not used for lookup).

        Returns:
            Fixture geo dict if location_id present, None otherwise.
        """
        self.geocode_calls.append({"location_id": location_id, "name": name, "uf": uf})
        return self._fixture_results.get(location_id)


# Structural type check (analog: fake_tripadvisor.py lines 97-100)
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: GeocoderClientProtocol = FakeGeocoderClient()  # noqa: F841
