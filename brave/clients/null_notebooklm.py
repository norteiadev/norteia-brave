"""In-package offline NotebookLMClient stub (production-safe, D-02).

Used when AppConfig.run_real_externals is False (local dev, CI, any environment
without data/notebooklm/ populated). Returns an empty report dict for every
municipality so producers continue without I/O.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree. Tests use tests/fakes/FakeNotebookLMClient for call-recording assertions.
"""

from __future__ import annotations

from typing import Any


class NullNotebookLMClient:
    """No-network NotebookLMClient stub (structural protocol match).

    Returns an empty dict for every municipality — no file I/O.
    """

    async def fetch_report(self, municipio: str) -> dict[str, Any]:
        """Return empty dict — offline stub always returns no report.

        Args:
            municipio: Municipality identifier (ignored).

        Returns:
            Empty dict.
        """
        return {}


# Structural type check: NullNotebookLMClient must satisfy NotebookLMClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import NotebookLMClientProtocol

    _client: NotebookLMClientProtocol = NullNotebookLMClient()  # noqa: F841
