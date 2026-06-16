import { http, HttpResponse } from "msw";

import type { DlqDetail, DlqListItem } from "@/lib/dlq-api";

/**
 * MSW handlers for the DLQ slice (D-07, offline test harness).
 *
 * The browser client (`apiFetch`) calls the BFF at a RELATIVE `/api/...` URL, and
 * the BFF mount maps `/api/<rest>` → FastAPI `/<rest>`. So to reach FastAPI's
 * `/api/v1/dlq` the browser actually requests `/api/api/v1/dlq`. MSW intercepts
 * that browser-facing URL — in jsdom the relative path resolves against
 * `http://localhost` (configured in vitest/jsdom), so we match the absolute form.
 *
 * Each factory returns a handler array; suites apply them via `server.use(...)`
 * and pick the variant for the view-state under test (success/empty/error/401).
 */

const BASE = "http://localhost:3000/api/api/v1/dlq";

export const sampleListItems: DlqListItem[] = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    nascente_id: "aaaaaaaa-1111-1111-1111-111111111111",
    entity_type: "destination",
    uf: "BA",
    routing: "dlq",
    dlq_reason: "below_threshold",
    score: 72.4,
    score_version: "v1",
    canonical_key: "ba:salvador:pelourinho",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    nascente_id: "aaaaaaaa-2222-2222-2222-222222222222",
    entity_type: "destination",
    uf: "RJ",
    routing: "dlq",
    dlq_reason: "low_corroboration",
    score: 64.0,
    score_version: "v1",
    canonical_key: "rj:rio:copacabana",
  },
  {
    id: "33333333-3333-3333-3333-333333333333",
    nascente_id: "aaaaaaaa-3333-3333-3333-333333333333",
    entity_type: "destination",
    uf: "SP",
    routing: "dlq",
    dlq_reason: "below_threshold",
    score: 58.1,
    score_version: "v1",
    canonical_key: "sp:sao-paulo:ibirapuera",
  },
];

export const sampleDetail: DlqDetail = {
  id: "11111111-1111-1111-1111-111111111111",
  routing: "dlq",
  sub_state: "aguardando_revisao",
  dlq_reason: "below_threshold",
  score: 72.4,
  score_version: "v1",
  score_breakdown: {
    origem: 90,
    completude: 60,
    corroboracao: 50,
    atualidade: 80,
    validacao_humana: 0,
  },
  normalized: {
    name: "Pelourinho",
    uf: "BA",
    municipality: "Salvador",
  },
  nascente_payload: {
    raw_name: "Pelourinho - Centro Histórico",
    source: "google_places",
    place_id: "ChIJ-test",
  },
  signals: {
    business_status: "OPERATIONAL",
    rating: 4.6,
  },
  whatsapp_log: [
    {
      id: "log-1",
      action: "dlq_reprocessed",
      actor: "steward",
      before_state: { routing: "dlq" },
      after_state: { routing: "dlq" },
      created_at: "2026-06-15T12:00:00Z",
    },
  ],
};

/** GET list — success (default returns the priority-ordered sample). */
export function dlqListSuccess(items: DlqListItem[] = sampleListItems) {
  return http.get(BASE, ({ request }) => {
    const url = new URL(request.url);
    const uf = url.searchParams.get("uf");
    const filtered = uf ? items.filter((i) => i.uf === uf) : items;
    return HttpResponse.json(filtered);
  });
}

export function dlqListEmpty() {
  return http.get(BASE, () => HttpResponse.json([]));
}

export function dlqListError(status = 500) {
  return http.get(BASE, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

/** GET detail — success. */
export function dlqDetailSuccess(detail: DlqDetail = sampleDetail) {
  return http.get(`${BASE}/:rioId`, () => HttpResponse.json(detail));
}

/** GET detail — empty body (server returned an empty object). */
export function dlqDetailEmpty() {
  return http.get(`${BASE}/:rioId`, () =>
    HttpResponse.json({
      id: "",
      routing: "dlq",
      sub_state: null,
      dlq_reason: null,
      score: null,
      score_version: null,
      score_breakdown: {},
      normalized: {},
      nascente_payload: {},
      signals: {},
      whatsapp_log: [],
    }),
  );
}

export function dlqDetailError(status = 500) {
  return http.get(`${BASE}/:rioId`, () =>
    HttpResponse.json({ detail: "boom" }, { status }),
  );
}

/** Mutations — generic 202/200 success. */
export function dlqValidateSuccess() {
  return http.patch(`${BASE}/:rioId/validate`, () =>
    HttpResponse.json(
      { status: "accepted", rio_id: "x", routing: "mar" },
      { status: 202 },
    ),
  );
}

export function dlqDescarteSuccess() {
  return http.patch(`${BASE}/:rioId/descarte`, () =>
    HttpResponse.json({ status: "ok", routing: "descarte", rio_id: "x" }),
  );
}

export function dlqReprocessSuccess() {
  return http.patch(`${BASE}/:rioId/reprocess`, () =>
    HttpResponse.json({ status: "accepted", rio_id: "x" }, { status: 202 }),
  );
}

export function dlqBatchSuccess(validated = 3) {
  return http.post(`${BASE}/validate-batch`, ({ request }) => {
    const url = new URL(request.url);
    const uf = url.searchParams.get("uf") ?? "";
    return HttpResponse.json(
      { status: "accepted", uf, validated },
      { status: 202 },
    );
  });
}

/** Catch-all 401 for any DLQ route (session-expired path) — covers both the
 *  bare list endpoint and the per-record/detail/mutation sub-paths. */
export function dlqUnauthorized() {
  const unauth = () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 });
  return [http.all(BASE, unauth), http.all(`${BASE}/*`, unauth)];
}

/** Default barrel: list+detail success (suites override per state via server.use). */
export const dlqHandlers = [
  dlqListSuccess(),
  dlqDetailSuccess(),
  dlqValidateSuccess(),
  dlqDescarteSuccess(),
  dlqReprocessSuccess(),
  dlqBatchSuccess(),
];
