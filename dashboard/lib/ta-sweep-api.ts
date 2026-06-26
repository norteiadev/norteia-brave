/**
 * TripAdvisor sweep-progress data layer (TA-12).
 *
 * Typed fetcher for the live national-sweep progress panel. Every call goes
 * through the BFF via `apiFetch` (relative `/api/...`, operator Bearer attached);
 * the `bff()` helper maps the bare FastAPI path onto the double `/api/api/`
 * mount — callers never write the double prefix.
 *
 * Backing endpoint (brave/api/routers/tripadvisor_session.py, plan 15-03):
 *   GET /api/v1/tripadvisor/sweep/progress — read-only sweep snapshot over Redis
 *
 * Shared JSON contract — must match 15-03 TASweepProgressResponse EXACTLY.
 */

import { apiFetch } from "@/lib/api-client";

import { ENGINE_REFETCH_INTERVAL_MS } from "@/lib/engine-api";

/** Re-export so the panel polls at the same 10s cadence as the rest of /processo. */
export { ENGINE_REFETCH_INTERVAL_MS };

/**
 * Terminal/active state of the national sweep.
 *   idle                     — no sweep has run (or progress not yet initialised)
 *   running                  — pages are being fetched + ingested
 *   done                     — the full sweep completed
 *   stopped_needs_bootstrap  — DataDome/session expired mid-run; operator must re-inject
 */
export type TASweepState =
  | "running"
  | "done"
  | "stopped_needs_bootstrap"
  | "idle";

export interface TASweepProgress {
  state: TASweepState;
  pages_done: number;
  pages_total: number;
  attractions_ingested: number;
  current_offset: number;
  error_count: number;
  started_at?: string;
}

/** TanStack Query key for the sweep-progress poll. */
export const taSweepKeys = {
  status: ["ta", "sweep", "progress"] as const,
};

/** Fetch the live TripAdvisor sweep progress from the BFF. */
export function fetchTASweepProgress(): Promise<TASweepProgress> {
  return apiFetch<TASweepProgress>("api/v1/tripadvisor/sweep/progress");
}
