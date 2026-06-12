"""Fake NotebookLM client for offline testing.

FakeNotebookLMClient implements NotebookLMClientProtocol (structural typing, D-09).
Phase 2 — used in lane unit tests and integration tests.

Usage:
    from tests.fakes.fake_notebooklm import FakeNotebookLMClient

    fake = FakeNotebookLMClient(reports={
        "Porto Seguro:BA:2927408": {"name": "Porto Seguro", "highlights": [...]}
    })
    report = await fake.fetch_report("Porto Seguro:BA:2927408")
    assert fake.calls[0] == "Porto Seguro:BA:2927408"
    assert report["name"] == "Porto Seguro"

    # Missing report returns {}
    empty = await fake.fetch_report("Lençóis:BA:2919553")
    assert empty == {}
"""

from typing import Any

from brave.clients.base import NotebookLMClientProtocol


class FakeNotebookLMClient:
    """Fake NotebookLM client returning fixture reports keyed by municipio string.

    Structurally satisfies NotebookLMClientProtocol (D-09).
    Records every call to fetch_report for test assertions.
    Returns {} for any municipio not present in the reports dict.
    """

    def __init__(self, reports: dict[str, dict[str, Any]] | None = None) -> None:
        """Initialize with optional report fixture data.

        Args:
            reports: Dict mapping municipio string → report dict. If None,
                     defaults to an empty dict (all municipalities return {}).
        """
        self._reports = reports or {}
        self.calls: list[str] = []  # records each municipio string passed to fetch_report

    async def fetch_report(self, municipio: str) -> dict[str, Any]:
        """Return fixture report for the given municipio.

        Args:
            municipio: Municipality identifier (same format accepted by the real
                       NotebookLMClient, e.g. "Porto Seguro:BA:2927408").

        Returns:
            Report dict if municipio key matches, empty dict otherwise.
        """
        self.calls.append(municipio)
        return self._reports.get(municipio, {})


# Structural type check: FakeNotebookLMClient must satisfy NotebookLMClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: NotebookLMClientProtocol = FakeNotebookLMClient()  # noqa: F841
