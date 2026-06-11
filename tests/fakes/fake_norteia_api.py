"""Fake Norteia API client for offline testing.

FakeNorteiaApiClient implements NorteiaApiClientProtocol (structural typing, D-09).
Used in unit and integration tests to avoid real norteia-api calls.

Usage:
    from tests.fakes.fake_norteia_api import FakeNorteiaApiClient

    fake = FakeNorteiaApiClient()
    result = await fake.push_destination({"source_ref": "mtur:BA:1", ...})
    assert fake.push_destination_calls[0]["source_ref"] == "mtur:BA:1"
"""

from typing import Any
from uuid import uuid4

from brave.clients.base import NorteiaApiClientProtocol


class FakeNorteiaApiClient:
    """Fake Norteia API client that records calls and returns canned responses.

    Structurally satisfies NorteiaApiClientProtocol (D-09).
    Records every push_destination and push_attraction call for test assertions.
    Optionally fails to test error paths.
    """

    def __init__(self, should_fail: bool = False) -> None:
        """Initialize the fake client.

        Args:
            should_fail: If True, raise RuntimeError on any push call.
        """
        self._should_fail = should_fail
        self.push_destination_calls: list[dict[str, Any]] = []
        self.push_attraction_calls: list[dict[str, Any]] = []

    async def push_destination(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Record the call and return a canned destination push response.

        Args:
            payload: Mar push payload matching the Pact contract shape.

        Returns:
            Dict with {"id": <uuid>, "source_ref": <from payload>}.

        Raises:
            RuntimeError if should_fail=True.
        """
        if self._should_fail:
            raise RuntimeError("FakeNorteiaApiClient: simulated push failure")
        self.push_destination_calls.append(payload)
        return {
            "id": str(uuid4()),
            "source_ref": payload.get("source_ref", ""),
        }

    async def push_attraction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Record the call and return a canned attraction push response.

        Args:
            payload: Mar push payload matching the Pact contract shape.

        Returns:
            Dict with {"id": <uuid>, "source_ref": <from payload>}.

        Raises:
            RuntimeError if should_fail=True.
        """
        if self._should_fail:
            raise RuntimeError("FakeNorteiaApiClient: simulated push failure")
        self.push_attraction_calls.append(payload)
        return {
            "id": str(uuid4()),
            "source_ref": payload.get("source_ref", ""),
        }


# Structural type check: FakeNorteiaApiClient must satisfy NorteiaApiClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: NorteiaApiClientProtocol = FakeNorteiaApiClient()  # noqa: F841
