import { http, HttpResponse } from "msw";

import type { AppConfigSnapshot } from "@/lib/config-api";

/**
 * MSW handlers for the runtime-config slice (Phase H Config view).
 *
 * Same BFF double-prefix rule as the other handlers: the browser client
 * (`apiFetch`) hits a RELATIVE `/api/...` URL which jsdom resolves against
 * `http://localhost:3000`, and the BFF maps `/api/<rest>` → FastAPI `/<rest>`.
 * So reaching FastAPI's `/api/v1/config` means matching `/api/api/v1/config`.
 *
 * GET and PATCH share the same URL (distinguished by method), so there is no
 * route-ordering hazard. Suites pick the variant via `server.use(...)`.
 */

const BASE = "http://localhost:3000/api/api/v1/config";

/** A valid snapshot: five weights sum to 100, threshold 85, engine LIGADO. */
export const sampleConfig: AppConfigSnapshot = {
  score: {
    weight_origem: 20,
    weight_completude: 20,
    weight_corroboracao: 20,
    weight_atualidade: 20,
    weight_validacao_humana: 20,
    threshold_mar: 85,
  },
  engine: { mode: "LIGADO" },
  sources: { mtur: true, tripadvisor: true, places: false },
};

export function configGetSuccess(
  overrides: Partial<AppConfigSnapshot> = {},
) {
  return http.get(BASE, () =>
    HttpResponse.json({ ...sampleConfig, ...overrides }),
  );
}

export function configGetError(status = 500) {
  return http.get(BASE, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

/** PATCH — echoes the sorted touched keys + the (post-write) redacted snapshot. */
export function configPatchSuccess(
  config: AppConfigSnapshot = sampleConfig,
) {
  return http.patch(BASE, async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      updated: Object.keys(body ?? {}).sort(),
      config,
    });
  });
}

/** PATCH rejected — the reliability weight-sum (or range/mode) 422 backstop. */
export function configPatchError(
  status = 422,
  detail = "score weights (origem + completude + corroboracao + atualidade + validacao_humana) must sum to 100 — got 90",
) {
  return http.patch(BASE, () => HttpResponse.json({ detail }, { status }));
}

/** Catch-all 401 for the config route (session-expired path). */
export function configUnauthorized() {
  const unauth = () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 });
  return [http.all(BASE, unauth)];
}

/** Default barrel: GET + PATCH success (suites override per state via server.use). */
export const configHandlers = [configGetSuccess(), configPatchSuccess()];
