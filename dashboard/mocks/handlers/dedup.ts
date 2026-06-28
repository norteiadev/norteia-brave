import { http, HttpResponse } from "msw";

import type {
  DedupPairItem,
  DedupPairsResponse,
  DedupResolveResult,
} from "@/lib/dedup-api";

/**
 * MSW handlers for the Duplicados (dedup) slice (offline test harness).
 *
 * Double-prefix BFF rule (Pitfall 6): the browser client (`apiFetch`) hits the
 * BFF at a relative `/api/...` URL which jsdom resolves against
 * `http://localhost:3000`, and the BFF maps `/api/<rest>` → FastAPI `/<rest>`. So
 * to reach FastAPI's `/api/v1/dedup/pairs` the browser requests
 * `/api/api/v1/dedup/pairs`; MSW matches that absolute form.
 *
 * The mock payloads are typed against the lib interfaces, so this handler IS the
 * field-for-field contract mirror of the backend DedupPairsResponse (A5).
 */

const PAIRS = "http://localhost:3000/api/api/v1/dedup/pairs";
const RESOLVE = "http://localhost:3000/api/api/v1/dedup/pairs/:candidateRioId/resolve";

/** Two representative pairs mirroring the design seed (seedDups). */
export const sampleDedupPairs: DedupPairItem[] = [
  {
    candidate_id: "cand-ce-praca",
    mar_id: "mar-ce-0142",
    candidate_rio_id: "cand-ce-praca",
    mar_rio_id: "rio-ce-0142",
    uf: "CE",
    municipio: "Fortaleza",
    entity_type: "atrativo",
    similarity: 0.95,
    similarity_source: "embedding_stub",
    matched_fields: ["name", "municipio", "uf"],
    diverged_fields: [
      { field: "source", candidate: "tripadvisor", mar: "mtur" },
      { field: "coordenadas", candidate: "-3.73,-38.52", mar: "-3.73,-38.53" },
    ],
  },
  {
    candidate_id: "cand-ba-mercado",
    mar_id: "mar-ba-0098",
    candidate_rio_id: "cand-ba-mercado",
    mar_rio_id: "rio-ba-0098",
    uf: "BA",
    municipio: "Salvador",
    entity_type: "atrativo",
    similarity: 0.88,
    similarity_source: "embedding_stub",
    matched_fields: ["municipio", "uf"],
    diverged_fields: [
      { field: "name", candidate: "Mercado Modelo", mar: "Mercado Modelo de Salvador" },
      { field: "source", candidate: "tripadvisor", mar: "mtur" },
    ],
  },
];

/** Success handler: returns the given pairs in the paginated envelope. */
export function dedupPairsSuccess(items: DedupPairItem[] = sampleDedupPairs) {
  return http.get(PAIRS, () =>
    HttpResponse.json({
      items,
      total: items.length,
      offset: 0,
      limit: 50,
    } satisfies DedupPairsResponse),
  );
}

/** Empty handler: no pending pairs (the "Nenhum duplicado pendente" state). */
export function dedupPairsEmpty() {
  return http.get(PAIRS, () =>
    HttpResponse.json({
      items: [],
      total: 0,
      offset: 0,
      limit: 50,
    } satisfies DedupPairsResponse),
  );
}

export function dedupPairsError(status = 500) {
  return http.get(PAIRS, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

/** Resolve handler: echoes the posted action (mirrors resolve_pair return). */
export function dedupResolveSuccess() {
  return http.patch(RESOLVE, async ({ request }) => {
    const body = (await request.json()) as { action: DedupResolveResult["action"] };
    return HttpResponse.json({
      status: "ok",
      action: body.action,
    } satisfies DedupResolveResult);
  });
}

/** Default barrel: pairs + resolve success (suites override per state). */
export const dedupHandlers = [dedupPairsSuccess(), dedupResolveSuccess()];
