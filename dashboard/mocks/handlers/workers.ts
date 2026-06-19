import { http, HttpResponse } from "msw";

import type { FailuresData, WorkersData } from "@/lib/workers-api";

/**
 * MSW handlers for the workers + failures slices (D-05, D-07, offline test
 * harness).
 *
 * Same double-prefix rule as all other handlers: the browser client (`apiFetch`)
 * hits the BFF at a relative `/api/...` URL which jsdom resolves against
 * `http://localhost:3000`, and the BFF maps `/api/<rest>` → FastAPI `/<rest>`. To
 * reach FastAPI's `/api/v1/workers` the browser requests `/api/api/v1/workers`;
 * MSW matches that absolute form.
 *
 * Each factory returns one handler; suites pick the view-state variant
 * (success / broker-down / empty / error) via `server.use(...)`.
 */

const BASE_WORKERS = "http://localhost:3000/api/api/v1/workers";
const BASE_FAILURES = "http://localhost:3000/api/api/v1/failures";

// ---------------------------------------------------------------------------
// Sample data
// ---------------------------------------------------------------------------

/** A healthy workers payload: broker reachable, one worker up, queues populated. */
export const sampleWorkers: WorkersData = {
  broker_reachable: true,
  workers: [
    {
      hostname: "celery@worker-1",
      status: "up",
      active_count: 2,
      reserved_count: 5,
    },
  ],
  queues: {
    "brave.sweep": 27,
    celery: 0,
  },
  beat_schedule: {
    entries: 54,
    queues: ["brave.sweep"],
  },
};

/**
 * Broker-down payload: broker_reachable=false, workers=[].
 * WorkerBoard must render the "Broker indisponível" banner without throwing.
 */
export const sampleWorkersBrokerDown: WorkersData = {
  broker_reachable: false,
  workers: [],
  queues: {
    "brave.sweep": null,
    celery: null,
  },
  beat_schedule: {
    entries: 54,
    queues: ["brave.sweep"],
  },
};

/** Recent failures payload: 2 quarantine items for `brave.process_nascente`. */
export const sampleFailures: FailuresData = {
  total: 2,
  by_task: { "brave.process_nascente": 2 },
  items: [
    {
      id: "ffffffff-0001-0001-0001-000000000001",
      task_name: "brave.process_nascente",
      error_message: "ValidationError: origem field required",
      quarantined_at: "2026-06-19T00:00:00Z",
    },
    {
      id: "ffffffff-0001-0001-0001-000000000002",
      task_name: "brave.process_nascente",
      error_message: "ValidationError: score_breakdown missing keys",
      quarantined_at: "2026-06-18T23:45:00Z",
    },
  ],
};

/** No failures payload. */
export const sampleFailuresEmpty: FailuresData = {
  total: 0,
  by_task: {},
  items: [],
};

// ---------------------------------------------------------------------------
// Handler factories
// ---------------------------------------------------------------------------

/** GET /api/v1/workers — healthy broker + active worker. */
export function workersSuccess(data: WorkersData = sampleWorkers) {
  return http.get(BASE_WORKERS, () => HttpResponse.json(data));
}

/** GET /api/v1/workers — broker unreachable, workers=[]. */
export function workersBrokerDown() {
  return http.get(BASE_WORKERS, () =>
    HttpResponse.json(sampleWorkersBrokerDown),
  );
}

/** GET /api/v1/workers — server error. */
export function workersError(status = 500) {
  return http.get(BASE_WORKERS, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

/** GET /api/v1/failures — recent quarantine items. */
export function failuresSuccess(data: FailuresData = sampleFailures) {
  return http.get(BASE_FAILURES, () => HttpResponse.json(data));
}

/** GET /api/v1/failures — empty quarantine. */
export function failuresEmpty() {
  return http.get(BASE_FAILURES, () => HttpResponse.json(sampleFailuresEmpty));
}

/** GET /api/v1/failures — server error. */
export function failuresError(status = 500) {
  return http.get(BASE_FAILURES, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

/** Default barrel: success variants (suites override per state via server.use). */
export const workersHandlers = [workersSuccess(), failuresSuccess()];
