"""Fake Apify client for offline testing.

FakeApifyClient implements ApifyClientProtocol (structural typing, D-09).
Returns pre-configured fixture data or raises a configured exception.
Tracks all scrape_ig calls for assertion in tests.

Phase 3 scaffold — real ApifyClient deferred to brave/clients/apify.py (Phase 3).

Usage:
    from tests.fakes.fake_apify import FakeApifyClient

    client = FakeApifyClient(
        fixture_data={"@praiadobonito": {"followers": 1200, "last_post": "2026-06-01"}}
    )
    data = await client.scrape_ig("@praiadobonito")
    assert client.scrape_ig_calls == ["@praiadobonito"]
"""

from typing import Any

from brave.clients.base import ApifyClientProtocol


class FakeApifyClient:
    """Fake Apify client that returns pre-configured fixture results.

    Structurally satisfies ApifyClientProtocol (D-09).
    Apify is best-effort and non-blocking in production (D-05):
    configure raise_on_call to test graceful degradation paths.
    """

    def __init__(
        self,
        fixture_data: dict[str, dict[str, Any]] | None = None,
        raise_on_call: Exception | None = None,
    ) -> None:
        """Initialize with optional fixture data.

        Args:
            fixture_data: Dict mapping IG handle → data dict.
                          Returned by scrape_ig() when handle matches.
            raise_on_call: If set, scrape_ig() raises this exception instead of
                           returning fixture data. Use to test best-effort
                           graceful-degradation paths in SignalAgent.
        """
        self._fixture_data = fixture_data or {}
        self._raise_on_call = raise_on_call
        self.scrape_ig_calls: list[str] = []

    async def scrape_ig(self, handle: str) -> dict[str, Any]:
        """Return fixture data for the given IG handle.

        Args:
            handle: Instagram handle (e.g. "@praiadobonito").

        Returns:
            Fixture data dict if handle matches, empty dict otherwise.

        Raises:
            Exception: If raise_on_call was set at construction time.
        """
        self.scrape_ig_calls.append(handle)
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return self._fixture_data.get(handle, {})


# Structural type check: FakeApifyClient must satisfy ApifyClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: ApifyClientProtocol = FakeApifyClient()  # noqa: F841
