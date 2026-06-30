/**
 * Logs data layer — incremental tail of the per-source Brave log ring buffer.
 * Backend: GET /api/v1/logs?source={source}&since={cursor}&limit={n}
 *          Bearer-gated; BFF double-prefix applies via apiFetch.
 *
 * LGPD: the buffer never contains cookies/tokens/PII (enforced server-side by
 * log_buffer._BLOCKED_FIELDS). This client only exposes the safe structured
 * event fields.
 */
import { apiFetch } from "@/lib/api-client";

export interface LogLine {
  id: number;
  ts: string;
  level: string;
  event: string;
  [key: string]: unknown;
}

export interface LogsResponse {
  source: string;
  lines: LogLine[];
  cursor: number;
}

export const logsKeys = {
  tail: (source: string) => ["logs", "tail", source] as const,
};

/** Fetch log tail. `since` is the last cursor id (omit for initial load). */
export function fetchLogs(
  source: string,
  since?: number,
  limit = 100,
): Promise<LogsResponse> {
  const params = new URLSearchParams({ source, limit: String(limit) });
  if (since != null && since > 0) params.set("since", String(since));
  return apiFetch<LogsResponse>(`api/v1/logs?${params}`);
}
