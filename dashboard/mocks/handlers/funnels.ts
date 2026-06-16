import { http, HttpResponse } from "msw";

import type { FunnelData } from "@/lib/funnels-api";

/**
 * MSW handlers for the funnels slice (DASH-05, D-07 offline harness).
 *
 * Same double-prefix rule as the other handlers: the browser client (`apiFetch`)
 * hits the BFF at a relative `/api/...` URL which jsdom resolves against
 * `http://localhost:3000`, and the BFF maps `/api/<rest>` → FastAPI `/<rest>`. So
 * to reach FastAPI's `/api/v1/funnels` the browser requests
 * `/api/api/v1/funnels`; MSW matches that absolute form (the entity_type/uf/
 * source query string is ignored by the path matcher).
 *
 * Each factory returns one handler; suites pick the view-state variant
 * (success / empty / error / 401) via `server.use(...)`.
 */

const BASE = "http://localhost:3000/api/api/v1/funnels";

export const sampleFunnel: FunnelData = {
  filters: { entity_type: null, uf: null, source: null },
  ingested: [
    { source: "places", uf: "BA", entity_type: "attraction", count: 1200 },
    { source: "ota", uf: "RJ", entity_type: "destination", count: 800 },
    { source: "mtur", uf: "SP", entity_type: "attraction", count: 400 },
  ],
  routing: [
    { routing: "in_progress", uf: "BA", count: 900 },
    { routing: "mar", uf: "BA", count: 420 },
    { routing: "dlq", uf: "RJ", count: 260 },
    { routing: "descarte", uf: "SP", count: 130 },
  ],
  published: 420,
};

export function funnelsSuccess(data: FunnelData = sampleFunnel) {
  return http.get(BASE, () => HttpResponse.json(data));
}

export function funnelsEmpty() {
  return http.get(BASE, () =>
    HttpResponse.json({
      filters: { entity_type: null, uf: null, source: null },
      ingested: [],
      routing: [],
      published: 0,
    } satisfies FunnelData),
  );
}

export function funnelsError(status = 500) {
  return http.get(BASE, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

export function funnelsUnauthorized() {
  return http.get(BASE, () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 }),
  );
}

/** Default barrel: success (suites override per state via server.use). */
export const funnelsHandlers = [funnelsSuccess()];
