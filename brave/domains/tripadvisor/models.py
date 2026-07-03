"""Domain models for the ``tripadvisor`` source (Phase G).

TripAdvisor persists through the shared kernel entities (``NascenteRecord`` /
``RioRecord`` / ``MarRecord``); its one domain-local value object is
``IbgeMunicipio`` (a resolved municipality row). Re-exported here for a stable
``brave.domains.tripadvisor.models`` surface.
"""

from __future__ import annotations

from brave.core.models import MarRecord, NascenteRecord, RioRecord
from brave.domains.tripadvisor.ibge import IbgeMunicipio

__all__ = ["IbgeMunicipio", "MarRecord", "NascenteRecord", "RioRecord"]
