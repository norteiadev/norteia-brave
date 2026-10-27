"use client";

import { useCurrency } from "./currency";

export type CapLevel = "ok" | "warn" | "bad";

/** Same bands for every cost-guard cap: warn from 80%, bad at or past the cap. */
export function capLevel(used: number, cap: number): CapLevel {
  const ratio = cap > 0 ? used / cap : used > 0 ? 1 : 0;
  return ratio >= 1 ? "bad" : ratio >= 0.8 ? "warn" : "ok";
}

const FILL: Record<CapLevel, string> = {
  ok: "var(--rio)",
  warn: "var(--dlq)",
  bad: "var(--falha)",
};

/**
 * One spend-vs-cap row (POC .cost-row + .meter): label, the cap's config name as
 * a hint, "used / cap" in the operator's currency and a banded meter.
 */
export function CostMeter({
  testId,
  label,
  hint,
  used,
  cap,
}: {
  testId: string;
  label: string;
  hint: string;
  used: number;
  cap: number;
}) {
  const { format } = useCurrency();
  const level = capLevel(used, cap);
  const pct = cap > 0 ? Math.min(100, (used / cap) * 100) : used > 0 ? 100 : 0;
  return (
    <div data-testid={testId} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-[10px] gap-y-1">
      <span className="text-[12.5px] font-medium text-[var(--ink)]">
        {label}
        <small className="ml-1 text-[11.5px] font-normal text-[var(--ink-3)]">{hint}</small>
      </span>
      <span className="whitespace-nowrap font-mono text-[12px] font-semibold text-[var(--ink)]">
        {format(used)}{" "}
        <span className="font-normal text-[var(--ink-3)]">/ {format(cap)}</span>
      </span>
      <div
        role="meter"
        aria-label={`${label}: ${Math.round(pct)}% do teto`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        data-level={level}
        title={`${Math.round(pct)}% do teto`}
        className="col-span-2 h-[6px] overflow-hidden rounded-full bg-[var(--surface-2)]"
      >
        <i className="block h-full rounded-full" style={{ width: `${pct}%`, background: FILL[level] }} />
      </div>
    </div>
  );
}
