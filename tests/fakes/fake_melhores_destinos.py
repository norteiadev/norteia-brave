"""Fake Melhores Destinos client for offline testing.

FakeMelhoresDestinosClient implements MelhoresDestinosClientProtocol (structural
typing). Records all calls for assertion and returns pre-configured fixture data.

Usage:
    from tests.fakes.fake_melhores_destinos import FakeMelhoresDestinosClient

    fake = FakeMelhoresDestinosClient(
        url_by_name={"Praia do Forte": "https://.../praia-do-forte-54-249-l.html"},
        description_by_url={"https://.../praia-do-forte-54-249-l.html": "Prosa editorial…"},
        place_by_url={"https://.../praia-do-forte-54-249-l.html": "Mata de São João"},
    )
    url = await fake.find_attraction_url("Praia do Forte", "Mata de São João", "BA")
    desc = await fake.fetch_description(url)
    place = await fake.fetch_breadcrumb_place(url)
"""

from typing import Any

from brave.clients.base import MelhoresDestinosClientProtocol


class FakeMelhoresDestinosClient:
    """Fake Melhores Destinos scraper — records calls, returns fixtures, no network.

    Structurally satisfies MelhoresDestinosClientProtocol. A name absent from
    ``url_by_name`` yields None (no match); a url absent from ``description_by_url``
    (or ``place_by_url``) yields None (no description / no breadcrumb <Place>) —
    exercising the graceful-degradation paths.
    """

    def __init__(
        self,
        url_by_name: dict[str, str | None] | None = None,
        description_by_url: dict[str, str | None] | None = None,
        place_by_url: dict[str, str | None] | None = None,
    ) -> None:
        self._url_by_name = url_by_name or {}
        self._description_by_url = description_by_url or {}
        self._place_by_url = place_by_url or {}
        self.find_calls: list[dict[str, Any]] = []
        self.fetch_calls: list[str] = []
        self.breadcrumb_calls: list[str] = []

    async def find_attraction_url(
        self, nome: str, municipio: str, uf: str
    ) -> str | None:
        self.find_calls.append({"nome": nome, "municipio": municipio, "uf": uf})
        return self._url_by_name.get(nome)

    async def fetch_description(self, url: str) -> str | None:
        self.fetch_calls.append(url)
        return self._description_by_url.get(url)

    async def fetch_breadcrumb_place(self, url: str) -> str | None:
        self.breadcrumb_calls.append(url)
        return self._place_by_url.get(url)


# Structural type check
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _c: MelhoresDestinosClientProtocol = FakeMelhoresDestinosClient()  # noqa: F841
