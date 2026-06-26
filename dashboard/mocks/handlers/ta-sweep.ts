import { http, HttpResponse } from "msw";

import type { TASweepProgress } from "@/lib/ta-sweep-api";

/**
 * MSW handlers for the TripAdvisor sweep-progress slice (offline test harness).
 * Double-prefix BFF rule: browser → /api/api/v1/tripadvisor/... (Pitfall 5 —
 * a single /api/ 404s in tests).
 */

const SWEEP_PROGRESS_URL =
  "http://localhost:3000/api/api/v1/tripadvisor/sweep/progress";

/**
 * Sweep-progress handler — default returns a running snapshot (5/334 pages).
 * Override per-test via server.use(taSweepProgress({ state: "done", ... })).
 */
export function taSweepProgress(overrides: Partial<TASweepProgress> = {}) {
  const status: TASweepProgress = {
    state: "running",
    pages_done: 5,
    pages_total: 334,
    attractions_ingested: 150,
    current_offset: 120,
    error_count: 0,
    started_at: "2026-06-26T12:00:00Z",
    ...overrides,
  };
  return http.get(SWEEP_PROGRESS_URL, () => HttpResponse.json(status));
}

/** 401 variant — mirrors the engine unauthorized handler for 401-safe render tests. */
export function taSweepUnauthorized() {
  const unauth = () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 });
  return http.get(SWEEP_PROGRESS_URL, unauth);
}

/** Default barrel: a running sweep snapshot. */
export const taSweepHandlers = [taSweepProgress()];
