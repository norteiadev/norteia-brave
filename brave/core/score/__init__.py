"""§7.6 score engine — pure function, simulation harness, and schemas.

Exports:
  compute_score   — pure scoring function (zero I/O)
  ScoreInput      — per-criterion input values
  ScoreResult     — score + routing + version + breakdown
  ScoreBreakdown  — per-criterion point values
"""

from brave.core.score.engine import compute_score
from brave.core.score.schemas import ScoreBreakdown, ScoreInput, ScoreResult
from brave.core.score.simulation import generate_cold_start_samples, simulate_distribution

__all__ = [
    "compute_score",
    "ScoreInput",
    "ScoreResult",
    "ScoreBreakdown",
    "simulate_distribution",
    "generate_cold_start_samples",
]
