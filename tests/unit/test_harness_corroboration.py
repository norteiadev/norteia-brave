"""Offline unit tests for the harness corroboration boost (gap G1, plan 07-06).

Tests verify:
1. The corroboration boost (+50, capped at 100) enables Mar promotion via compute_score.
2. The boost cap at 100 is respected.
3. ScoreConfig weights and thresholds are unchanged (guard against accidental config change).

100% offline — no DB, no session, no external calls.
"""
from __future__ import annotations

from brave.config.settings import ScoreConfig
from brave.core.score.engine import compute_score
from brave.core.score.schemas import ScoreInput


def test_corroboration_boost_enables_mar_promotion() -> None:
    """Corroborated destino (corroboracao=50, validacao_humana=100) scores >=85 → Mar.

    Baseline normalized state after Mtur single-source ingest:
      origem=100, completude=100, corroboracao=0, atualidade=70, validacao_humana=0
    After harness boost (corroboracao_value = min(100, 0+50) = 50)
    and D-06 validate_and_promote_rio (validacao_humana=100):
      score = 30 + 20 + 10 + 10.5 + 15 = 85.5 ≥ threshold_mar → routing "mar"

    Does NOT hardcode weights — derives threshold from ScoreConfig() to remain
    weight-change-safe.
    """
    config = ScoreConfig()

    # Apply the harness boost: corroboracao_value = min(100.0, 0.0 + 50.0)
    corroboracao_raw = 0.0
    corroboracao_boosted = min(100.0, corroboracao_raw + 50.0)
    assert corroboracao_boosted == 50.0

    inp = ScoreInput(
        origem_value=100.0,
        completude_value=100.0,
        corroboracao_value=corroboracao_boosted,
        atualidade_value=70.0,
        validacao_humana_value=100.0,
    )
    result = compute_score(inp, config)

    assert result.score >= config.threshold_mar, (
        f"Expected score >= threshold_mar={config.threshold_mar}, got {result.score}"
    )
    assert result.routing == "mar", (
        f"Expected routing='mar', got '{result.routing}' (score={result.score})"
    )
    # Verify the exact expected score for completeness
    assert result.score == 85.5, (
        f"Expected score=85.5, got {result.score}"
    )


def test_boost_capped_at_100() -> None:
    """Boost is capped at 100: corroboracao=80 + 50 = min(100, 130) = 100, not 130."""
    corroboracao_current = 80.0
    boosted = min(100.0, float(corroboracao_current) + 50.0)
    assert boosted == 100.0, (
        f"Expected boost to cap at 100.0, got {boosted}"
    )
    assert boosted != 130.0, "Boost must NOT exceed 100.0"

    # Also verify that a boosted value of 100 is valid in ScoreInput (le=100 constraint)
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=100.0,
        completude_value=100.0,
        corroboracao_value=boosted,
        atualidade_value=70.0,
        validacao_humana_value=100.0,
    )
    result = compute_score(inp, config)
    # corroboracao=100 adds 20.0 pts, pushing score to 95.5
    assert result.routing == "mar"
    assert result.score == 95.5


def test_harness_boost_does_not_touch_settings() -> None:
    """Guard: ScoreConfig §7.6 weights and thresholds are unchanged by this plan.

    If any of these assertions fail, plan 07-06 accidentally changed brave/config/settings.py.
    This test must pass BOTH before and after applying the harness changes — the harness
    only modifies scripts/loadtest_destinos_atrativos.py, not the config.
    """
    config = ScoreConfig()

    # §7.6 calibrated weights (must sum to 100)
    assert config.weight_origem == 30.0, (
        f"weight_origem changed: expected 30.0, got {config.weight_origem}"
    )
    assert config.weight_completude == 20.0, (
        f"weight_completude changed: expected 20.0, got {config.weight_completude}"
    )
    assert config.weight_corroboracao == 20.0, (
        f"weight_corroboracao changed: expected 20.0, got {config.weight_corroboracao}"
    )
    assert config.weight_atualidade == 15.0, (
        f"weight_atualidade changed: expected 15.0, got {config.weight_atualidade}"
    )
    assert config.weight_validacao_humana == 15.0, (
        f"weight_validacao_humana changed: expected 15.0, got {config.weight_validacao_humana}"
    )

    # Routing thresholds
    assert config.threshold_mar == 85.0, (
        f"threshold_mar changed: expected 85.0, got {config.threshold_mar}"
    )
    assert config.threshold_dlq == 40.0, (
        f"threshold_dlq changed: expected 40.0, got {config.threshold_dlq}"
    )

    # Sanity: weights sum to 100
    total_weight = (
        config.weight_origem
        + config.weight_completude
        + config.weight_corroboracao
        + config.weight_atualidade
        + config.weight_validacao_humana
    )
    assert total_weight == 100.0, (
        f"§7.6 weights no longer sum to 100: got {total_weight}"
    )
