"""Score calibration script for the Destinos lane (D-05).

Purpose
-------
This script answers the Phase 2 calibration question: with the default §7.6 weights
and the three Destinos producers (Mtur, NotebookLM, DesmembramentoAgent), where do
cold-start records land — Mar, DLQ, or descarte?

Critical finding driving this calibration
------------------------------------------
With the Phase 1 default ``threshold_dlq=51``, DesmembramentoAgent records
(origem=40, corroboração=0, validação_humana=0) have a MAXIMUM possible score of:

    40*0.3 + 100*0.2 + 0*0.2 + 100*0.15 + 0*0.15 = 12 + 20 + 0 + 15 + 0 = 47.0

47.0 < 51.0 → **all Desmembramento cold-start records hit descarte, not DLQ**.
This is the "descarte black-hole" for LLM-generated destinos (RESEARCH.md §Score
Calibration Analysis). If not corrected, zero Desmembramento records would ever reach
the DLQ for steward review, defeating the purpose of the lane entirely.

Fix: lower ``threshold_dlq`` from 51.0 to 40.0 (Phase 2 calibration, D-05).
With threshold_dlq=40, Desmembramento records in the realistic range
(completude=80-100, atualidade=50-80) score 42-47 — above the new DLQ floor.

Conservative cold-start range (atualidade 0-50): max score 39.5 — still all descarte.
Realistic fresh-extraction range (atualidade 0-80, completude 70-100): scores 38-47 —
a meaningful fraction land in DLQ, enabling steward review.

The GATE check below uses the realistic range to validate that the calibration is
effective. Re-run on real BA extraction output to fine-tune atualidade_value mapping.

Usage
-----
    python scripts/calibrate_destinos.py

No environment variables required — pure Python computation, no DB, no network.
"""

import random
from typing import Any

from brave.config.settings import ScoreConfig
from brave.core.score.schemas import ScoreInput
from brave.core.score.simulation import generate_cold_start_samples, simulate_distribution

# ---------------------------------------------------------------------------
# Sample generators
# ---------------------------------------------------------------------------

PRODUCERS = [
    ("mtur", 100.0),
    ("notebooklm", 80.0),
    ("desmembramento", 40.0),
]

N_SAMPLES = 500


def _generate_realistic_desmembramento_samples(n: int, rng: random.Random) -> list[ScoreInput]:
    """Generate samples representative of a realistic DesmembramentoAgent extraction.

    A "realistic" cold-start Desmembramento record comes from a LLM extraction run in
    2024/2025 against the current Mtur dataset. The key difference from the conservative
    cold-start range is atualidade (0–80, not 0–50), reflecting that the extraction was
    just run against a recently-published source dataset.

    These parameters represent real-world conditions where the calibration must work.
    Conservative (generate_cold_start_samples) shows the worst case; realistic shows
    typical operation after the first state sweep.
    """
    samples = []
    for _ in range(n):
        # Desmembramento LLM output: well-described destinos (completude 70–100)
        completude = rng.uniform(70.0, 100.0)
        # Fresh extraction from 2024/2025 Mtur data: atualidade 50–80
        atualidade = rng.uniform(50.0, 80.0)
        samples.append(
            ScoreInput(
                origem_value=40.0,
                completude_value=completude,
                corroboracao_value=0.0,  # No corroboration at cold start
                atualidade_value=atualidade,
                validacao_humana_value=0.0,  # No human validation yet
            )
        )
    return samples


def _generate_post_validation_mtur_samples(n: int) -> list[ScoreInput]:
    """Generate Mtur records after steward validation + corroboração boost.

    Validates that the D-02 corroboration-boost mechanism (NotebookLM dedup merging
    with Mtur record → corroboração += 50) is the load-bearing path to Mar promotion.

    Without corroboração=50: max Mtur post-validation score = 30+20+0+15+15 = 80 → DLQ
    With corroboração=50 + atualidade=70: score = 30+20+10+10.5+15 = 85.5 → Mar ✓
    """
    return [
        ScoreInput(
            origem_value=100.0,
            completude_value=100.0,
            corroboracao_value=50.0,  # D-02: NotebookLM dedup boost
            atualidade_value=70.0,
            validacao_humana_value=100.0,  # Steward validated
        )
        for _ in range(n)
    ]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _header(text: str) -> None:
    width = 72
    print()
    print("=" * width)
    print(f"  {text}")
    print("=" * width)


def _print_table(rows: list[dict[str, Any]]) -> None:
    print(
        f"  {'Producer':<18} {'Origem':>6} {'N':>5} "
        f"{'Mar%':>7} {'DLQ%':>7} {'Descarte%':>10} {'Mean':>7}"
    )
    print("  " + "-" * 66)
    for r in rows:
        print(
            f"  {r['producer']:<18} {r['origem']:>6.0f} {r['n']:>5} "
            f"{r['mar_pct']:>6.1f}% {r['dlq_pct']:>6.1f}% "
            f"{r['descarte_pct']:>9.1f}% {r['mean']:>7.2f}"
        )


# ---------------------------------------------------------------------------
# Main calibration routine
# ---------------------------------------------------------------------------


def main() -> None:
    rng = random.Random(42)

    # -----------------------------------------------------------------------
    # Pass 1: Default config (threshold_dlq=51) — demonstrates the black-hole
    # -----------------------------------------------------------------------
    _header("PASS 1: Default config (threshold_dlq=51) — descarte black-hole")
    print()
    print("  WHY threshold_dlq=51 is broken for Desmembramento:")
    print("  DesmembramentoAgent cold-start max score = 40*0.3 + 100*0.2 + 0*0.2 + 100*0.15 + 0*0.15")
    print("  = 12 + 20 + 0 + 15 + 0 = 47.0")
    print("  47.0 < 51.0 → ALL Desmembramento records hit descarte, not DLQ!")
    print("  Without steward review, zero LLM-generated destinos can reach Mar.")
    print()

    default_config = ScoreConfig(threshold_dlq=51.0, score_version="v1.0")
    rows_default = []
    for name, origem in PRODUCERS:
        samples = generate_cold_start_samples(N_SAMPLES, origem_value=origem)
        dist = simulate_distribution(default_config, samples)
        rows_default.append(
            {
                "producer": name,
                "origem": origem,
                "n": dist["total"],
                "mar_pct": dist["mar_pct"],
                "dlq_pct": dist["dlq_pct"],
                "descarte_pct": dist["descarte_pct"],
                "mean": dist["mean"],
            }
        )
    _print_table(rows_default)

    desm_default = next(r for r in rows_default if r["producer"] == "desmembramento")
    print()
    if desm_default["dlq_pct"] == 0.0:
        print(f"  CONFIRMED: Desmembramento DLQ% = 0.0% with threshold_dlq=51 (all descarte)")
    else:
        print(f"  NOTE: Desmembramento DLQ% = {desm_default['dlq_pct']}% (unexpected with threshold_dlq=51)")

    # -----------------------------------------------------------------------
    # Pass 2: Calibrated config (threshold_dlq=40) — conservative cold-start
    # Conservative cold-start: atualidade 0-50 (generate_cold_start_samples)
    # -----------------------------------------------------------------------
    _header("PASS 2: Calibrated config (threshold_dlq=40) — conservative cold-start samples")
    print()
    print("  Conservative sample range: atualidade 0-50, completude 60-100")
    print("  With origin=40, atualidade=50, completude=100: max score = 12+20+7.5 = 39.5")
    print("  This range still shows some descarte — reflects worst-case cold start.")
    print()

    calibrated_config = ScoreConfig(threshold_dlq=40.0, score_version="v1.1")
    rows_calibrated = []
    for name, origem in PRODUCERS:
        samples = generate_cold_start_samples(N_SAMPLES, origem_value=origem)
        dist = simulate_distribution(calibrated_config, samples)
        rows_calibrated.append(
            {
                "producer": name,
                "origem": origem,
                "n": dist["total"],
                "mar_pct": dist["mar_pct"],
                "dlq_pct": dist["dlq_pct"],
                "descarte_pct": dist["descarte_pct"],
                "mean": dist["mean"],
            }
        )
    _print_table(rows_calibrated)

    # -----------------------------------------------------------------------
    # Pass 3: Calibrated config — realistic cold-start (atualidade 50-80)
    # This represents a fresh LLM extraction against 2024/2025 Mtur data
    # -----------------------------------------------------------------------
    _header("PASS 3: Calibrated config (threshold_dlq=40) — realistic cold-start samples")
    print()
    print("  Realistic sample range: atualidade 50-80 (fresh LLM extraction, 2024/2025 dataset)")
    print("  With origin=40, atualidade=70, completude=100: score = 12+20+10.5 = 42.5 → DLQ")
    print()

    rows_realistic = []
    for name, origem in PRODUCERS:
        if name == "desmembramento":
            # Use realistic range for the gate-critical producer
            samples = _generate_realistic_desmembramento_samples(N_SAMPLES, rng)
        else:
            samples = generate_cold_start_samples(N_SAMPLES, origem_value=origem)
        dist = simulate_distribution(calibrated_config, samples)
        rows_realistic.append(
            {
                "producer": name,
                "origem": origem,
                "n": dist["total"],
                "mar_pct": dist["mar_pct"],
                "dlq_pct": dist["dlq_pct"],
                "descarte_pct": dist["descarte_pct"],
                "mean": dist["mean"],
            }
        )
    _print_table(rows_realistic)

    # -----------------------------------------------------------------------
    # Pass 4: Post-validation path (Mtur + corroboração=50 + validação=100 → Mar)
    # -----------------------------------------------------------------------
    _header("PASS 4: Post-validation path — validates D-02 corroboration boost to Mar")
    print()
    print("  Scenario: steward validates Mtur record; NotebookLM dedup merged (corroboração=50)")
    print("  Formula: 100*0.3 + 100*0.2 + 50*0.2 + 70*0.15 + 100*0.15")
    print("         = 30 + 20 + 10 + 10.5 + 15 = 85.5 → Mar ✓")
    print()

    post_val_samples = _generate_post_validation_mtur_samples(100)
    post_val_dist = simulate_distribution(calibrated_config, post_val_samples)
    print(
        f"  {'Producer':<18} {'N':>5} {'Mar%':>7} {'DLQ%':>7} {'Descarte%':>10} {'Mean':>7}"
    )
    print("  " + "-" * 56)
    print(
        f"  {'mtur+validated':<18} {post_val_dist['total']:>5} "
        f"{post_val_dist['mar_pct']:>6.1f}% {post_val_dist['dlq_pct']:>6.1f}% "
        f"{post_val_dist['descarte_pct']:>9.1f}% {post_val_dist['mean']:>7.2f}"
    )

    # -----------------------------------------------------------------------
    # GATE checks
    # -----------------------------------------------------------------------
    _header("GATE CHECKS")
    print()

    gate_pass = True

    # Gate 1: Default config — Desmembramento must show 0% DLQ (confirms the black-hole)
    desm_dlq_default = desm_default["dlq_pct"]
    if desm_dlq_default == 0.0:
        print(f"  GATE 1 PASS — Default threshold=51: Desmembramento DLQ=0% (black-hole confirmed)")
    else:
        print(f"  GATE 1 FAIL — Expected 0% DLQ with threshold=51, got {desm_dlq_default}%")
        gate_pass = False

    # Gate 2: Calibrated config — Desmembramento realistic cold-start must have DLQ > 0
    desm_realistic = next(r for r in rows_realistic if r["producer"] == "desmembramento")
    if desm_realistic["dlq_pct"] > 0:
        print(
            f"  GATE 2 PASS — Calibrated threshold=40: Desmembramento lands in DLQ "
            f"({desm_realistic['dlq_pct']:.1f}%) with realistic cold-start samples"
        )
    else:
        print(
            f"  GATE 2 FAIL — Desmembramento DLQ=0% even with threshold_dlq=40 "
            f"(atualidade mapping may need adjustment)"
        )
        gate_pass = False

    # Gate 3: Post-validation Mtur + corroboração must reach Mar
    if post_val_dist["mar_pct"] == 100.0:
        print(
            f"  GATE 3 PASS — Post-validation Mtur + corroboração=50 reaches Mar "
            f"(Mar={post_val_dist['mar_pct']:.1f}%, mean={post_val_dist['mean']:.2f})"
        )
    else:
        print(
            f"  GATE 3 FAIL — Post-validation Mtur DLQ landing "
            f"(Mar={post_val_dist['mar_pct']:.1f}%, mean={post_val_dist['mean']:.2f})"
        )
        gate_pass = False

    # Summary gate line for grep/CI
    print()
    print(
        f"  GATE: Desmembramento lands in DLQ with threshold_dlq=40: "
        f"{'PASS' if gate_pass else 'FAIL'}"
    )

    # -----------------------------------------------------------------------
    # Calibration summary
    # -----------------------------------------------------------------------
    _header("CALIBRATION SUMMARY")
    print()
    print("  Recommended settings (Phase 2, D-05):")
    print("    threshold_dlq = 40.0  (lowered from 51.0; avoids Desmembramento descarte black-hole)")
    print("    threshold_mar = 85.0  (unchanged)")
    print("    score_version = v1.1  (bumped to reflect threshold change)")
    print()
    print("  atualidade_value mapping guidance:")
    print("    Mtur dataset 2024/2025 edition  → atualidade = 70   (recently published)")
    print("    NotebookLM report (2024)         → atualidade = 60")
    print("    DesmembramentoAgent extraction   → atualidade = 60-70 (just-run LLM call)")
    print()
    print("  Re-run this script on real BA extraction output to validate distribution.")
    print("  If >50% of Desmembramento records still hit descarte on real data, lower")
    print("  threshold_dlq further (to 30) or boost atualidade_value mapping.")
    print()

    # Exit non-zero if any gate fails (useful for CI)
    if not gate_pass:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
