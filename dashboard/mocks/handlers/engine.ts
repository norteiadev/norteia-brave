import { http, HttpResponse } from "msw";

import type { EngineSource, EngineState, EngineStatus } from "@/lib/engine-api";

/**
 * MSW handlers for the collection-engine slice (offline test harness).
 * Double-prefix BFF rule: browser → /api/api/v1/engine/... (Pitfall 5).
 */

const BASE = "http://localhost:3000/api/api/v1/engine";

export function engineStatus(overrides: Partial<EngineStatus> = {}) {
  const status: EngineStatus = {
    state: "idle",
    current_uf: null,
    ufs_done: 0,
    ufs_total: 0,
    counts: {
      nascente: 0,
      rio: { in_progress: 0, mar: 0, dlq: 0, descarte: 0 },
      mar: 0,
      atrativos_by_sub_state: {},
    },
    depth: null,
    source: null,
    ...overrides,
  };
  return http.get(`${BASE}/status`, () => HttpResponse.json(status));
}

export function engineStartSuccess(
  state: EngineState = "running",
  source: EngineSource = "default",
) {
  return http.post(`${BASE}/start`, () =>
    HttpResponse.json(
      { status: "started", ufs_total: 27, lane: "both", depth: "nascente_rio", source },
      { status: 202 },
    ),
  );
}

export function engineStopSuccess() {
  return http.post(`${BASE}/stop`, () =>
    HttpResponse.json({ status: "stopping" }, { status: 202 }),
  );
}

export function engineUnauthorized() {
  const unauth = () => HttpResponse.json({ detail: "Unauthorized" }, { status: 401 });
  return [http.all(BASE, unauth), http.all(`${BASE}/*`, unauth)];
}

/** Default barrel: idle status + start/stop success. */
export const engineHandlers = [
  engineStatus(),
  engineStartSuccess(),
  engineStopSuccess(),
];
