"""In-package offline Wikimedia stub (production-safe): finds nothing, downloads nothing.

The images producer refuses to run without run_real_externals, so this never writes
no_match markers in practice; it exists so clients_for stays total offline.
"""

from __future__ import annotations

from typing import Any


class NullWikimediaClient:
    """No-network Wikimedia stub (structural protocol match)."""

    async def items_around(self, lat: float, lon: float, km: float) -> list[Any]:
        return []

    async def items_by_name(self, name: str, lat: float, lon: float, km: float) -> list[Any]:
        return []

    async def municipio(self, ibge: str) -> Any | None:
        return None

    async def items_in_municipio(self, qid: str) -> list[Any]:
        return []

    async def depicts(self, qid: str) -> list[str]:
        return []

    async def category_files(self, category: str) -> list[str]:
        return []

    async def geosearch(self, lat: float, lon: float, radius_m: int) -> list[str]:
        return []

    async def image_info(self, titles: list[str]) -> dict[str, Any]:
        return {}

    async def download(self, f: Any) -> tuple[bytes, str]:
        raise RuntimeError("NullWikimediaClient.download: offline")


def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import WikimediaClientProtocol

    _c: WikimediaClientProtocol = NullWikimediaClient()  # noqa: F841
