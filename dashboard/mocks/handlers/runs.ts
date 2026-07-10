import { http, HttpResponse } from "msw";

import type {
  RunItem,
  RunReprocessResult,
  RunsResponse,
} from "@/lib/runs-api";

/**
 * MSW handlers for the Varreduras (runs history) slice (offline test harness).
 *
 * Double-prefix BFF rule (Pitfall 6): the browser client (`apiFetch`) hits the
 * BFF at a relative `/api/...` URL which jsdom resolves against
 * `http://localhost:3000`, and the BFF maps `/api/<rest>` → FastAPI `/<rest>`. So
 * to reach FastAPI's `/api/v1/runs` the browser requests `/api/api/v1/runs`; MSW
 * matches that absolute form (uf/source/depth query string is ignored by the path
 * matcher, so one handler serves the filtered requests too).
 *
 * The mock payloads are typed against the lib interfaces, so this handler IS the
 * field-for-field contract mirror of the backend RunsResponse (A5).
 */

const LIST = "http://localhost:3000/api/api/v1/runs";
const REPROCESS = "http://localhost:3000/api/api/v1/runs/:runId/reprocess";

/** A few representative runs mirroring the design's runs table. */
export const sampleRuns: RunItem[] = [
  {
    id: "run-ce-0001",
    started_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    ended_at: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString(),
    ufs: ["CE"],
    source: "tripadvisor",
    depth: "nascente_rio_mar",
    total: 142,
    synced: 138,
    failed: 4,
    status: "concluido",
  },
  {
    id: "run-ba-0002",
    started_at: new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString(),
    ended_at: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
    ufs: ["BA"],
    source: "tripadvisor",
    depth: "nascente_rio",
    total: 88,
    synced: 71,
    failed: 17,
    status: "parcial",
  },
  {
    id: "run-pe-0003",
    started_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
    ended_at: null,
    ufs: ["PE", "AL"],
    source: "tripadvisor",
    depth: "nascente",
    total: 20,
    synced: 0,
    failed: 20,
    status: "falha",
  },
];

/** Success handler: returns the given runs in the paginated envelope. */
export function runsListSuccess(items: RunItem[] = sampleRuns) {
  return http.get(LIST, () =>
    HttpResponse.json({
      items,
      total: items.length,
      offset: 0,
      limit: 50,
    } satisfies RunsResponse),
  );
}

/** Empty handler: no runs yet (the "Nenhuma varredura" empty state). */
export function runsListEmpty() {
  return http.get(LIST, () =>
    HttpResponse.json({
      items: [],
      total: 0,
      offset: 0,
      limit: 50,
    } satisfies RunsResponse),
  );
}

export function runsListError(status = 500) {
  return http.get(LIST, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

/** Reprocess handler: echoes accepted + the run id (mirrors reprocess_run return). */
export function runsReprocessSuccess() {
  return http.patch(REPROCESS, ({ params }) =>
    HttpResponse.json({
      status: "accepted",
      run_id: String(params.runId),
      ufs: ["CE"],
    } satisfies RunReprocessResult),
  );
}

/** Default barrel: list + reprocess success (suites override per state). */
export const runsHandlers = [runsListSuccess(), runsReprocessSuccess()];
