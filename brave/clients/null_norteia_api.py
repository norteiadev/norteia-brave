"""In-package offline NorteiaApiClient stub (production-safe).

Used when AppConfig.run_real_externals is False (local dev, the CLI fixture run,
and any environment without norteia-api credentials). It satisfies
NorteiaApiClientProtocol and returns a synthetic 200-style response without any
network I/O.

This lives in brave/ (NOT tests/) so production code never imports from the test
tree (review finding CR-01): brave/tasks/pipeline.py and brave/cli.py select this
stub in offline mode. Tests still use tests/fakes/FakeNorteiaApiClient, which adds
call-recording for assertions; this stub deliberately stays dependency-free.
"""

from __future__ import annotations

import uuid
from typing import Any


class NullNorteiaApiClient:
    """No-network NorteiaApiClient implementation (structural protocol match)."""

    async def push_destination(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": str(uuid.uuid4()), "source_ref": payload.get("source_ref", "")}

    async def push_attraction(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": str(uuid.uuid4()), "source_ref": payload.get("source_ref", "")}
