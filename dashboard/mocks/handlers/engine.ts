import { http, HttpResponse } from "msw";

import type {
  EngineSource,
  EngineState,
  EngineStatus,
  TASessionStatus,
} from "@/lib/engine-api";
import type { NascenteListItem } from "@/lib/nascente-api";

/**
 * MSW handlers for the collection-engine slice (offline test harness).
 * Double-prefix BFF rule: browser → /api/api/v1/engine/... (Pitfall 5).
 */

const BASE = "http://localhost:3000/api/api/v1/engine";
const TA_BASE = "http://localhost:3000/api/api/v1/tripadvisor";
const NASCENTE_BASE = "http://localhost:3000/api/api/v1/nascente";

export function engineStatus(overrides: Partial<EngineStatus> = {}) {
  const status: EngineStatus = {
    state: "idle",
    current_uf: null,
    ufs_done: 0,
    ufs_total: 0,
    enabled: false,
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

/**
 * TA session status handler — default returns a "Pronta" (ready) session.
 * Override per-test via server.use(taSessionStatus({ present: false, ... })).
 */
export function taSessionStatus(overrides: Partial<TASessionStatus> = {}) {
  const status: TASessionStatus = {
    present: true,
    expires_in: 1200,
    query_ids: ["destinations", "attractions"],
    reason: null,
    ...overrides,
  };
  return http.get(`${TA_BASE}/session/status`, () => HttpResponse.json(status));
}

/**
 * Nascente list handler (GET /api/v1/nascente) — the read-only board cards.
 * `total` defaults to items.length; pass it explicitly to drive the Nascente
 * column COUNT independently of the seeded card list (limit:1 count query).
 */
export function nascenteList(items: NascenteListItem[] = [], total?: number) {
  return http.get(`${NASCENTE_BASE}`, () =>
    HttpResponse.json({
      items,
      total: total ?? items.length,
      offset: 0,
      limit: 500,
    }),
  );
}

/** Empty Nascente list (default). */
export function nascenteEmpty() {
  return nascenteList([], 0);
}

/** Default barrel: idle status + start/stop success + TA session ready. */
export const engineHandlers = [
  engineStatus(),
  engineStartSuccess(),
  engineStopSuccess(),
  taSessionStatus(),
  nascenteEmpty(),
];
