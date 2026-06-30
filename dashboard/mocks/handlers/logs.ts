import { http, HttpResponse } from "msw";
import type { LogLine, LogsResponse } from "@/lib/logs-api";

/**
 * MSW handler for GET /api/v1/logs (BFF double-prefix).
 * Usage: server.use(logsLines()) per-test.
 */
const LOGS_BASE = "http://localhost:3000/api/api/v1/logs";

export function logsLines(overrides: Partial<LogsResponse> = {}) {
  const payload: LogsResponse = {
    source: "tripadvisor",
    lines: [
      { id: 1, ts: "2026-06-30T12:00:00Z", level: "info", event: "page_ingested" },
      { id: 2, ts: "2026-06-30T12:00:01Z", level: "info", event: "uf_done" },
    ] as LogLine[],
    cursor: 2,
    ...overrides,
  };
  return http.get(LOGS_BASE, () => HttpResponse.json(payload));
}

export function logsEmpty(source = "tripadvisor") {
  return logsLines({ source, lines: [], cursor: 0 });
}
