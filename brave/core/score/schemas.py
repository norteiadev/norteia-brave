"""Pydantic schemas for the reliability score engine.

ScoreInput    — per-criterion input values (0-100 each)
ScoreBreakdown — per-criterion weighted point contributions
ScoreResult   — final score, routing, version, and breakdown

These schemas are intentionally import-free of I/O libraries (D-12).
"""

from typing import Literal

from pydantic import BaseModel, Field


class ScoreInput(BaseModel):
    """Per-criterion reliability score inputs.

    Each value is a 0–100 percentage representing how well the record
    satisfies that criterion. Validated at construction time.
    """

    origem_value: float = Field(..., ge=0.0, le=100.0, description="Origem criterion value (0–100)")
    completude_value: float = Field(..., ge=0.0, le=100.0, description="Completude criterion value (0–100)")
    corroboracao_value: float = Field(..., ge=0.0, le=100.0, description="Corroboração criterion value (0–100)")
    atualidade_value: float = Field(..., ge=0.0, le=100.0, description="Atualidade criterion value (0–100)")
    validacao_humana_value: float = Field(..., ge=0.0, le=100.0, description="Validação humana criterion value (0–100)")


class ScoreBreakdown(BaseModel):
    """Per-criterion weighted point contributions.

    Each field is the weighted points for that criterion:
        criterion_pts = criterion_value * weight / 100
    Sum of all fields equals the total reliability score.
    """

    origem: float = Field(..., description="Weighted origem points")
    completude: float = Field(..., description="Weighted completude points")
    corroboracao: float = Field(..., description="Weighted corroboração points")
    atualidade: float = Field(..., description="Weighted atualidade points")
    validacao_humana: float = Field(..., description="Weighted validação humana points")


class ScoreResult(BaseModel):
    """Result of the pure reliability score computation.

    score         — total reliability score (0–100, rounded to 2 decimal places)
    routing       — destination: "mar" (≥ threshold_mar) or "dlq" (below it).
                    Binary gate — the score engine never emits "descarte".
    score_version — identity stamp of the weight set used (D-13)
    breakdown     — per-criterion point contributions for provenance (D-13)
    """

    score: float = Field(..., description="Total reliability score (0–100)")
    routing: Literal["mar", "dlq"] = Field(
        ..., description="Routing destination based on the threshold_mar gate"
    )
    score_version: str = Field(..., description="Weight-set identity stamp (D-13)")
    breakdown: ScoreBreakdown = Field(..., description="Per-criterion point breakdown")
