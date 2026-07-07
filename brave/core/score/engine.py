"""§7.6 pure score engine (D-12).

PURE FUNCTION: compute_score has zero I/O.
This file must NEVER import SQLAlchemy, httpx, redis, aiohttp, or any I/O library.
Verified by grep check in CI: grep -n "sqlalchemy|httpx|redis|aiohttp|Session" this_file

The score formula weights five criteria (§7.6):
  origem:           30%
  completude:       20%
  corroboração:     20%
  atualidade:       15%
  validação humana: 15%
  ─────────────────────
  total:           100%

Routing is binary (configurable via ScoreConfig, D-14):
  ≥ threshold_mar  (default 80.0) → "mar"
  < threshold_mar                  → "dlq"
"""

from brave.config.settings import ScoreConfig
from brave.core.score.schemas import ScoreBreakdown, ScoreInput, ScoreResult


def compute_score(inp: ScoreInput, config: ScoreConfig) -> ScoreResult:
    """Compute a §7.6 reliability score for a normalized record.

    Pure function — no I/O, no side effects, no global state mutation.
    Can be called synchronously inside a Celery task or directly in a test
    without any fixtures.

    Args:
        inp:    Per-criterion input values (each 0–100).
        config: ScoreConfig with calibrated weights and thresholds.

    Returns:
        ScoreResult with score, routing, score_version, and breakdown.
    """
    # Compute per-criterion weighted points
    origem_pts = inp.origem_value * config.weight_origem / 100.0
    completude_pts = inp.completude_value * config.weight_completude / 100.0
    corroboracao_pts = inp.corroboracao_value * config.weight_corroboracao / 100.0
    atualidade_pts = inp.atualidade_value * config.weight_atualidade / 100.0
    validacao_humana_pts = inp.validacao_humana_value * config.weight_validacao_humana / 100.0

    total = (
        origem_pts
        + completude_pts
        + corroboracao_pts
        + atualidade_pts
        + validacao_humana_pts
    )

    # Round to 2 decimal places to avoid floating-point representation drift
    score = round(total, 2)

    # Binary routing by the single Mar threshold (D-02)
    routing = "mar" if score >= config.threshold_mar else "dlq"

    return ScoreResult(
        score=score,
        routing=routing,
        score_version=config.score_version,
        breakdown=ScoreBreakdown(
            origem=origem_pts,
            completude=completude_pts,
            corroboracao=corroboracao_pts,
            atualidade=atualidade_pts,
            validacao_humana=validacao_humana_pts,
        ),
    )
