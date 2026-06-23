"""Tests for TripAdvisor §7.6 scoring helpers — calibration proof (TA-04).

Three mandatory scoring proof tests:
  1. typical → score in [66.5, 67.6] → routing=="dlq"
  2. sparse/no-review → score in [27.0, 28.0] → routing=="descarte"
  3. val=100 → score < 85 → routing!="mar" (proves promote-override is required)

Calibration spec: origin=65.0 (CONTEXT.md TA-04), completude=100.0 (well-documented
attraction), atualidade=70.0 (5 months ≤180d step), val=0.0 at ingest.
corroboracao_from_reviews(200, 4.5) uses log curve saturating at ~500 reviews.
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
        threshold_mar=85.0,
        threshold_dlq=40.0,
        score_version="v1.1",
        mar_ready_atualidade_bar=70.0,
        mar_ready_corrob_bar=60.0,
    )


class TestCorroboracaoFromReviews:
    """Unit tests for corroboracao_from_reviews pure function."""

    def test_zero_reviews_returns_zero(self) -> None:
        assert corroboracao_from_reviews(0, 0.0) == 0.0

    def test_saturates_at_500_reviews(self) -> None:
        v_500 = corroboracao_from_reviews(500, 5.0)
        v_1000 = corroboracao_from_reviews(1000, 5.0)
        # 500 reviews should be near or at saturation
        assert v_500 >= 90.0
        # 1000 reviews should not be drastically higher than 500
        assert v_1000 <= 100.0

    def test_value_in_range(self) -> None:
        v = corroboracao_from_reviews(200, 4.5)
        assert 0.0 <= v <= 100.0

    def test_higher_rating_higher_or_equal_corroboracao(self) -> None:
        low_rating = corroboracao_from_reviews(200, 3.0)
        high_rating = corroboracao_from_reviews(200, 5.0)
        assert high_rating >= low_rating


class TestAtualidadeFromRecency:
    """Unit tests for atualidade_from_recency step function."""

    def test_none_returns_zero(self) -> None:
        assert atualidade_from_recency(None) == 0.0

    def test_recent_30_days_returns_100(self) -> None:
        recent = datetime.now(timezone.utc) - timedelta(days=15)
        assert atualidade_from_recency(recent) == 100.0

    def test_exactly_30_days_returns_100(self) -> None:
        thirty_days = datetime.now(timezone.utc) - timedelta(days=30)
        assert atualidade_from_recency(thirty_days) == 100.0

    def test_5_months_returns_70(self) -> None:
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


class TestScoringProofTests:
    """Mandatory calibration proof tests (CONTEXT.md TA-04)."""

    def test_scoring_typical_routes_dlq(self) -> None:
        """Typical TA attraction: 200 reviews/4.5★/~5mo → score in [66.5, 67.6] → dlq.

        Calibration (CONTEXT.md TA-04):
          origem=65, completude=100, atualidade=70 (≤180d), val=0.
          corroboracao=corroboracao_from_reviews(200, 4.5) using log curve.
        """
        config = _default_config()
        corroboracao = corroboracao_from_reviews(200, 4.5)
        score_input = ScoreInput(
            origem_value=65.0,
            completude_value=100.0,  # well-documented attraction
            corroboracao_value=corroboracao,
            atualidade_value=70.0,  # 5 months ≤180d step
            validacao_humana_value=0.0,
        )
        result = compute_score(score_input, config)
        assert 66.5 <= result.score <= 67.6, (
            f"Expected score in [66.5, 67.6], got {result.score:.2f}. "
            f"corroboracao={corroboracao:.2f}"
        )
        assert result.routing == "dlq", f"Expected dlq, got {result.routing}"

    def test_scoring_sparse_routes_descarte(self) -> None:
        """Sparse TA record: no reviews, no recent data → score in [27.0, 28.0] → descarte.

        Calibration:
          origem=65, completude=25 (sparse fields), corroboracao=0, atualidade=0 (None), val=0.
        """
        config = _default_config()
        corroboracao = corroboracao_from_reviews(0, 0.0)
        atualidade = atualidade_from_recency(None)
        score_input = ScoreInput(
            origem_value=65.0,
            completude_value=25.0,  # only name+uf known
            corroboracao_value=corroboracao,
            atualidade_value=atualidade,
            validacao_humana_value=0.0,
        )
        result = compute_score(score_input, config)
        assert 27.0 <= result.score <= 28.0, (
            f"Expected score in [27.0, 28.0], got {result.score:.2f}"
        )
        assert result.routing == "descarte", f"Expected descarte, got {result.routing}"

    def test_scoring_val100_below_85(self) -> None:
        """val=100 + typical TA → score < 85 → routing != 'mar'.

        Proves that promote_override is REQUIRED to get TA attractions to Mar —
        standard validate_and_promote_rio (val=100) cannot cross the ≥85 gate.
        """
        config = _default_config()
        corroboracao = corroboracao_from_reviews(200, 4.5)
        score_input = ScoreInput(
            origem_value=65.0,
            completude_value=100.0,
            corroboracao_value=corroboracao,
            atualidade_value=70.0,
            validacao_humana_value=100.0,  # steward override
        )
        result = compute_score(score_input, config)
        assert result.score < 85.0, (
            f"Expected score < 85.0 (promote-override needed), got {result.score:.2f}"
        )
        assert result.routing != "mar", (
            f"Expected routing != 'mar', got {result.routing}"
        )
