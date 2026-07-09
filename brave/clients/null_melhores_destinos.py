"""In-package offline Melhores Destinos stub (production-safe).

Used when AppConfig.run_real_externals is False (CI default). Both methods return
None (no match / no description) so the DescriptionEnrichmentAgent degrades to the
floor (keeps the synthetic ``posicionamento``) without any network I/O.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree. Tests use tests/fakes/FakeMelhoresDestinosClient for call-recording assertions.

Security note: NullMelhoresDestinosClient never makes HTTP requests — the network
boundary is never crossed from this path.
"""

from __future__ import annotations


class NullMelhoresDestinosClient:
    """No-network Melhores Destinos stub (structural protocol match).

    Returns None for both methods — no httpx call, no Redis write, no throttle.
    Safe to use when RUN_REAL_EXTERNALS is unset/false.
    """

    async def find_attraction_url(
        self, nome: str, municipio: str, uf: str
    ) -> str | None:
        """Return None — offline stub performs no matching (no network)."""
        return None

    async def fetch_description(self, url: str) -> str | None:
        """Return None — offline stub performs no fetching (no network)."""
        return None


# Structural type check (analog: null_nominatim.py)
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import MelhoresDestinosClientProtocol

    _c: MelhoresDestinosClientProtocol = NullMelhoresDestinosClient()  # noqa: F841
