"""Canonical DTO surface for the ``tripadvisor`` domain (Phase G).

Re-exports the TripAdvisor payload schemas (the real definitions live in
``brave.domains.tripadvisor.schemas`` — kept at that module name because the
``brave.lanes.tripadvisor.schemas`` re-export shim aliases onto it).
"""

from __future__ import annotations

from brave.domains.tripadvisor.schemas import (
    TripAdvisorAtrativoPayload,
    TripAdvisorDestinoPayload,
    TripAdvisorReviewSignals,
)

__all__ = [
    "TripAdvisorAtrativoPayload",
    "TripAdvisorDestinoPayload",
    "TripAdvisorReviewSignals",
]
