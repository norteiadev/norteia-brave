"""Fake Google Places client for offline testing.

FakePlacesClient implements PlacesClientProtocol (structural typing, D-09).
Phase 1 stub — real Places API integration deferred to Phase 3.

Usage:
    from tests.fakes.fake_places import FakePlacesClient

    fake = FakePlacesClient(
        fixture_results={"praias em Porto Seguro": [{"place_id": "abc", "name": "Praia de Trancoso"}]}
    )
    results = await fake.text_search("praias em Porto Seguro", uf="BA")
"""

from typing import Any

from brave.clients.base import PlacesClientProtocol


class FakePlacesClient:
    """Fake Google Places client that returns pre-configured fixture results.

    Structurally satisfies PlacesClientProtocol (D-09).
    Phase 1 stub: returns fixture data keyed by query string.
    Real implementation deferred to Phase 3 (Discovery/Signal agents).
    """

    def __init__(
        self,
        fixture_results: dict[str, list[dict[str, Any]]] | None = None,
        fixture_details: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Initialize with optional fixture data.

        Args:
            fixture_results: Dict mapping query string → list of place dicts.
                             Returned by text_search().
            fixture_details: Dict mapping place_id → place detail dict.
                             Returned by place_details().
        """
        self._fixture_results = fixture_results or {}
        self._fixture_details = fixture_details or {}
        self.text_search_calls: list[dict[str, Any]] = []
        self.place_details_calls: list[str] = []

    async def text_search(self, query: str, uf: str) -> list[dict[str, Any]]:
        """Return fixture results for the given query.

        Args:
            query: Text search string.
            uf:    Two-letter state code (recorded but not used by stub).

        Returns:
            Fixture results if query matches, empty list otherwise.
        """
        self.text_search_calls.append({"query": query, "uf": uf})
        return self._fixture_results.get(query, [])

    async def place_details(self, place_id: str) -> dict[str, Any]:
        """Return fixture details for the given place_id.

        Args:
            place_id: Google Places place_id.

        Returns:
            Fixture detail dict if place_id matches, empty dict otherwise.
        """
        self.place_details_calls.append(place_id)
        return self._fixture_details.get(place_id, {})


# Structural type check: FakePlacesClient must satisfy PlacesClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: PlacesClientProtocol = FakePlacesClient()  # noqa: F841
