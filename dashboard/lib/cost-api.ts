/**
 * Cost & LLM data layer (DASH-04, D-01).
 *
 * Query key + typed fetcher for the cost slice. The call goes through the BFF via
 * `apiFetch` (relative `/api/...`, operator Bearer attached) — never to FastAPI
 * directly. The endpoint is a read-only GROUP BY (no pipeline logic, no writes).
 *
 * Backing endpoint (brave/api/routers/dashboard.py):
 *   GET /api/v1/cost?group_by=lane|model&since= — aggregate llm_generations
 *   (sum usd_cost + token sums + call count grouped by lane or model_slug).
 *
 * The cost view drives two modes off the same endpoint: spend-per-lane
 * (group_by=lane) and spend-per-model (group_by=model), optionally windowed by a
 * `since` ISO timestamp on created_at.
 */

import { apiFetch } from "@/lib/api-client";

/** The aggregation dimension — lane (collection lane) or model (model_slug). */
export type CostGroupBy = "lane" | "model";

/** One aggregated cost row: a group key with summed spend / tokens / call count. */
export interface CostRow {
  /** The lane name or model_slug, depending on group_by. */
  key: string;
  /** Summed USD cost over the group (coerced from Numeric). */
  usd_cost: number;
  /** Summed prompt + completion tokens over the group. */
  tokens: number;
  /** Number of LLM calls in the group. */
  count: number;
}

/** The full GET /api/v1/cost response. */
export interface CostData {
  group_by: CostGroupBy;
  rows: CostRow[];
}

/**
 * Time-window presets for the cost view. `null` = all-time (no `since`); the
 * others map to a relative ISO `since` computed at fetch time.
 */
export const COST_WINDOWS = [
  { label: "24h", hours: 24 },
  { label: "7d", hours: 24 * 7 },
  { label: "30d", hours: 24 * 30 },
  { label: "Tudo", hours: null },
] as const;

export type CostWindowHours = number | null;

export const costKeys = {
  all: ["cost"] as const,
  data: (groupBy: CostGroupBy, windowHours: CostWindowHours) =>
    ["cost", { groupBy, windowHours }] as const,
};

/** Resolve a relative window (hours) into the absolute `since` ISO timestamp. */
function windowSince(windowHours: CostWindowHours): string | null {
  if (windowHours == null) return null;
  return new Date(Date.now() - windowHours * 60 * 60 * 1000).toISOString();
}

export function fetchCost(
  groupBy: CostGroupBy = "lane",
  windowHours: CostWindowHours = 24 * 7,
): Promise<CostData> {
  const since = windowSince(windowHours);
  const params = new URLSearchParams({ group_by: groupBy });
  if (since) params.set("since", since);
  return apiFetch<CostData>(`api/v1/cost?${params.toString()}`);
}

/** Total USD across all rows (the summary headline figure). */
export function totalUsd(rows: CostRow[]): number {
  return rows.reduce((sum, r) => sum + r.usd_cost, 0);
}

/** Total tokens across all rows. */
export function totalTokens(rows: CostRow[]): number {
  return rows.reduce((sum, r) => sum + r.tokens, 0);
}

/** Total LLM calls across all rows. */
export function totalCalls(rows: CostRow[]): number {
  return rows.reduce((sum, r) => sum + r.count, 0);
}

/** Format a USD figure with 4 decimals (LLM costs are sub-cent), pt-BR style. */
export function formatUsd(value: number): string {
  return `US$ ${value.toLocaleString("pt-BR", {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  })}`;
}
