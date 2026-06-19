/**
 * Collection-engine data layer.
 *
 * Typed fetchers for the operator start/stop control over the Brave sweep.
 * Every call goes through the BFF via `apiFetch` (relative `/api/...`, operator
 * Bearer attached).
 *
 * Backing endpoints (brave/api/routers/engine.py):
 *   GET  /api/v1/engine/status   — state + progress + pipeline counts
 *   POST /api/v1/engine/start    — start the full sweep
 *   POST /api/v1/engine/stop     — request graceful stop
 */

import { apiFetch } from "@/lib/api-client";

export type EngineState = "idle" | "running" | "stopping";

export interface EnginePipelineCounts {
  nascente: number;
  rio: { in_progress: number; mar: number; dlq: number; descarte: number };
  mar: number;
  atrativos_by_sub_state: Record<string, number>;
}

export interface EngineStatus {
  state: EngineState;
  current_uf: string | null;
  ufs_done: number;
  ufs_total: number;
  counts: EnginePipelineCounts;
}

export interface EngineActionResult {
  status: string;
  ufs_total?: number;
  lane?: string;
  detail?: string;
}

/** Live engine view polls at the same 10s cadence as the rest of /processo. */
export const ENGINE_REFETCH_INTERVAL_MS = 10_000;

export const engineKeys = {
  status: ["engine", "status"] as const,
};

export function fetchEngineStatus(): Promise<EngineStatus> {
  return apiFetch<EngineStatus>("api/v1/engine/status");
}

export function startEngine(
  body?: { ufs?: string[]; lane?: "destinos" | "atrativos" | "both" },
): Promise<EngineActionResult> {
  return apiFetch<EngineActionResult>("api/v1/engine/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export function stopEngine(): Promise<EngineActionResult> {
  return apiFetch<EngineActionResult>("api/v1/engine/stop", {
    method: "POST",
  });
}
