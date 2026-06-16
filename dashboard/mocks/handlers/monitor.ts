import { http, HttpResponse } from "msw";

import type { MonitorData } from "@/lib/monitor-api";

/**
 * MSW handlers for the monitor slice (D-07, offline test harness).
 *
 * Same double-prefix rule as the DLQ handlers: the browser client (`apiFetch`)
 * hits the BFF at a relative `/api/...` URL which jsdom resolves against
 * `http://localhost:3000`, and the BFF maps `/api/<rest>` → FastAPI `/<rest>`. So
 * to reach FastAPI's `/api/v1/monitor` the browser requests
 * `/api/api/v1/monitor`; MSW matches that absolute form.
 *
 * Each factory returns one handler; suites pick the view-state variant
 * (success / empty / error / 401) via `server.use(...)`.
 */

const BASE = "http://localhost:3000/api/api/v1/monitor";

export const sampleMonitor: MonitorData = {
  since_hours: 24,
  window_start: "2026-06-15T12:00:00Z",
  volume: {
    nascente_count: 1280,
    rio_count: { in_progress: 42, mar: 910, dlq: 73, descarte: 255 },
    mar_count: 910,
  },
  rates: {
    dlq_validated: 0.6,
    dlq_rejected: 0.25,
    dlq_reprocessed: 0.15,
  },
  rate_counts: {
    dlq_validated: 12,
    dlq_rejected: 5,
    dlq_reprocessed: 3,
  },
  throughput: 318,
  alerts: {
    failures: 0,
    quality: false,
  },
};

/** A monitor payload with active failure alerts (PoisonQuarantine + RED quality). */
export const sampleMonitorAlerting: MonitorData = {
  ...sampleMonitor,
  alerts: { failures: 4, quality: true },
};

/** An empty-window monitor payload (all counts/rates zeroed). */
export const sampleMonitorEmpty: MonitorData = {
  since_hours: 24,
  window_start: "2026-06-15T12:00:00Z",
  volume: {
    nascente_count: 0,
    rio_count: { in_progress: 0, mar: 0, dlq: 0, descarte: 0 },
    mar_count: 0,
  },
  rates: { dlq_validated: 0, dlq_rejected: 0, dlq_reprocessed: 0 },
  rate_counts: { dlq_validated: 0, dlq_rejected: 0, dlq_reprocessed: 0 },
  throughput: 0,
  alerts: { failures: 0, quality: false },
};

export function monitorSuccess(data: MonitorData = sampleMonitor) {
  return http.get(BASE, () => HttpResponse.json(data));
}

export function monitorEmpty() {
  return http.get(BASE, () => HttpResponse.json(sampleMonitorEmpty));
}

export function monitorError(status = 500) {
  return http.get(BASE, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

export function monitorUnauthorized() {
  return http.get(BASE, () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 }),
  );
}

/** Default barrel: success (suites override per state via server.use). */
export const monitorHandlers = [monitorSuccess()];
