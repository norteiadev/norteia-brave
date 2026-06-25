"""In-package offline Geocoder stub (production-safe, TA-14).

Used when AppConfig.run_real_externals is False (CI default).
Returns None (no match) so callers fall through to quarantine without network I/O.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree. Tests use tests/fakes/FakeGeocoderClient for call-recording assertions.

Security note (T-14-05): NullGeocoderClient never makes HTTP requests —
the network boundary is never crossed from this path.
"""

from __future__ import annotations

from typing import Any


class NullGeocoderClient:
    """No-network geocoder stub (structural protocol match).

    Returns None for geocode() — no httpx call, no Redis write, no rate limit.
    Safe to use when RUN_REAL_EXTERNALS is unset/false.
    """

    async def geocode(
        self, location_id: str, name: str, uf: str
    ) -> dict[str, Any] | None:
        """Return None — offline stub performs no geocoding.

        Args:
            location_id: TripAdvisor location id (ignored).
            name:        Attraction name (ignored).
            uf:          Two-letter state code (ignored).

        Returns:
            None (no match sentinel — caller quarantines if no other match).
        """
        return None


# Structural type check (analog: null_tripadvisor.py lines 65-70)
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import GeocoderClientProtocol

    _c: GeocoderClientProtocol = NullGeocoderClient()  # noqa: F841
