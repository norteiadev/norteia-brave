"""In-package offline PlacesClient stub (production-safe).

Used when AppConfig.run_real_externals is False (local dev, CI, any environment
without a Google Places API key). Returns empty results so producers no-op cleanly
without making any network call.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree. Tests use tests/fakes/FakePlacesClient for call-recording assertions.
"""

from __future__ import annotations

from typing import Any


class NullPlacesClient:
    """No-network PlacesClient stub (structural protocol match).

    Returns empty list for text_search and empty dict for place_details —
    no Google Maps API call, no network I/O.
    Safe to use when RUN_REAL_EXTERNALS is unset/false.
    """

    async def text_search(self, query: str, uf: str) -> list[dict[str, Any]]:
        """Return empty list — offline stub performs no search.

        Args:
            query: Text search string (ignored).
            uf: Two-letter state code (ignored).

        Returns:
            Empty list.
        """
        return []

    async def place_details(self, place_id: str) -> dict[str, Any]:
        """Return empty dict — offline stub fetches no details.

        Args:
            place_id: Google Places place_id (ignored).

        Returns:
            Empty dict.
        """
        return {}


# Structural type check: NullPlacesClient must satisfy PlacesClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import PlacesClientProtocol

    _client: PlacesClientProtocol = NullPlacesClient()  # noqa: F841
