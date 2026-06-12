"""In-package offline MturClient stub (production-safe, D-01).

Used when AppConfig.run_real_externals is False (local dev, CI, any environment
without access to data/mtur/). Returns an empty municipality list so producers
no-op cleanly without raising FileNotFoundError.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree. Tests use tests/fakes/FakeMturClient for call-recording assertions.
"""

from __future__ import annotations

from typing import Any


class NullMturClient:
    """No-network MturClient stub (structural protocol match).

    Returns an empty list for every UF — no CSV read, no network I/O.
    Safe to use when data/mtur/ is not populated.
    """

    async def fetch_municipalities(self, uf: str) -> list[dict[str, Any]]:
        """Return empty list — offline stub always returns no municipalities.

        Args:
            uf: Two-letter state code (ignored).

        Returns:
            Empty list.
        """
        return []


# Structural type check: NullMturClient must satisfy MturClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import MturClientProtocol

    _client: MturClientProtocol = NullMturClient()  # noqa: F841
