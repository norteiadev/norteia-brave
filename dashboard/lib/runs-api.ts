/**
 * Varreduras data layer (UI-PAINEL-2).
 *
 * Query keys + typed fetchers for the engine-runs "Varreduras" slice. Every call
 * goes through the BFF via `apiFetch` (relative `/api/...`, operator Bearer
 * attached) — never to FastAPI directly. This client + its MSW handler together
 * ARE the consumer contract (typed Pydantic ⇄ mirrored MSW handler — A5; no
 * pact-js).
 *
 * Backing endpoints (brave/api/routers/runs.py):
 *   GET   /api/v1/runs?uf=&source=&depth=          — paginated runs history
 *   PATCH /api/v1/runs/{run_id}/reprocess          — re-dispatch the run scope (202)
 *
 * synced/failed/total are computed ON-READ on the backend over each run's
 * [started_at, ended_at] window (the async producer tasks never return counts).
 * The 7-day window helpers below summarize the loaded run set for the stat cards
 * (client-side, mirroring the cost-api total/format idiom).
 */

import { apiFetch } from "@/lib/api-client";

/**
 * A single engine run with on-read synced/failed/total. Mirrors the backend
 * RunItem Pydantic model field-for-field (extra="forbid").
 */
export interface RunItem {
  id: string;
  started_at: string;
  ended_at: string | null;
  ufs: string[];
  source: string;
  depth: string;
  total: number;
  synced: number;
  failed: number;
  status: string;
}

/** Paginated envelope for GET /api/v1/runs (mirrors RunsResponse). */
export interface RunsResponse {
  items: RunItem[];
  total: number;
  offset: number;
  limit: number;
}

/** Reprocess response: { status, run_id, ufs } (mirrors reprocess_run return). */
export interface RunReprocessResult {
  status: string;
  run_id: string;
  ufs: string[];
}

/** Filters accepted by GET /api/v1/runs. */
export interface RunsFilters {
  uf?: string;
  source?: string;
  depth?: string;
}

/** TanStack query keys — all runs keys share the ['runs'] prefix so a single
 *  `invalidateQueries(['runs'])` after a reprocess refetches the runs list. */
export const runsKeys = {
  all: ["runs"] as const,
  list: (filters: RunsFilters = {}) => ["runs", "list", filters] as const,
};

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/** List engine runs (newest first), optionally filtered by uf / source / depth. */
export function fetchRuns(filters: RunsFilters = {}): Promise<RunsResponse> {
  return apiFetch<RunsResponse>(
    `api/v1/runs${qs({ uf: filters.uf, source: filters.source, depth: filters.depth })}`,
  );
}

/**
 * Reprocess a run: re-dispatch its (ufs × source × lane) scope. Routes through
 * the BFF Bearer + the steward-guarded backend (PATCH /api/v1/runs/{id}/reprocess).
 */
export function reprocessRun(runId: string): Promise<RunReprocessResult> {
  return apiFetch<RunReprocessResult>(`api/v1/runs/${runId}/reprocess`, {
    method: "PATCH",
  });
}

// ---------------------------------------------------------------------------
// 7-day window helpers (mirror the cost-api total/format idiom) — drive the
// Varreduras stat cards over the loaded run set, client-side.
// ---------------------------------------------------------------------------

/** The stat-card window: runs started within the last 7 days. */
export const RUNS_WINDOW_HOURS = 24 * 7;

/** True when a run's started_at falls within `windowHours` of now. */
export function withinWindow(run: RunItem, windowHours: number = RUNS_WINDOW_HOURS): boolean {
  const started = Date.parse(run.started_at);
  if (Number.isNaN(started)) return false;
  return started >= Date.now() - windowHours * 60 * 60 * 1000;
}

/** Runs started within the window (default 7 days). */
export function recentRuns(items: RunItem[], windowHours: number = RUNS_WINDOW_HOURS): RunItem[] {
  return items.filter((r) => withinWindow(r, windowHours));
}

/** Total number of runs in the set. */
export function totalRuns(items: RunItem[]): number {
  return items.length;
}

/** Sum of synced records across the set. */
export function totalSynced(items: RunItem[]): number {
  return items.reduce((sum, r) => sum + r.synced, 0);
}

/** Sum of failed records across the set. */
export function totalFailed(items: RunItem[]): number {
  return items.reduce((sum, r) => sum + r.failed, 0);
}

/** Format an integer count pt-BR style. */
export function formatCount(value: number): string {
  return Math.round(value).toLocaleString("pt-BR");
}
