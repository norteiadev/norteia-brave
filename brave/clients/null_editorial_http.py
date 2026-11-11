"""In-package offline editorial fetcher (production-safe): every page "does not exist"."""

from __future__ import annotations


class NullEditorialFetcher:
    """No-network fetcher (structural match of EditorialFetcher): finds nothing."""

    async def get_text(self, url: str) -> str | None:
        return None
