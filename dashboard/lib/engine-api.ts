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

/**
 * Pipeline depth — the operator-chosen cost checkpoint for a sweep run.
 * Values are identical to the backend contract (brave/core/engine.py):
 *   nascente          — ingest + §7.6 score only (free, Mtur seed only)
 *   nascente_rio      — + Places + LLM validation up to Rio routing (paid)
 *   nascente_rio_mar  — full pipeline incl. the idempotent Mar push
 */
export type EngineDepth = "nascente" | "nascente_rio" | "nascente_rio_mar";

/** PT-BR labels for the depth enum — reused by the selector AND the running-state read-back. */
export const DEPTH_LABELS: Record<EngineDepth, string> = {
  nascente: "Apenas nascente",
  nascente_rio: "Nascente → Rio",
  nascente_rio_mar: "Nascente → Rio → Mar",
};

/**
 * Collection source — which lane the sweep uses to ingest territorial data.
 *   default      — standard lane (Mtur seed + Places validation)
 *   tripadvisor  — TripAdvisor GraphQL scraper lane (Phase 11)
 */
export type EngineSource = "default" | "tripadvisor";

/** PT-BR labels for the source enum — reused by the selector AND the running-state read-back. */
export const SOURCE_LABELS: Record<EngineSource, string> = {
  default: "Padrão",
  tripadvisor: "TripAdvisor",
};

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
  /** Active run's pipeline depth, echoed by /status. null when unset/not running. */
  depth: EngineDepth | null;
  /** Active run's collection source, echoed by /status. null when unset/not running. */
  source?: EngineSource | null;
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
  body?: {
    ufs?: string[];
    lane?: "destinos" | "atrativos" | "both";
    depth?: EngineDepth;
    source?: EngineSource;
  },
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

/**
 * TripAdvisor session status — returned by GET /api/v1/tripadvisor/session/status
 * (plan 12-02). Three states:
 *   present=true               — session is in Redis, ready for sweep
 *   present=false, reason="needs_bootstrap"  — operator must inject a session
 *   present=false, reason=null — session was present but has expired / was never injected
 */
export interface TASessionStatus {
  present: boolean;
  expires_in?: number;
  query_ids?: string[];
  reason: "needs_bootstrap" | null;
}

/** TanStack Query key for TA session status. */
export const taSessionKeys = {
  status: ["ta", "session", "status"] as const,
};

/** Fetch TripAdvisor session status from the BFF. */
export function fetchTASessionStatus(): Promise<TASessionStatus> {
  return apiFetch<TASessionStatus>("api/v1/tripadvisor/session/status");
}
