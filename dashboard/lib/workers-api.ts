/**
 * Workers + failures data layer (D-05, §15.7).
 *
 * Query keys + typed fetchers for the workers and failures slices. Every call
 * goes through the BFF via `apiFetch` (relative `/api/...`, operator Bearer
 * attached) — never to FastAPI directly. Endpoints are read-only (T-08-16/18).
 *
 * Backing endpoints (brave/api/routers/workers.py):
 *   GET /api/v1/workers  — Celery worker status + queue depths + beat schedule
 *   GET /api/v1/failures — PoisonQuarantine recent items
 *
 * Polling (D-05): live ops view with refetchInterval every 10s — same cadence
 * as the monitor (RESEARCH §5). refetchOnWindowFocus=false to avoid hammering
 * FastAPI from background tabs.
 */

import { apiFetch } from "@/lib/api-client";

/** Live refetch cadence (ms) — 10s, same as monitor (D-05). */
export const WORKERS_REFETCH_INTERVAL_MS = 10_000;

/** A single Celery worker as reported by the workers endpoint. */
export interface WorkerInfo {
  hostname: string;
  status: "up" | "down";
  active_count: number;
  reserved_count: number;
}

/** Full response from GET /api/v1/workers. */
export interface WorkersData {
  broker_reachable: boolean;
  workers: WorkerInfo[];
  queues: {
    "brave.sweep": number | null;
    celery: number | null;
  };
  beat_schedule: {
    entries: number;
    queues: string[];
  };
}

/** A single PoisonQuarantine item. */
export interface FailureItem {
  id: string;
  task_name: string;
  error_message: string;
  quarantined_at: string | null;
}

/** Full response from GET /api/v1/failures. */
export interface FailuresData {
  total: number;
  by_task: Record<string, number>;
  items: FailureItem[];
}

/**
 * TanStack query keys for the workers slice.
 *
 * All keys share the ["workers"] prefix so a single
 * `invalidateQueries(["workers"])` refetches the board + failures panel.
 */
export const workersKeys = {
  all: ["workers"] as const,
  data: () => ["workers", "data"] as const,
  failures: () => ["workers", "failures"] as const,
};

/** Fetch live Celery worker status, queue depths, and beat schedule. */
export function fetchWorkers(): Promise<WorkersData> {
  return apiFetch<WorkersData>("api/v1/workers");
}

/** Fetch recent PoisonQuarantine failures (last `limit` items). */
export function fetchFailures(limit = 50): Promise<FailuresData> {
  return apiFetch<FailuresData>(`api/v1/failures?limit=${limit}`);
}
