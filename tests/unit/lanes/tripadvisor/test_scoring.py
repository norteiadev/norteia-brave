"""Tests for TripAdvisor §7.6 scoring helpers — calibration proof (TA-04).

Three mandatory scoring proof tests (binary threshold_mar=80):
  1. typical → score in [66.5, 67.6] → routing=="dlq"
  2. sparse/no-review → score in [27.0, 28.0] → routing=="dlq"
  3. val=100 → score ≈ 82.05 ≥ 80 → routing=="mar" (a validated TA attraction reaches
     Mar directly under the binary gate — the old promote-override bypass is obsolete)

Calibration spec (CONTEXT.md TA-04):
  - origin=65.0 (TA source, firewall: TA never crosses the gate on origin alone)
  - typical: completude=100.0 (well-documented attraction), atualidade=70 (≤180d), val=0
  - sparse: completude=40.0 (only name+uf+locationId), atualidade=0 (no reviews), val=0
  - corroboracao_from_reviews uses log1p curve saturating at ~500 reviews

Score formula (§7.6 weights): origin×0.30 + completude×0.20 + corroboracao×0.20
                               + atualidade×0.15 + val×0.15

Typical: 65×0.30 + 100×0.20 + 85.25×0.20 + 70×0.15 + 0×0.15 ≈ 67.05 → dlq (✓ in [66.5, 67.6])
Sparse:  65×0.30 + 40×0.20 + 0×0.20 + 0×0.15 + 0×0.15 = 27.50 → dlq (✓ in [27.0, 28.0])
Val100:  65×0.30 + 100×0.20 + 85.25×0.20 + 70×0.15 + 100×0.15 ≈ 82.05 ≥ 80 → mar
"""

from datetime import datetime, timedelta, timezone

import pytest

from brave.config.settings import ScoreConfig
from brave.core.score.engine import compute_score
from brave.core.score.schemas import ScoreInput
from brave.lanes.tripadvisor.scoring import (
    atualidade_from_recency,
    completude_from_fields,
    corroboracao_from_reviews,
)


def _default_config() -> ScoreConfig:
    """Return default ScoreConfig without loading env vars."""
    return ScoreConfig(
        weight_origem=30.0,
        weight_completude=20.0,
        weight_corroboracao=20.0,
        weight_atualidade=15.0,
        weight_validacao_humana=15.0,
        threshold_mar=80.0,
        score_version="v1.1",
    )


class TestCorroboracaoFromReviews:
    """Unit tests for corroboracao_from_reviews pure function."""

    def test_zero_reviews_returns_zero(self) -> None:
        assert corroboracao_from_reviews(0, 0.0) == 0.0

    def test_saturates_near_100_at_500_reviews(self) -> None:
        v_500 = corroboracao_from_reviews(500, 5.0)
        v_1000 = corroboracao_from_reviews(1000, 5.0)
        # 500 reviews should be at/near saturation
        assert v_500 >= 90.0
        # 1000 reviews should not exceed 100
        assert v_1000 <= 100.0

    def test_value_in_range(self) -> None:
        v = corroboracao_from_reviews(200, 4.5)
        assert 0.0 <= v <= 100.0

    def test_higher_count_higher_corroboracao(self) -> None:
        low = corroboracao_from_reviews(50, 4.5)
        high = corroboracao_from_reviews(200, 4.5)
        assert high > low

    def test_200_reviews_yields_approx_85(self) -> None:
        """200 reviews should yield ~85 (log1p(200)/log1p(500)*100 ≈ 85.24)."""
        v = corroboracao_from_reviews(200, 4.5)
        # The value should be in the range that enables the 67.05 proof
        assert 80.0 <= v <= 90.0


class TestAtualidadeFromRecency:
    """Unit tests for atualidade_from_recency step function."""

    def test_none_returns_zero(self) -> None:
        assert atualidade_from_recency(None) == 0.0

    def test_recent_15_days_returns_100(self) -> None:
        recent = datetime.now(timezone.utc) - timedelta(days=15)
        assert atualidade_from_recency(recent) == 100.0

    def test_exactly_30_days_returns_100(self) -> None:
        thirty_days = datetime.now(timezone.utc) - timedelta(days=30)
        assert atualidade_from_recency(thirty_days) == 100.0

    def test_5_months_150_days_returns_70(self) -> None:
        """~150 days (≤180d) → 70."""
        five_months = datetime.now(timezone.utc) - timedelta(days=150)
        assert atualidade_from_recency(five_months) == 70.0

    def test_180_days_returns_70(self) -> None:
        six_months = datetime.now(timezone.utc) - timedelta(days=180)
        assert atualidade_from_recency(six_months) == 70.0

    def test_365_days_returns_40(self) -> None:
        one_year = datetime.now(timezone.utc) - timedelta(days=365)
        assert atualidade_from_recency(one_year) == 40.0

    def test_730_days_returns_20(self) -> None:
        two_years = datetime.now(timezone.utc) - timedelta(days=730)
        assert atualidade_from_recency(two_years) == 20.0

    def test_very_old_returns_zero(self) -> None:
        ancient = datetime.now(timezone.utc) - timedelta(days=1000)
        assert atualidade_from_recency(ancient) == 0.0


class TestCompletudFromFields:
    """Unit tests for completude_from_fields coverage calculator."""

    def test_empty_entity_returns_zero(self) -> None:
        assert completude_from_fields({}) == 0.0

    def test_all_fields_returns_100(self) -> None:
        entity = {
            "name": "Test Place",
            "uf": "SP",
            "location_id": "12345",
            "lat": -23.5,
            "lng": -46.6,
            "rating": 4.5,
            "review_count": 100,
            "address": "Rua Teste, 123",
            "category": "attraction",
            "description": "A beautiful place",
        }
        result = completude_from_fields(entity)
        assert result == 100.0

    def test_partial_fields_proportional(self) -> None:
        entity = {"name": "Test", "uf": "SP"}
        result = completude_from_fields(entity)
        assert 0.0 < result < 100.0

    def test_returns_float(self) -> None:
        assert isinstance(completude_from_fields({"name": "test"}), float)


class TestScoringProofTests:
    """Mandatory calibration proof tests (CONTEXT.md TA-04).

    These tests assert the §7.6 score formula produces values in the
    acceptance ranges under the binary threshold_mar=80 gate.

    Calibration math (for documentation):
      origin=65, weight_origin=30%  → 65×0.30 = 19.50
      completude=100, weight=20%    → 100×0.20 = 20.00  (typical)
      completude=40, weight=20%     → 40×0.20 = 8.00    (sparse)
      corroboracao≈85.25, weight=20% → 85.25×0.20 ≈ 17.05
      atualidade=70, weight=15%     → 70×0.15 = 10.50
      val=0, weight=15%             → 0×0.15 = 0
      → typical: 19.5+20+17.05+10.5+0 ≈ 67.05 → dlq
      → sparse:  19.5+8+0+0+0 = 27.50 → dlq
      → val=100: 19.5+20+17.05+10.5+15 ≈ 82.05 → mar (≥ 80)
    """

    def test_scoring_typical_routes_dlq(self) -> None:
        """Typical TA attraction: 200 reviews/4.5★/5mo → score in [66.5, 67.6] → dlq."""
        config = _default_config()
        corroboracao = corroboracao_from_reviews(200, 4.5)
        score_input = ScoreInput(
            origem_value=65.0,
            completude_value=100.0,  # well-documented attraction
            corroboracao_value=corroboracao,
            atualidade_value=70.0,   # 5 months ≤180d step
            validacao_humana_value=0.0,
        )
        result = compute_score(score_input, config)
        assert 66.5 <= result.score <= 67.6, (
            f"Expected score in [66.5, 67.6], got {result.score:.2f}. "
            f"corroboracao={corroboracao:.4f}"
        )
        assert result.routing == "dlq", f"Expected dlq, got {result.routing}"

    def test_scoring_sparse_routes_dlq(self) -> None:
        """Sparse TA record: no reviews, no recent data → score in [27.0, 28.0] → dlq."""
        config = _default_config()
        corroboracao = corroboracao_from_reviews(0, 0.0)
        atualidade = atualidade_from_recency(None)
        score_input = ScoreInput(
            origem_value=65.0,
            completude_value=40.0,   # name + uf + locationId only (40% coverage)
            corroboracao_value=corroboracao,
            atualidade_value=atualidade,
            validacao_humana_value=0.0,
        )
        result = compute_score(score_input, config)
        assert 27.0 <= result.score <= 28.0, (
            f"Expected score in [27.0, 28.0], got {result.score:.2f}"
        )
        assert result.routing == "dlq", f"Expected dlq, got {result.routing}"

    def test_scoring_val100_reaches_mar(self) -> None:
        """val=100 + typical TA → score ≈ 82.05 ≥ 80 → routing == 'mar'.

        Under the binary threshold_mar=80 gate a fully steward-validated TA
        attraction crosses into Mar directly through validate_and_promote_rio —
        the former mar_ready promote-override bypass is obsolete.
        """
        config = _default_config()
        corroboracao = corroboracao_from_reviews(200, 4.5)
        score_input = ScoreInput(
            origem_value=65.0,
            completude_value=100.0,
            corroboracao_value=corroboracao,
            atualidade_value=70.0,
            validacao_humana_value=100.0,  # full steward validation
        )
        result = compute_score(score_input, config)
        assert result.score >= 80.0, (
            f"Expected score >= 80.0 (crosses the binary gate), got {result.score:.2f}"
        )
        assert result.routing == "mar", (
            f"Expected routing == 'mar', got {result.routing}"
        )
