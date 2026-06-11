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


def test_cold_start_landfill_effect():
    """Cold-start samples (validacao_humana=0, corroboracao=0) should show mar_pct == 0.

    Without human validation (15pts) and corroboração (20pts), cold-start records
    cannot reach the Mar threshold (≥85). This is the DLQ landfill warning:
    all cold-start records are trapped in descarte or DLQ, never reaching Mar.

    The harness makes this risk visible before wiring real intake.
    """
    config = ScoreConfig()
    samples = generate_cold_start_samples(100, origem_value=40.0)
    result = simulate_distribution(config, samples)
    # Cold-start landfill: no records reach Mar (no human validation + no corroboration)
    assert result["mar_pct"] == 0.0, (
        f"Cold-start records should not reach Mar, but {result['mar_pct']}% did. "
        "Validate thresholds with simulate_distribution before wiring intake."
    )
    # All records go to descarte or DLQ (not Mar)
    assert result["descarte_pct"] + result["dlq_pct"] == pytest.approx(100.0, abs=0.01)

    # To demonstrate DLQ landfill with Mtur-origin records (origem=100):
    # Mtur records (origem=100) are better quality but still can't reach Mar without
    # human validation. With origem=100 + completude=80 + atualidade=40:
    # score = 30 + 16 + 0 + 6 + 0 = 52 → DLQ
    from brave.core.score.schemas import ScoreInput
    mtur_samples = [
        ScoreInput(
            origem_value=100,
            completude_value=80,
            corroboracao_value=50,
            atualidade_value=60,
            validacao_humana_value=0,
        )
        for _ in range(10)
    ]
    mtur_result = simulate_distribution(config, mtur_samples)
    # 30 + 16 + 10 + 9 + 0 = 65 → DLQ
    assert mtur_result["dlq_pct"] > 0, (
        "Mtur cold-start records should land in DLQ (DLQ landfill effect visible)"
    )


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
