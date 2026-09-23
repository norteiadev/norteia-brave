"""In-package offline NorteiaApiClient stub (production-safe).

Used when AppConfig.run_real_externals is False (local dev, the CLI fixture run,
and any environment without norteia-api credentials). It satisfies
NorteiaApiClientProtocol and sends nothing: ``push`` returns False, so the Mar row
is never stamped as pushed.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree (review finding CR-01): brave/tasks/pipeline.py and brave/cli.py select this
stub in offline mode. Tests still use tests/fakes/FakeNorteiaApiClient, which adds
call-recording for assertions; this stub deliberately stays dependency-free.
"""

from __future__ import annotations

from typing import Any


class NullNorteiaApiClient:
    """No-network NorteiaApiClient implementation (structural protocol match)."""

    async def push(self, entity_type: str, payload: dict[str, Any]) -> bool:
        """Send nothing and return False — the caller never stamps pushed_at, so the
        first real push (externals turned on later) is not a silent no-op."""
        return False
