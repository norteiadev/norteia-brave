import { http, HttpResponse } from "msw";

import type { DestinoDetail, DestinoListItem } from "@/lib/destinos-api";

/**
 * MSW handlers for the Destinos CMS slice (D-03, offline test harness).
 *
 * The browser client (`apiFetch`) calls the BFF at a RELATIVE `/api/...` URL,
 * and the BFF mount maps `/api/<rest>` → FastAPI `/<rest>`. So to reach
 * FastAPI's `/api/v1/destinos` the browser actually requests
 * `/api/api/v1/destinos`. MSW intercepts the browser-facing URL — in jsdom
 * the relative path resolves against `http://localhost:3000`, so we match the
 * absolute form.
 *
 * CRITICAL: double-prefix is mandatory — Pitfall 5.
 * BASE = "http://localhost:3000/api/api/v1/destinos"
 *                               ^^^^ BFF mount   ^^^^ FastAPI router prefix
 */

const BASE = "http://localhost:3000/api/api/v1/destinos";

export const sampleDestinos: DestinoListItem[] = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    entity_type: "destination",
    uf: "BA",
    routing: "dlq",
    score: 72.4,
    name: "Pelourinho",
    canonical_key: "ba:salvador:pelourinho",
    validation_pending: true,
    mar_id: null,
    published_at: null,
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    entity_type: "destination",
    uf: "RJ",
    routing: "mar",
    score: 91.2,
    name: "Copacabana",
    canonical_key: "rj:rio:copacabana",
    validation_pending: false,
    mar_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    published_at: "2026-06-01T10:00:00Z",
  },
];

export const sampleDestinoDetail: DestinoDetail = {
  id: "11111111-1111-1111-1111-111111111111",
  entity_type: "destination",
  uf: "BA",
  routing: "dlq",
  score: 72.4,
  name: "Pelourinho",
  canonical_key: "ba:salvador:pelourinho",
  validation_pending: true,
  mar_id: null,
  published_at: null,
  score_breakdown: {
    origem: 100,
    completude: 70,
    corroboracao: 50,
    atualidade: 80,
    validacao_humana: 0,
  },
  normalized: {
    name: "Pelourinho",
    uf: "BA",
    municipality: "Salvador",
    type: "historic_district",
  },
  source: "mtur",
  audit_log: [
    {
      action: "dlq_reprocessed",
      actor: "steward",
      after_state: { routing: "dlq", score: 72.4 },
      created_at: "2026-06-10T09:00:00Z",
    },
    {
      action: "dlq_validated",
      actor: "steward",
      after_state: { routing: "mar", score: 88.0 },
      created_at: "2026-06-15T14:30:00Z",
    },
  ],
  child_atrativos: {
    total: 3,
    by_sub_state: {
      discovered: 1,
      contacts_found: 2,
    },
  },
};

/** GET list — success. Supports uf + routing query param filtering. */
export function destinosListSuccess(items: DestinoListItem[] = sampleDestinos) {
  return http.get(BASE, ({ request }) => {
    const url = new URL(request.url);
    const uf = url.searchParams.get("uf");
    const routing = url.searchParams.get("routing");

    let filtered = items;
    if (uf) filtered = filtered.filter((i) => i.uf === uf);
    if (routing) filtered = filtered.filter((i) => i.routing === routing);

    return HttpResponse.json({
      items: filtered,
      total: filtered.length,
      offset: 0,
      limit: 50,
    });
  });
}

/** GET list — empty (zero items, valid envelope). */
export function destinosListEmpty() {
  return http.get(BASE, () =>
    HttpResponse.json({ items: [], total: 0, offset: 0, limit: 50 }),
  );
}

/** GET list — error. */
export function destinosListError(statusCode = 500) {
  return http.get(BASE, () =>
    HttpResponse.json({ detail: "boom" }, { status: statusCode }),
  );
}

/** GET detail — success. */
export function destinoDetailSuccess(detail: DestinoDetail = sampleDestinoDetail) {
  return http.get(`${BASE}/:id`, () => HttpResponse.json(detail));
}

/** GET detail — error. */
export function destinoDetailError(statusCode = 500) {
  return http.get(`${BASE}/:id`, () =>
    HttpResponse.json({ detail: "boom" }, { status: statusCode }),
  );
}

/** PATCH promote — success (202). */
export function destinoPromoteSuccess() {
  return http.patch(`${BASE}/:id/promote`, () =>
    HttpResponse.json(
      { status: "accepted", routing: "mar", rio_id: "11111111-1111-1111-1111-111111111111" },
      { status: 202 },
    ),
  );
}

/** PATCH descarte — success. */
export function destinoDescarteSuccess() {
  return http.patch(`${BASE}/:id/descarte`, () =>
    HttpResponse.json({
      status: "ok",
      routing: "descarte",
      rio_id: "11111111-1111-1111-1111-111111111111",
    }),
  );
}

/** PATCH reprocess — success (202). */
export function destinoReprocessSuccess() {
  return http.patch(`${BASE}/:id/reprocess`, () =>
    HttpResponse.json(
      { status: "accepted", rio_id: "11111111-1111-1111-1111-111111111111" },
      { status: 202 },
    ),
  );
}

/** Catch-all 401 for any destinos route (session-expired path). */
export function destinosUnauthorized() {
  const unauth = () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 });
  return [http.all(BASE, unauth), http.all(`${BASE}/*`, unauth)];
}

/** Default barrel: list+detail+mutations success. */
export const destinoHandlers = [
  destinosListSuccess(),
  destinoDetailSuccess(),
  destinoPromoteSuccess(),
  destinoDescarteSuccess(),
  destinoReprocessSuccess(),
];
