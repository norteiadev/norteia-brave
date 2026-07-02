"""Score distribution simulation harness (D-14).

Purpose: Detect DLQ landfill risk before wiring real intake.
Cold-start records (validacao_humana=0, thin corroboration) tend to
collapse below the Mar threshold and land in DLQ. Run this harness
first and treat threshold_mar as a tunable knob, not a fixed truth.

See PITFALLS §1 (DLQ Landfill at Cold Start) for the warning signs.
"""

import random
import statistics
from typing import Any

from brave.config.settings import ScoreConfig
from brave.core.score.engine import compute_score
from brave.core.score.schemas import ScoreInput


def simulate_distribution(config: ScoreConfig, samples: list[ScoreInput]) -> dict[str, Any]:
    """Compute score distribution statistics for a list of ScoreInput samples.

    Args:
        config:  ScoreConfig with weights and thresholds.
        samples: List of ScoreInput to score.

    Returns:
        Dict with keys:
          total      — number of samples
          mar_pct    — percentage routed to Mar (≥ threshold_mar)
          dlq_pct    — percentage routed to DLQ (< threshold_mar)
          mean       — mean score
          stdev      — standard deviation (0.0 if n < 2)
    """
    n = len(samples)
    if n == 0:
        return {
            "total": 0,
            "mar_pct": 0.0,
            "dlq_pct": 0.0,
            "mean": 0.0,
            "stdev": 0.0,
        }

    results = [compute_score(s, config) for s in samples]
    scores = [r.score for r in results]

    mar_count = sum(1 for r in results if r.routing == "mar")
    dlq_count = sum(1 for r in results if r.routing == "dlq")

    mean = statistics.mean(scores)
    stdev = statistics.stdev(scores) if n >= 2 else 0.0

    return {
        "total": n,
        "mar_pct": round(mar_count / n * 100.0, 2),
        "dlq_pct": round(dlq_count / n * 100.0, 2),
        "mean": round(mean, 2),
        "stdev": round(stdev, 2),
    }


def generate_cold_start_samples(n: int, origem_value: float = 40.0) -> list[ScoreInput]:
    """Generate n ScoreInput samples representative of cold-start conditions.

    Cold-start characteristics (see PITFALLS §1):
      - validacao_humana = 0 (no human validation yet)
      - corroboracao = 0 (no corroborating sources yet)
      - atualidade = 0–30 (some freshness data from ingestion metadata)
      - completude = 50–100 (varies from partial to complete records)
      - origem = caller-supplied (default 40 for LLM-generated source)

    With default origem=40 and completude 50-100, atualidade 0-30:
      - score = 40*30/100 + completude*20/100 + 0*20/100 + atualidade*15/100 + 0*15/100
      - score = 12 + 10-20 + 0-4.5 = ~22-36.5 → dlq (< 80)
    With origem=100 (Mtur-sourced), same ranges:
      - score = 30 + 10-20 + 0-4.5 = ~40-54.5 → dlq (< 80)

    The DLQ landfill risk is visible when using Mtur-origin (100) records:
    they cluster in the 40-54.5 band, all in DLQ —
    validacao_humana=0 prevents reaching Mar (needs +15 pts = 30 more pts impossible
    unless corroboracao and atualidade are both high).

    To demonstrate the landfill effect at the default origen=40, we use
    completude=80-100 and atualidade=50-80, showing that even well-described
    records without human validation and corroboration are trapped in DLQ.

    Args:
        n:            Number of samples to generate.
        origem_value: Origem criterion value (default 40 for LLM-sourced).
                      Use 100 for Mtur-sourced records.

    Returns:
        List of ScoreInput with cold-start characteristics.
    """
    rng = random.Random(42)  # Deterministic seed for reproducible histograms
    samples = []
    for _ in range(n):
        # Completude varies from 60-100 (most cold-start records are reasonably complete)
        completude = rng.uniform(60.0, 100.0)
        # Atualidade 0-50: some freshness signal from source timestamps
        atualidade = rng.uniform(0.0, 50.0)
        samples.append(
            ScoreInput(
                origem_value=origem_value,
                completude_value=completude,
                corroboracao_value=0.0,
                atualidade_value=atualidade,
                validacao_humana_value=0.0,
            )
        )
    return samples
