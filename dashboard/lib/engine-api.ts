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
import type { EngineMode } from "@/lib/config-api";

export type { EngineMode } from "@/lib/config-api";

// Reuse the canonical failures client + types (lib/workers-api.ts) rather than
// re-declaring a second, drift-prone FailureItem here. Re-exported so the painel
// data layer + the board's falha sourcing depend on a single engine-api surface
// (key_link: painel-data → GET /api/v1/failures).
export { fetchFailures, type FailureItem, type FailuresData } from "@/lib/workers-api";

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
  /**
   * Operator-intent latch: True when an operator has started the engine and has
   * not yet explicitly stopped it. Unlike `state`, this does NOT flip to false
   * when `state` returns to `idle` after the dispatch loop finishes — workers
   * may still be processing. Only cleared when an operator POSTs /stop.
   */
  enabled: boolean;
  /**
   * Operator mode (Motor Pausado, phase C/H) — orthogonal to `state`/`enabled`:
   *   LIGADO    — normal auto-collection; Kanban card editing LOCKED.
   *   PAUSADO   — orchestrator drains; card editing UNLOCKED (manual steward edits).
   *   DESLIGADO — hard off (also idles + clears `enabled`); card editing UNLOCKED.
   */
  mode: EngineMode;
  /**
   * True iff Kanban card mutations (drag transitions, DLQ→WhatsApp batch) are
   * allowed — i.e. mode ∈ {PAUSADO, DESLIGADO}. The server backstops every card
   * mutation with a 423 when this is false; the dashboard mirrors it to gate the
   * drag/selection affordances.
   */
  editing_unlocked: boolean;
  /** Tri-state sync phase for the topbar indicator: idle (gray) | syncing (yellow) | synced (green). */
  sync_phase?: "idle" | "syncing" | "synced";
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
  failures: ["engine", "failures"] as const,
};

export function fetchEngineStatus(): Promise<EngineStatus> {
  return apiFetch<EngineStatus>("api/v1/engine/status");
}

// ---------------------------------------------------------------------------
// Stage transition (UI-PAINEL-2)
// ---------------------------------------------------------------------------

/** Entity discriminator for the per-entity transition endpoint path. */
export type TransitionEntity = "destino" | "atrativo";

/**
 * Body for the generic per-entity stage-transition endpoint. Mirrors the
 * backend `TransitionBody` (brave/api/routers/cms.py — `extra="forbid"`):
 *   to       — the target board column
 *   expected — the caller's view of the record's CURRENT column (optimistic-
 *              concurrency guard; a stale `expected` yields 409, not a mutation)
 */
export interface TransitionBody {
  to: string;
  expected: string;
}

/** Result of a stage transition (audited server-side). */
export interface TransitionResult {
  status: string;
  routing?: string;
  rio_id?: string;
  sub_state?: string;
}

/**
 * Fire ONE generic, audited stage transition for a destino/atrativo. The client
 * `mapDrop` allow-list (lib/painel-actions.ts) is the twin of the server-side
 * `_ALLOWED_EDGES`; this client only ever calls a path mapDrop already approved.
 * Routes to PATCH /api/v1/{destinos|atrativos}/{rioId}/transition.
 */
export function transition(
  entity: TransitionEntity,
  rioId: string,
  body: TransitionBody,
): Promise<TransitionResult> {
  return apiFetch<TransitionResult>(`api/v1/${entity}s/${rioId}/transition`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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

/**
 * Body for POST /api/v1/tripadvisor/session — the strict `SessionInjectBody`
 * shape (brave/api/routers/tripadvisor_session.py, `extra="forbid"`). The four
 * required fields come from a real browser capture; cookie VALUES are never
 * logged client-side (they post straight to the BFF). Optional fields mirror the
 * backend's optional inputs.
 */
export interface InjectTASessionBody {
  cookies: Record<string, string>;
  query_ids: Record<string, string>;
  user_agent: string;
  acquired_at: string;
  session_id?: string;
  client_hints?: Record<string, string>;
  locale?: string;
  acquisition_ip?: string;
}

/** Result of injecting a TripAdvisor session (cookie count + canary outcome). */
export interface InjectTASessionResult {
  status: string;
  cookie_count?: number;
  query_ids?: string[];
}

/**
 * (Re)establish the TripAdvisor session from an operator's authenticated cURL
 * paste (Origem modal). Surfaces `ApiError.status` to callers so the modal can
 * distinguish 422 (invalid_session — malformed paste) from 503
 * (canary_unverified — session rejected by the live canary check).
 */
export function injectTASession(
  body: InjectTASessionBody,
): Promise<InjectTASessionResult> {
  return apiFetch<InjectTASessionResult>("api/v1/tripadvisor/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * Persist the active collection source WITHOUT starting a run.
 * Calls POST /api/v1/engine/source — validates + writes to Redis source key so
 * the next /start picks up the correct sweep lane (default vs tripadvisor).
 */
export function setEngineSource(
  source: EngineSource,
): Promise<{ source: EngineSource }> {
  return apiFetch<{ source: EngineSource }>("api/v1/engine/source", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source }),
  });
}

/**
 * Set the operator mode (Motor Pausado edit-lock — phase H tri-state topbar).
 * Calls POST /api/v1/engine/mode; the backend validates the mode (422 otherwise),
 * persists it durably, and echoes back the new mode + `editing_unlocked` so the
 * dashboard can update the Kanban edit-lock indicator without waiting for the
 * next status poll.
 */
export function setEngineMode(
  mode: EngineMode,
): Promise<{ mode: EngineMode; editing_unlocked: boolean }> {
  return apiFetch<{ mode: EngineMode; editing_unlocked: boolean }>(
    "api/v1/engine/mode",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    },
  );
}
