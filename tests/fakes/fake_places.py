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


# ---------------------------------------------------------------------------
# Phase 3: Signal fixture constants for SignalAgent tests (D-05)
# ---------------------------------------------------------------------------

SIGNAL_FIXTURE_OPEN: dict[str, Any] = {
    "place_id": "ChIJtest001",
    "business_status": "OPERATIONAL",
    "weekday_text": [
        "Monday: 9:00 AM – 5:00 PM",
        "Tuesday: 9:00 AM – 5:00 PM",
        "Wednesday: 9:00 AM – 5:00 PM",
        "Thursday: 9:00 AM – 5:00 PM",
        "Friday: 9:00 AM – 5:00 PM",
        "Saturday: 10:00 AM – 3:00 PM",
        "Sunday: Closed",
    ],
    "reviews": [
        {
            "publishTime": "2026-06-01T12:00:00Z",
            "rating": 5,
            "text": "Ótimo lugar! Muito bonito e bem organizado.",
        }
    ],
}
"""Open-place fixture for SignalAgent tests.

business_status=OPERATIONAL + recent review (within 30 days of 2026-06-15).
Use to test the happy-path score path: atualidade_value=100, no descarte.
"""

SIGNAL_FIXTURE_CLOSED: dict[str, Any] = {
    "place_id": "ChIJtest002",
    "business_status": "CLOSED_PERMANENTLY",
    "weekday_text": [],
    "reviews": [],
}
"""Closed-place fixture for SignalAgent tests.

business_status=CLOSED_PERMANENTLY → hard descarte before §7.6 scoring (D-05).
Use to test that SignalAgent sets routing=descarte and sub_state=None
without calling route_by_score.
"""
