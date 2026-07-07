"""Canonical service surface for the ``tripadvisor`` domain (Phase G).

Re-exports the two ingest producers so callers can depend on
``brave.domains.tripadvisor.services`` rather than the individual
``atrativos`` / ``destinos`` implementation submodules.
"""

from __future__ import annotations

from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest
from brave.domains.tripadvisor.destinos import TripAdvisorDestinosIngest

__all__ = ["TripAdvisorAtrativosIngest", "TripAdvisorDestinosIngest"]
