"""Tests for the reliability pure score engine.

All tests run fully offline (no I/O, no DB, no Redis, no network).
"""

import pytest

from brave.config.settings import ScoreConfig
from brave.core.score.engine import compute_score
from brave.core.score.schemas import ScoreInput, ScoreResult


# ---------------------------------------------------------------------------
# Parametrized routing band tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origem,completude,corroboracao,atualidade,validacao_humana,expected_score,expected_routing",
    [
        # All 100 → Mar
        (100, 100, 100, 100, 100, 100.0, "mar"),
        # Mixed → DLQ: 30+16+12+6+0 = 64.0 (< 80)
        (100, 80, 60, 40, 0, 64.0, "dlq"),
        # Low values → DLQ (binary gate, no descarte band): 12+4+0+0+0 = 16.0
        (40, 20, 0, 0, 0, 16.0, "dlq"),
        # Above the gate: 30+20+20+15+0 = 85.0 ≥ 80 → mar
        (100, 100, 100, 100, 0, 85.0, "mar"),
        # Boundary: exactly 80.0 → mar (30+20+15+15+0)
        (100, 100, 75, 100, 0, 80.0, "mar"),
        # Boundary: exactly 79.9 → dlq (30+20+14.9+15+0)
        (100, 100, 74.5, 100, 0, 79.9, "dlq"),
        # Mid → DLQ: 30+20+1+0+0 = 51.0 (< 80)
        (100, 100, 5, 0, 0, 51.0, "dlq"),
    ],
)
def test_compute_score_routing(
    origem,
    completude,
    corroboracao,
    atualidade,
    validacao_humana,
    expected_score,
    expected_routing,
):
    """compute_score returns correct score and routing for each band."""
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=origem,
        completude_value=completude,
        corroboracao_value=corroboracao,
        atualidade_value=atualidade,
        validacao_humana_value=validacao_humana,
    )
    result = compute_score(inp, config)
    assert isinstance(result, ScoreResult)
    assert result.score == pytest.approx(expected_score, abs=0.05)
    assert result.routing == expected_routing


def test_compute_score_all_100_returns_mar():
    """Explicit test: all 100 → score=100.0, routing='mar'."""
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=100,
        completude_value=100,
        corroboracao_value=100,
        atualidade_value=100,
        validacao_humana_value=100,
    )
    result = compute_score(inp, config)
    assert result.score == 100.0
    assert result.routing == "mar"


def test_compute_score_mixed_returns_dlq():
    """Explicit test: mixed values → 64.0, routing='dlq'."""
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=100,
        completude_value=80,
        corroboracao_value=60,
        atualidade_value=40,
        validacao_humana_value=0,
    )
    result = compute_score(inp, config)
    # 30 + 16 + 12 + 6 + 0 = 64.0
    assert result.score == 64.0
    assert result.routing == "dlq"


def test_compute_score_low_returns_dlq():
    """Explicit test: low values → 16.0, routing='dlq' (binary gate, < 80)."""
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=40,
        completude_value=20,
        corroboracao_value=0,
        atualidade_value=0,
        validacao_humana_value=0,
    )
    result = compute_score(inp, config)
    # 12 + 4 + 0 + 0 + 0 = 16.0
    assert result.score == 16.0
    assert result.routing == "dlq"


# ---------------------------------------------------------------------------
# Score version stamp
# ---------------------------------------------------------------------------


def test_compute_score_carries_score_version():
    """ScoreResult.score_version matches config.score_version."""
    config = ScoreConfig(score_version="v2.0")
    inp = ScoreInput(
        origem_value=100,
        completude_value=100,
        corroboracao_value=100,
        atualidade_value=100,
        validacao_humana_value=100,
    )
    result = compute_score(inp, config)
    assert result.score_version == "v2.0"


def test_compute_score_default_version():
    """Default score_version is 'v1.1'."""
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=50,
        completude_value=50,
        corroboracao_value=50,
        atualidade_value=50,
        validacao_humana_value=50,
    )
    result = compute_score(inp, config)
    assert result.score_version == "v1.1"


# ---------------------------------------------------------------------------
# Purity test (no side effects)
# ---------------------------------------------------------------------------


def test_compute_score_is_pure():
    """Calling compute_score twice with identical inputs produces identical results."""
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=70,
        completude_value=60,
        corroboracao_value=50,
        atualidade_value=40,
        validacao_humana_value=30,
    )
    result1 = compute_score(inp, config)
    result2 = compute_score(inp, config)
    assert result1.score == result2.score
    assert result1.routing == result2.routing
    assert result1.score_version == result2.score_version
    assert result1.breakdown.origem == result2.breakdown.origem


# ---------------------------------------------------------------------------
# Threshold boundary cases (explicit)
# ---------------------------------------------------------------------------


def test_boundary_85_is_mar():
    """Score of exactly 85.0 routes to 'mar'."""
    config = ScoreConfig(threshold_mar=85.0)
    # 100*30/100 + 100*20/100 + 75*20/100 + 0 + 0 = 30+20+15+0+0 = 65, not enough
    # Let's use: 100*30/100 + 100*20/100 + 100*20/100 + 100*15/100 + 0*15/100
    # = 30 + 20 + 20 + 15 + 0 = 85
    inp = ScoreInput(
        origem_value=100,
        completude_value=100,
        corroboracao_value=100,
        atualidade_value=100,
        validacao_humana_value=0,
    )
    result = compute_score(inp, config)
    assert result.score == 85.0
    assert result.routing == "mar"


def test_boundary_just_below_85_is_dlq():
    """Score of exactly 84.9 routes to 'dlq' when threshold_mar is set to 85.0."""
    config = ScoreConfig(threshold_mar=85.0)
    # 30 + 20 + 20 + 15 - 0.1 = 84.9
    # origin=100 (30) + completude=100 (20) + corrobora=100 (20) + atual=99.33 (14.9) = 84.9
    inp = ScoreInput(
        origem_value=100,
        completude_value=100,
        corroboracao_value=100,
        atualidade_value=99.33,
        validacao_humana_value=0,
    )
    result = compute_score(inp, config)
    assert result.score == pytest.approx(84.9, abs=0.05)
    assert result.routing == "dlq"


def test_boundary_51_is_dlq():
    """Score of exactly 51.0 routes to 'dlq' (< threshold_mar=80)."""
    config = ScoreConfig()
    # 100*30/100 + 100*20/100 + 5*20/100 = 30 + 20 + 1 = 51
    inp = ScoreInput(
        origem_value=100,
        completude_value=100,
        corroboracao_value=5,
        atualidade_value=0,
        validacao_humana_value=0,
    )
    result = compute_score(inp, config)
    assert result.score == 51.0
    assert result.routing == "dlq"


def test_boundary_80_is_mar():
    """Score of exactly 80.0 (default threshold_mar) routes to 'mar'."""
    config = ScoreConfig()  # threshold_mar defaults to 80.0
    # 30 + 20 + 15 + 15 + 0 = 80.0
    inp = ScoreInput(
        origem_value=100,
        completude_value=100,
        corroboracao_value=75,
        atualidade_value=100,
        validacao_humana_value=0,
    )
    result = compute_score(inp, config)
    assert result.score == 80.0
    assert result.routing == "mar"


def test_boundary_just_below_80_is_dlq():
    """Score of exactly 79.9 routes to 'dlq' (binary gate)."""
    config = ScoreConfig()  # threshold_mar defaults to 80.0
    # 30 + 20 + 14.9 + 15 + 0 = 79.9
    inp = ScoreInput(
        origem_value=100,
        completude_value=100,
        corroboracao_value=74.5,
        atualidade_value=100,
        validacao_humana_value=0,
    )
    result = compute_score(inp, config)
    assert result.score == pytest.approx(79.9, abs=0.05)
    assert result.routing == "dlq"


# ---------------------------------------------------------------------------
# Breakdown validation
# ---------------------------------------------------------------------------


def test_compute_score_breakdown_values():
    """ScoreResult.breakdown contains per-criterion point values."""
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=100,
        completude_value=80,
        corroboracao_value=60,
        atualidade_value=40,
        validacao_humana_value=0,
    )
    result = compute_score(inp, config)
    assert result.breakdown.origem == pytest.approx(30.0)
    assert result.breakdown.completude == pytest.approx(16.0)
    assert result.breakdown.corroboracao == pytest.approx(12.0)
    assert result.breakdown.atualidade == pytest.approx(6.0)
    assert result.breakdown.validacao_humana == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Phase 2 producer score boundary cases (TEST-02, D-06)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origem,completude,corroboracao,atualidade,validacao_humana,expected_routing",
    [
        # D-06 firewall: origem=40 + validacao=0 → NEVER Mar (max score = 67.0)
        # 12+20+20+15+0 = 67.0 → dlq (below threshold_mar=80)
        (40, 100, 100, 100, 0, "dlq"),
        # Mtur cold-start: origem=100, completude=70, atualidade=50
        # 30+14+0+7.5+0 = 51.5 → dlq (below threshold_mar=80)
        (100, 70, 0, 50, 0, "dlq"),
        # Mtur cold-start, thin completude/atualidade
        # 30+4+0+0+0 = 34.0 → dlq (below threshold_mar=80)
        (100, 20, 0, 0, 0, "dlq"),
        # origem=80 source, full completude, atualidade=50 → DLQ
        # 24+20+0+7.5+0 = 51.5 → dlq (below threshold_mar=80)
        (80, 100, 0, 50, 0, "dlq"),
        # After human validation, Mtur + corroboração boost → Mar
        # 30+20+10+10.5+15 = 85.5 → mar (≥ threshold_mar=80)
        (100, 100, 50, 70, 100, "mar"),
        # After human validation, a perfect Mtur record reaches the gate exactly
        # even without corroboração: 30+20+0+15+15 = 80.0 → mar (== threshold_mar=80)
        (100, 100, 0, 100, 100, "mar"),
        # origem=40 firewall, good completude/atualidade → DLQ even post-validate
        # 12+20+0+10.5+15 = 57.5 → dlq (origin=40 firewall: max possible is 57.5 < 80)
        (40, 100, 0, 70, 100, "dlq"),
    ],
)
def test_producer_score_boundaries(
    origem,
    completude,
    corroboracao,
    atualidade,
    validacao_humana,
    expected_routing,
):
    """Producer score boundary cases for Phase 2 (D-06, TEST-02).

    All scores computed with the binary threshold_mar=80 gate (Phase B).

    Key invariants proven:
    - D-06 firewall: origem=40 + validacao_humana=0 can never reach Mar
      (max score with origin=40 and no human validation = 67.0 < threshold_mar=80)
    - Mtur cold-start records land in DLQ with adequate completude/atualidade
    - After human validation, a perfect Mtur record reaches Mar exactly at 80.0 even
      without corroboração; anything short of full completude/atualidade stays in DLQ
    - origem=40 records stay in DLQ even after human validation (origin=40 caps
      the score at 57.5 < 80)
    """
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=origem,
        completude_value=completude,
        corroboracao_value=corroboracao,
        atualidade_value=atualidade,
        validacao_humana_value=validacao_humana,
    )
    result = compute_score(inp, config)
    assert result.routing == expected_routing
