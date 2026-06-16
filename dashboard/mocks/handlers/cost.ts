import { http, HttpResponse } from "msw";

import type { CostData, CostGroupBy } from "@/lib/cost-api";

/**
 * MSW handlers for the cost slice (D-07, offline test harness).
 *
 * Same double-prefix rule as the other handlers: the browser client (`apiFetch`)
 * hits the BFF at a relative `/api/...` URL which jsdom resolves against
 * `http://localhost:3000`, and the BFF maps `/api/<rest>` → FastAPI `/<rest>`. So
 * to reach FastAPI's `/api/v1/cost` the browser requests `/api/api/v1/cost`; MSW
 * matches that absolute form (query string — group_by / since — is ignored by the
 * path matcher, so one handler serves both the lane and model requests, switching
 * the rows by the `group_by` query param).
 *
 * Each factory returns one handler; suites pick the view-state variant
 * (success / empty / error / 401) via `server.use(...)`.
 */

const BASE = "http://localhost:3000/api/api/v1/cost";

export const sampleCostByLane: CostData = {
  group_by: "lane",
  rows: [
    { key: "destinos", usd_cost: 4.215, tokens: 1_280_400, count: 1820 },
    { key: "atrativos", usd_cost: 2.8307, tokens: 940_120, count: 1310 },
    { key: "desmembramento", usd_cost: 0.612, tokens: 210_300, count: 290 },
  ],
};

export const sampleCostByModel: CostData = {
  group_by: "model",
  rows: [
    {
      key: "deepseek/deepseek-chat:nitro",
      usd_cost: 5.901,
      tokens: 1_980_000,
      count: 2640,
    },
    {
      key: "anthropic/claude-sonnet-4.5",
      usd_cost: 1.7567,
      tokens: 450_820,
      count: 780,
    },
  ],
};

/** group_by-aware success handler: lane vs model payload by the query param. */
export function costSuccess(
  byLane: CostData = sampleCostByLane,
  byModel: CostData = sampleCostByModel,
) {
  return http.get(BASE, ({ request }) => {
    const url = new URL(request.url);
    const groupBy = (url.searchParams.get("group_by") ?? "lane") as CostGroupBy;
    return HttpResponse.json(groupBy === "model" ? byModel : byLane);
  });
}

/** Empty-window handler: rows == [] for whichever dimension is requested. */
export function costEmpty() {
  return http.get(BASE, ({ request }) => {
    const url = new URL(request.url);
    const groupBy = (url.searchParams.get("group_by") ?? "lane") as CostGroupBy;
    return HttpResponse.json({ group_by: groupBy, rows: [] } satisfies CostData);
  });
}

export function costError(status = 500) {
  return http.get(BASE, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

export function costUnauthorized() {
  return http.get(BASE, () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 }),
  );
}

/** Default barrel: success (suites override per state via server.use). */
export const costHandlers = [costSuccess()];
