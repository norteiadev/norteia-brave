/**
 * Brave monitor data layer (DASH-02, §15.7).
 *
 * Query key + typed fetcher for the monitor slice. The call goes through the BFF
 * via `apiFetch` (relative `/api/...`, operator Bearer attached) — never to
 * FastAPI directly. The endpoint is read-only (D-01).
 *
 * Backing endpoint (brave/api/routers/dashboard.py):
 *   GET /api/v1/monitor?since_hours= — volume + rates + throughput + alerts
 *
 * Polling (D-04): the monitor view drives liveness via TanStack Query
 * `refetchInterval` (RESEARCH §5) — no WebSocket this milestone (CONTEXT deferred).
 */

import { apiFetch } from "@/lib/api-client";

/** Live refetch cadence (ms). RESEARCH §5 / UI-SPEC: 5–15s for an ops console. */
export const MONITOR_REFETCH_INTERVAL_MS = 10_000;

/** Per-layer record volume (mirrors the backend `volume` block). */
export interface MonitorVolume {
  nascente_count: number;
  rio_count: {
    in_progress: number;
    mar: number;
    dlq: number;
    descarte: number;
  };
  mar_count: number;
}

/** AuditLog-derived approval/rejection/DLQ proportions over the window. */
export interface MonitorRates {
  dlq_validated: number;
  dlq_rejected: number;
  dlq_reprocessed: number;
}

/** Operational failure alerts. */
export interface MonitorAlerts {
  /** PoisonQuarantine row count. */
  failures: number;
  /** True when the WhatsApp quality rating is RED (auto-pause). */
  quality: boolean;
}

/** The full GET /api/v1/monitor response. */
export interface MonitorData {
  since_hours: number;
  window_start: string;
  volume: MonitorVolume;
  rates: MonitorRates;
  /** Raw windowed action counts behind `rates`. */
  rate_counts: MonitorRates;
  /** RioRecord rows processed in the window. */
  throughput: number;
  alerts: MonitorAlerts;
}

export const monitorKeys = {
  all: ["monitor"] as const,
  data: (sinceHours: number) => ["monitor", { sinceHours }] as const,
};

export function fetchMonitor(sinceHours = 24): Promise<MonitorData> {
  return apiFetch<MonitorData>(`api/v1/monitor?since_hours=${sinceHours}`);
}
