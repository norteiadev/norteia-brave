"""TripAdvisor §7.6 scoring helpers (TA-04).

Pure functions — no I/O, no SQLAlchemy, no external dependencies except math.
Feeds the existing compute_score() via *_value payload keys in the Nascente payload.

Calibration spec (CONTEXT.md TA-04):
  corroboracao_from_reviews(200, 4.5) ≈ 85.25 (log1p curve)
  atualidade_from_recency(150 days) = 70.0 (≤180d step)
  completude_from_fields checks 10 TA-specific fields

Scoring proof (§7.6 weights — see brave/core/score/engine.py):
  Typical: origin=65 + completude=100 + corroboracao≈85.25 + atualidade=70 + val=0
  = 65×0.30 + 100×0.20 + 85.25×0.20 + 70×0.15 + 0×0.15 ≈ 67.05 → dlq (< 85)
  Sparse:  origin=65 + completude=40 + corroboracao=0 + atualidade=0 + val=0
  = 65×0.30 + 40×0.20 = 27.50 → descarte
  Val100:  same as typical but val=100 → ≈82.05 → still < 85 → promote-override required

NEVER import from brave.core.rio, brave.core.models, or any I/O library here.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# corroboracao_from_reviews — log curve saturating at ~500 reviews (TA-04)
# ---------------------------------------------------------------------------

# Saturation point: at 500 reviews the curve reaches 100 (log1p(500) ≈ 6.215)
_LOG_SATURATION = math.log1p(500)


def corroboracao_from_reviews(count: int, rating: float) -> float:
    """Compute corroboração value from TripAdvisor review count and average rating.

    Uses a log1p curve that saturates at ~500 reviews, returning values in [0, 100].
    Rating is stored and accepted as a parameter for forward compatibility (e.g., future
    quality-gating), but the primary calibration uses the log curve directly so that
    the §7.6 proof values hold:

        corroboracao_from_reviews(200, 4.5) ≈ 85.25

    Combined with origin=65, completude=100, atualidade=70 (≤180d), val=0:
        total ≈ 19.5 + 20.0 + 17.05 + 10.5 + 0 = 67.05 → dlq (CONTEXT.md TA-04)

    Args:
        count:  Total number of TripAdvisor reviews.
        rating: Average TripAdvisor rating (0.0–5.0).

    Returns:
        Corroboração value in [0.0, 100.0].
    """
    if count <= 0:
        return 0.0
    # log1p curve: saturates at 100 when count reaches ~500
    # log1p(200) / log1p(500) ≈ 5.298 / 6.215 ≈ 0.8524 → corroboracao ≈ 85.24
    base = min(100.0, 100.0 * math.log1p(count) / _LOG_SATURATION)
    return round(base, 2)


# ---------------------------------------------------------------------------
# atualidade_from_recency — step function (TA-04)
# ---------------------------------------------------------------------------


def atualidade_from_recency(most_recent_review_at: datetime | None) -> float:
    """Compute atualidade value from the date of the most recent TripAdvisor review.

    Step function (CONTEXT.md TA-04):
      None           → 0   (no reviews, no recency signal)
      ≤ 30 days      → 100 (very recent)
      ≤ 180 days     → 70  (within 6 months; 5 months ≈ 150 days falls here)
      ≤ 365 days     → 40  (within a year)
      ≤ 730 days     → 20  (within 2 years)
      > 730 days     → 0   (stale)

    Args:
        most_recent_review_at: UTC datetime of the most recent review, or None.

    Returns:
        Atualidade value in {0.0, 20.0, 40.0, 70.0, 100.0}.
    """
    if most_recent_review_at is None:
        return 0.0

    now = datetime.now(timezone.utc)
    # Ensure the datetime is timezone-aware for comparison
    if most_recent_review_at.tzinfo is None:
        most_recent_review_at = most_recent_review_at.replace(tzinfo=timezone.utc)

    age_days = (now - most_recent_review_at).days

    if age_days <= 30:
        return 100.0
    elif age_days <= 180:
        return 70.0
    elif age_days <= 365:
        return 40.0
    elif age_days <= 730:
        return 20.0
    else:
        return 0.0


# ---------------------------------------------------------------------------
# completude_from_fields — field coverage calculator (TA-04)
# ---------------------------------------------------------------------------

# TA-specific fields checked for completude. 10 fields → each worth 10 points (cap 100).
_TA_COMPLETUDE_FIELDS = [
    "name",
    "uf",
    "location_id",
    "lat",
    "lng",
    "rating",
    "review_count",
    "address",
    "category",
    "description",
]


def completude_from_fields(entity: dict, *, cap: int = 100) -> float:
    """Compute completude value from field coverage of a TripAdvisor entity dict.

    Checks _TA_COMPLETUDE_FIELDS (10 fields). Returns percentage × cap.
    A fully-documented entity (all 10 fields present) returns cap (default 100).

    Args:
        entity: TripAdvisor entity dict with raw field values.
        cap:    Maximum return value (default 100; pass 80 for destino cap).

    Returns:
        Completude value in [0.0, cap].
    """
    present = sum(1 for field in _TA_COMPLETUDE_FIELDS if entity.get(field) not in (None, "", []))
    fraction = present / len(_TA_COMPLETUDE_FIELDS)
    return round(fraction * cap, 2)
