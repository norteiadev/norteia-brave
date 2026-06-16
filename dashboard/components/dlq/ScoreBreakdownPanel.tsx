"use client";

import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

/**
 * ScoreBreakdownPanel — the signature §7.6 explainability component (UI-SPEC D-05).
 *
 * Renders the five reliability criteria as labeled horizontal score bars, each
 * showing its weight, raw value (0–100), and weighted contribution, with the
 * TOTAL score as the Display-size readout. Per UI-SPEC the bars are quantitative
 * (one hue per bar, label-driven, never a rainbow): a neutral fill with a
 * green/amber/red THRESHOLD CAP by the raw value (green ≥85 / amber 51–84.9 /
 * red ≤50), mono numerals, tabular-nums.
 *
 * The §7.6 criteria + canonical weights (locked by UI-SPEC):
 *   origem 30% · completude 20% · corroboração 20% · atualidade 15% · validação-humana 15%
 *
 * `score_breakdown` from GET /api/v1/dlq/{id} is a loose dict (the score engine
 * controls the exact keys). We read each criterion best-effort by its canonical
 * key with PT/EN aliases, defaulting a missing criterion to 0 so the panel always
 * renders all five rows (explainability must be complete even on partial data).
 */

export interface ScoreBreakdown {
  [key: string]: unknown;
}

interface CriterionSpec {
  /** canonical §7.6 label rendered to the operator (PT-BR) */
  label: string;
  /** weight as a fraction (origem = 0.30) */
  weight: number;
  /** candidate keys in score_breakdown, most-specific first */
  keys: string[];
}

/** Locked §7.6 order + weights (UI-SPEC). */
const CRITERIA: CriterionSpec[] = [
  { label: "origem", weight: 0.3, keys: ["origem", "origin"] },
  { label: "completude", weight: 0.2, keys: ["completude", "completeness"] },
  {
    label: "corroboração",
    weight: 0.2,
    keys: ["corroboracao", "corroboração", "corroboration"],
  },
  { label: "atualidade", weight: 0.15, keys: ["atualidade", "recency"] },
  {
    label: "validação-humana",
    weight: 0.15,
    keys: ["validacao_humana", "validação-humana", "human_validation"],
  },
];

/** Pull a 0–100 raw value for a criterion from the loose breakdown dict. */
function rawValue(breakdown: ScoreBreakdown, keys: string[]): number {
  for (const key of keys) {
    const direct = breakdown[key];
    if (typeof direct === "number") return direct;
    // some engines nest { value, weighted } per criterion
    if (direct && typeof direct === "object") {
      const v = (direct as Record<string, unknown>).value;
      if (typeof v === "number") return v;
    }
    // common suffix form: origem_value
    const suffixed = breakdown[`${key}_value`];
    if (typeof suffixed === "number") return suffixed;
  }
  return 0;
}

/** UI-SPEC threshold cap: green ≥85, amber 51–84.9, red ≤50. */
function capColor(raw: number): string {
  if (raw >= 85) return "var(--status-mar)";
  if (raw > 50) return "var(--status-dlq)";
  return "var(--status-descarte)";
}

function totalCapClass(total: number): string {
  if (total >= 85) return "text-[var(--status-mar)]";
  if (total > 50) return "text-[var(--status-dlq)]";
  return "text-[var(--status-descarte)]";
}

export function ScoreBreakdownPanel({
  breakdown,
  score,
  className,
}: {
  breakdown: ScoreBreakdown | null | undefined;
  /** total score from the detail endpoint; recomputed from criteria if absent */
  score?: number | null;
  className?: string;
}) {
  const bd = breakdown ?? {};

  const rows = CRITERIA.map((c) => {
    const raw = rawValue(bd, c.keys);
    return {
      ...c,
      raw,
      weighted: raw * c.weight,
    };
  });

  const computedTotal = rows.reduce((acc, r) => acc + r.weighted, 0);
  const total = typeof score === "number" ? score : computedTotal;

  return (
    <section
      className={cn("flex flex-col gap-3", className)}
      aria-label="§7.6 Breakdown"
    >
      <header className="flex items-baseline justify-between">
        <h3 className="text-[20px] font-semibold leading-tight">
          §7.6 Breakdown
        </h3>
        <div className="text-right">
          <span
            className={cn(
              "font-mono text-[28px] font-semibold leading-none tabular-nums",
              totalCapClass(total),
            )}
            data-testid="score-total"
          >
            {total.toFixed(1)}
          </span>
          <span className="ml-1 font-mono text-[12px] text-muted-foreground">
            / 100
          </span>
        </div>
      </header>

      <ul className="flex flex-col gap-2">
        {rows.map((r) => {
          const color = capColor(r.raw);
          return (
            <li key={r.label} className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between gap-2">
                <Label className="text-[12px] font-semibold capitalize">
                  {r.label}
                  <span className="ml-1 font-mono text-[12px] font-normal text-muted-foreground tabular-nums">
                    {Math.round(r.weight * 100)}%
                  </span>
                </Label>
                <div className="flex items-center gap-2 font-mono text-[12px] tabular-nums">
                  <span>{r.raw.toFixed(0)}</span>
                  <span className="text-muted-foreground">
                    → {r.weighted.toFixed(1)}
                  </span>
                </div>
              </div>
              <div
                className="h-2 w-full overflow-hidden rounded-full bg-muted"
                role="progressbar"
                aria-label={r.label}
                aria-valuenow={Math.round(r.raw)}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.max(0, Math.min(100, r.raw))}%`,
                    backgroundColor: color,
                  }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
