"""Tests for the §7.6 score distribution simulation harness.

All tests run fully offline.
"""

import pytest

from brave.config.settings import ScoreConfig
from brave.core.score.simulation import generate_cold_start_samples, simulate_distribution


def test_simulate_distribution_keys():
    """simulate_distribution returns dict with required keys."""
    config = ScoreConfig()
    samples = generate_cold_start_samples(10)
    result = simulate_distribution(config, samples)
    assert "total" in result
    assert "mar_pct" in result
    assert "dlq_pct" in result
    assert "descarte_pct" in result
    assert "mean" in result
    assert "stdev" in result


def test_simulate_distribution_totals_100_pct():
    """mar_pct + dlq_pct + descarte_pct == 100.0 (within float rounding tolerance)."""
    config = ScoreConfig()
    samples = generate_cold_start_samples(50)
    result = simulate_distribution(config, samples)
    total_pct = result["mar_pct"] + result["dlq_pct"] + result["descarte_pct"]
    assert total_pct == pytest.approx(100.0, abs=0.01)


def test_simulate_distribution_total_count():
    """result['total'] equals the number of samples passed in."""
    config = ScoreConfig()
    samples = generate_cold_start_samples(100)
    result = simulate_distribution(config, samples)
    assert result["total"] == 100


def test_cold_start_dlq_dominates():
    """Cold-start samples (validacao_humana=0) should show dlq_pct > 0.

    Cold-start records have validacao_humana=0 and thin corroboracao,
    so they compress into the DLQ band. The harness is the DLQ landfill warning.
    """
    config = ScoreConfig()
    samples = generate_cold_start_samples(100, origem_value=40.0)
    result = simulate_distribution(config, samples)
    # DLQ landfill risk: significant fraction lands in review queue
    assert result["dlq_pct"] > 0, "Expected DLQ landfill effect for cold-start records"


def test_simulate_distribution_mean_and_stdev():
    """mean and stdev are valid floats for n >= 2 samples."""
    config = ScoreConfig()
    samples = generate_cold_start_samples(50)
    result = simulate_distribution(config, samples)
    assert isinstance(result["mean"], float)
    assert isinstance(result["stdev"], float)
    assert result["mean"] >= 0.0
    assert result["stdev"] >= 0.0


def test_simulate_distribution_single_sample_stdev_zero():
    """simulate_distribution with n=1 returns stdev=0.0 (no division error)."""
    from brave.core.score.schemas import ScoreInput

    config = ScoreConfig()
    samples = [
        ScoreInput(
            origem_value=50,
            completude_value=50,
            corroboracao_value=50,
            atualidade_value=50,
            validacao_humana_value=0,
        )
    ]
    result = simulate_distribution(config, samples)
    assert result["stdev"] == 0.0
    assert result["total"] == 1


def test_generate_cold_start_samples_length():
    """generate_cold_start_samples(n) returns exactly n ScoreInput items."""
    samples = generate_cold_start_samples(200)
    assert len(samples) == 200


def test_generate_cold_start_samples_validacao_is_zero():
    """Cold-start samples always have validacao_humana_value=0."""
    samples = generate_cold_start_samples(50)
    for s in samples:
        assert s.validacao_humana_value == 0.0


def test_generate_cold_start_samples_corroboracao_is_zero():
    """Cold-start samples always have corroboracao_value=0."""
    samples = generate_cold_start_samples(50)
    for s in samples:
        assert s.corroboracao_value == 0.0


def test_simulate_distribution_all_mar():
    """When all inputs score above threshold_mar, mar_pct=100."""
    from brave.core.score.schemas import ScoreInput

    config = ScoreConfig(threshold_mar=85.0)
    # All 100 → score=100.0 → mar
    samples = [
        ScoreInput(
            origem_value=100,
            completude_value=100,
            corroboracao_value=100,
            atualidade_value=100,
            validacao_humana_value=100,
        )
        for _ in range(10)
    ]
    result = simulate_distribution(config, samples)
    assert result["mar_pct"] == 100.0
    assert result["dlq_pct"] == 0.0
    assert result["descarte_pct"] == 0.0
