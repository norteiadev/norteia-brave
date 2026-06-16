/**
 * Funnels data layer (DASH-05, D-01).
 *
 * Query key + typed fetcher for the funnels slice. The call goes through the BFF
 * via `apiFetch` (relative `/api/...`, operator Bearer attached) — never to
 * FastAPI directly. The endpoint is a read-only GROUP BY over the medallion
 * layers (no pipeline logic, no writes).
 *
 * Backing endpoint (brave/api/routers/dashboard.py):
 *   GET /api/v1/funnels?entity_type&uf&source →
 *     { filters, ingested[], routing[], published } — Nascente counts grouped by
 *     (source, uf, entity_type), Rio counts grouped by (routing, uf), Mar
 *     published terminal count. Optional entity_type/uf/source filters.
 *
 * The funnels view renders stage bars by UF/source: ingested → in_progress →
 * mar/dlq/descarte. Aggregate counts only — no PII, no record content.
 */

import { apiFetch } from "@/lib/api-client";

/** The two collection lanes this milestone delivers (entity_type filter). */
export type FunnelEntityType = "destination" | "attraction";

/** One ingested-stage row: a source × uf × entity_type raw-record count. */
export interface FunnelIngestedRow {
  source: string;
  uf: string;
  entity_type: string;
  count: number;
}

/** One routing-stage row: a Rio working-area count per (routing, uf). */
export interface FunnelRoutingRow {
  /** in_progress | mar | dlq | descarte (the §7.6 routing outcome). */
  routing: string;
  uf: string;
  count: number;
}

/** The full GET /api/v1/funnels response. */
export interface FunnelData {
  filters: {
    entity_type: string | null;
    uf: string | null;
    source: string | null;
  };
  ingested: FunnelIngestedRow[];
  routing: FunnelRoutingRow[];
  /** Mar terminal published count (bottom of the funnel). */
  published: number;
}

/** Funnel filters the view can apply (all optional → all-data). */
export interface FunnelFilters {
  entityType?: FunnelEntityType | null;
  uf?: string | null;
  source?: string | null;
}

export const funnelKeys = {
  all: ["funnels"] as const,
  data: (filters: FunnelFilters) => ["funnels", filters] as const,
};

export function fetchFunnels(filters: FunnelFilters = {}): Promise<FunnelData> {
  const params = new URLSearchParams();
  if (filters.entityType) params.set("entity_type", filters.entityType);
  if (filters.uf) params.set("uf", filters.uf);
  if (filters.source) params.set("source", filters.source);
  const qs = params.toString();
  return apiFetch<FunnelData>(`api/v1/funnels${qs ? `?${qs}` : ""}`);
}

/**
 * The canonical pipeline stages, in funnel order. `ingested` is the top of the
 * funnel (every raw record); the routing outcomes are the bottom split.
 */
export const FUNNEL_STAGES = [
  { key: "ingested", label: "ingerido" },
  { key: "in_progress", label: "em progresso" },
  { key: "mar", label: "mar" },
  { key: "dlq", label: "dlq" },
  { key: "descarte", label: "descarte" },
] as const;

export type FunnelStageKey = (typeof FUNNEL_STAGES)[number]["key"];

/** A single bar in the funnel chart: a stage with its total count. */
export interface FunnelStageBar {
  stage: FunnelStageKey;
  label: string;
  count: number;
}

/**
 * Collapse the endpoint's three blocks into ordered stage totals. `ingested` is
 * the summed Nascente count; the routing outcomes (in_progress / mar / dlq /
 * descarte) are summed from the Rio routing rows. Stages absent from the payload
 * render as 0 so the funnel shape stays stable.
 */
export function toStageBars(data: FunnelData): FunnelStageBar[] {
  const ingestedTotal = data.ingested.reduce((sum, r) => sum + r.count, 0);
  const routingTotals: Record<string, number> = {};
  for (const r of data.routing) {
    routingTotals[r.routing] = (routingTotals[r.routing] ?? 0) + r.count;
  }
  return FUNNEL_STAGES.map((s) => ({
    stage: s.key,
    label: s.label,
    count: s.key === "ingested" ? ingestedTotal : (routingTotals[s.key] ?? 0),
  }));
}

/** True when the funnel has no records at all (empty-period state). */
export function isFunnelEmpty(data: FunnelData): boolean {
  return toStageBars(data).every((b) => b.count === 0);
}
