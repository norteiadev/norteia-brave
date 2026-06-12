"""Fake Mtur client for offline testing.

FakeMturClient implements MturClientProtocol (structural typing, D-09).
Phase 2 — used in lane unit tests and integration tests.

Usage:
    from tests.fakes.fake_mtur import FakeMturClient

    fake = FakeMturClient(fixtures=[
        {"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "Oferta Principal", "uf": "BA"}
    ])
    results = await fake.fetch_municipalities("BA")
    assert fake.calls[0] == "BA"
    assert results[0]["ibge_code"] == "2927408"
"""

from typing import Any

from brave.clients.base import MturClientProtocol


class FakeMturClient:
    """Fake Mtur client returning configurable municipality fixtures.

    Structurally satisfies MturClientProtocol (D-09).
    Records every call to fetch_municipalities for test assertions.
    Filters fixtures by uf so the caller gets realistic per-state behaviour.
    """

    def __init__(self, fixtures: list[dict[str, Any]] | None = None) -> None:
        """Initialize with optional municipality fixture data.

        Args:
            fixtures: List of municipality dicts matching the MturClient output
                      shape (ibge_code, name, categoria, uf). If None, defaults
                      to a single Porto Seguro BA fixture.
        """
        self._fixtures = fixtures or [
            {
                "ibge_code": "2927408",
                "name": "Porto Seguro",
                "categoria": "Oferta Principal",
                "uf": "BA",
            },
        ]
        self.calls: list[str] = []  # records each uf string passed to fetch_municipalities

    async def fetch_municipalities(self, uf: str) -> list[dict[str, Any]]:
        """Return fixture municipalities filtered by uf.

        Args:
            uf: Two-letter state code. Case-insensitive comparison against
                fixture "uf" field.

        Returns:
            Fixture municipalities whose "uf" matches the requested uf.
        """
        self.calls.append(uf)
        return [m for m in self._fixtures if m.get("uf", "").upper() == uf.upper()]


# Structural type check: FakeMturClient must satisfy MturClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: MturClientProtocol = FakeMturClient()  # noqa: F841
