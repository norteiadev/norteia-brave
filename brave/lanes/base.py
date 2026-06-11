"""Lane Protocol stub.

Defines the minimal interface that every production lane must implement.
Concrete lanes (Destinos, Atrativos) fill in Phase 2/3.

D-18: Lanes import core; core NEVER imports lanes.
"""

from typing import Protocol


class LaneProtocol(Protocol):
    """Minimal interface for a Brave data collection lane.

    A lane is responsible for ingesting raw data from a specific source
    (Mtur, NotebookLM, Google Places, Apify, etc.) into Nascente records.
    """

    async def produce(self, uf: str) -> None:
        """Ingest one full UF sweep for this lane.

        Implementors write raw payloads to Nascente via the NascenteService.
        Called by the Celery sweep_uf task for each UF in the fan-out.

        Args:
            uf: Two-letter Brazilian state code (e.g. "BA", "RJ", "SP").
        """
        ...
