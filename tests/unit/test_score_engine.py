"""Tests for the §7.6 pure score engine.

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
        # Mixed → DLQ: 30+16+12+6+0 = 64.0
        (100, 80, 60, 40, 0, 64.0, "dlq"),
        # Low values → descarte: 12+4+0+0+0 = 16.0
        (40, 20, 0, 0, 0, 16.0, "descarte"),
        # Boundary: exactly 85.0 → mar
        (100, 100, 75, 100, 100, 85.0, "mar"),
        # Boundary: exactly 84.9 → dlq
        # weight_origem=30, weight_completude=20, weight_corroboracao=20,
        # weight_atualidade=15, weight_validacao_humana=15
        # Need total = 84.9
        # 100*30/100 + 100*20/100 + 74.5*20/100 + 100*15/100 + 100*15/100
        # = 30 + 20 + 14.9 + 15 + 15 = 94.9, too high
        # Use different approach: all 0 except atualidade=100 and orig=100
        # 30+0+0+15+0 = 45, too low
        # Let me compute: total = 84.9
        # origin=100 → 30, completude=100 → 20, corrobora=74.5 → 14.9, atual=100 → 15, valid=100 → 15
        # 30+20+14.9+15+15 = 94.9, nope
        # We need exactly 84.9: orig=100 (30), completude=74.5 (14.9), corrobora=100 (20), atual=100 (15), valid=33.33 (5.0)
        # 30+14.9+20+15+5.0 = 84.9 ✓
        (100, 74.5, 100, 100, 100 / 3, 84.9, "dlq"),
        # Boundary: exactly 51.0 → dlq
        # 100*30/100 + 70*20/100 + 0+0+0 = 30+14=44, not enough
        # 100*30/100 + 100*20/100 + 5*20/100 + 0 + 0 = 30+20+1=51 ✓
        (100, 100, 5, 0, 0, 51.0, "dlq"),
        # Boundary: exactly 50.9 → descarte
        # 100*30/100 + 100*20/100 + 4.5*20/100 + 0 + 0 = 30+20+0.9 = 50.9 ✓
        (100, 100, 4.5, 0, 0, 50.9, "descarte"),
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


def test_compute_score_low_returns_descarte():
    """Explicit test: low values → 16.0, routing='descarte'."""
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
    assert result.routing == "descarte"


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
    """Default score_version is 'v1.0'."""
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=50,
        completude_value=50,
        corroboracao_value=50,
        atualidade_value=50,
        validacao_humana_value=50,
    )
    result = compute_score(inp, config)
    assert result.score_version == "v1.0"


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
    """Score of exactly 84.9 routes to 'dlq'."""
    config = ScoreConfig(threshold_mar=85.0, threshold_dlq=51.0)
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
    """Score of exactly 51.0 routes to 'dlq'."""
    config = ScoreConfig(threshold_dlq=51.0)
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


def test_boundary_just_below_51_is_descarte():
    """Score of exactly 50.9 routes to 'descarte'."""
    config = ScoreConfig(threshold_dlq=51.0)
    # 100*30/100 + 100*20/100 + 4.5*20/100 = 30 + 20 + 0.9 = 50.9
    inp = ScoreInput(
        origem_value=100,
        completude_value=100,
        corroboracao_value=4.5,
        atualidade_value=0,
        validacao_humana_value=0,
    )
    result = compute_score(inp, config)
    assert result.score == pytest.approx(50.9, abs=0.05)
    assert result.routing == "descarte"


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
