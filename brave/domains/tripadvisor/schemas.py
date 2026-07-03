"""Pydantic v2 schemas for the TripAdvisor lane (TA-02, TA-08).

Three schemas:
  - TripAdvisorReviewSignals    — LGPD-enforced aggregate review data (extra=forbid)
  - TripAdvisorDestinoPayload   — Full Nascente payload shape for a destination
  - TripAdvisorAtrativoPayload  — Full Nascente payload shape for an attraction

LGPD boundary: TripAdvisorReviewSignals uses model_config=ConfigDict(extra="forbid") to
reject any field not explicitly declared (author, text, reviewer_id, etc.). No review
author or review text ever enters Nascente payload (T-11-02-01, TA-08).

See CONTEXT.md TA-08 for compliance rationale.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# TripAdvisorReviewSignals — LGPD boundary schema (T-11-02-01, TA-08)
# ---------------------------------------------------------------------------


class TripAdvisorReviewSignals(BaseModel):
    """Aggregate review signals from TripAdvisor — LGPD enforcement point.

    Stores only aggregate fields: review_count, rating, most_recent_review_at.
    model_config=ConfigDict(extra="forbid") ensures no author, text, or
    reviewer_id field can ever enter this object (and thus the Nascente payload).

    This is the mandatory LGPD boundary per TA-08: we store aggregate review
    statistics only, never individual review content or author identity.
    """

    review_count: int = Field(default=0, ge=0, description="Total number of reviews")
    rating: float = Field(
        default=0.0, ge=0.0, le=5.0, description="Average rating (0.0–5.0)"
    )
    most_recent_review_at: datetime | None = Field(
        default=None,
        description="Datetime of the most recent review (UTC). None if no reviews.",
    )

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# TripAdvisorDestinoPayload — destino Nascente payload shape
# ---------------------------------------------------------------------------


class TripAdvisorDestinoPayload(BaseModel):
    """Nascente payload shape for a TripAdvisor destination.

    Validates raw TripAdvisor destination data before it enters store_raw.
    Enforces the LGPD boundary via review_signals: TripAdvisorReviewSignals.

    Fields match the payload dict expected by route_by_score via process_nascente_record.
    """

    name: str = Field(..., min_length=1, description="Destination name")
    uf: str = Field(..., min_length=2, max_length=2, description="UF code (2 letters)")
    location_id: str = Field(..., description="TripAdvisor locationId as string")
    lat: float | None = Field(default=None, description="Latitude")
    lng: float | None = Field(default=None, description="Longitude")
    review_signals: TripAdvisorReviewSignals = Field(
        default_factory=TripAdvisorReviewSignals,
        description="Aggregate review signals (LGPD-safe)",
    )
    # §7.6 scoring criterion values
    origem_value: float = Field(default=65.0, ge=0.0, le=100.0)
    completude_value: float = Field(default=0.0, ge=0.0, le=100.0)
    corroboracao_value: float = Field(default=0.0, ge=0.0, le=100.0)
    atualidade_value: float = Field(default=0.0, ge=0.0, le=100.0)
    validacao_humana_value: float = Field(default=0.0, ge=0.0, le=100.0)


# ---------------------------------------------------------------------------
# TripAdvisorAtrativoPayload — atrativo Nascente payload shape
# ---------------------------------------------------------------------------


class TripAdvisorAtrativoPayload(BaseModel):
    """Nascente payload shape for a TripAdvisor attraction.

    Extends the destino payload with parent destino linkage fields.
    parent_rio_id and parent_source_ref are set from the destino_rio_map built
    during the same sweep (TA-02 — parent resolution from same-sweep RioRecord).
    parent_mar_id is set only if that destino is already in Mar.
    """

    name: str = Field(..., min_length=1, description="Attraction name")
    uf: str = Field(..., min_length=2, max_length=2, description="UF code (2 letters)")
    location_id: str = Field(..., description="TripAdvisor locationId as string")
    lat: float | None = Field(default=None, description="Latitude")
    lng: float | None = Field(default=None, description="Longitude")
    review_signals: TripAdvisorReviewSignals = Field(
        default_factory=TripAdvisorReviewSignals,
        description="Aggregate review signals (LGPD-safe)",
    )
    # §7.6 scoring criterion values
    origem_value: float = Field(default=65.0, ge=0.0, le=100.0)
    completude_value: float = Field(default=0.0, ge=0.0, le=100.0)
    corroboracao_value: float = Field(default=0.0, ge=0.0, le=100.0)
    atualidade_value: float = Field(default=0.0, ge=0.0, le=100.0)
    validacao_humana_value: float = Field(default=0.0, ge=0.0, le=100.0)
    # Parent destino linkage (TA-02, TA-03)
    parent_rio_id: str | None = Field(
        default=None,
        description="UUID string of the parent destino RioRecord produced in the same sweep",
    )
    parent_source_ref: str | None = Field(
        default=None,
        description="source_ref of the parent destino (e.g. 'tripadvisor:destination:303506')",
    )
    parent_mar_id: str | None = Field(
        default=None,
        description="UUID string of the parent destino MarRecord, if already in Mar",
    )
