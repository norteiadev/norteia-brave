import { http, HttpResponse } from "msw";

import type { AtrativoDetail, AtrativoListItem } from "@/lib/atrativos-api";

/**
 * MSW handlers for the Atrativos CMS slice (D-04, offline test harness).
 *
 * The browser client (`apiFetch`) calls the BFF at a RELATIVE `/api/...` URL,
 * and the BFF mount maps `/api/<rest>` → FastAPI `/<rest>`. So to reach
 * FastAPI's `/api/v1/atrativos` the browser actually requests
 * `/api/api/v1/atrativos`. MSW intercepts the browser-facing URL — in jsdom
 * the relative path resolves against `http://localhost:3000`, so we match the
 * absolute form.
 *
 * CRITICAL: double-prefix is mandatory — Pitfall 5.
 * BASE = "http://localhost:3000/api/api/v1/atrativos"
 *                               ^^^^ BFF mount   ^^^^ FastAPI router prefix
 *
 * PII contract: sample data uses phone_masked (e.g. "**1234"), NEVER phone_e164.
 */

const BASE = "http://localhost:3000/api/api/v1/atrativos";

export const sampleAtrativos: AtrativoListItem[] = [
  {
    id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    entity_type: "attraction",
    uf: "BA",
    routing: "in_progress",
    sub_state: "discovered",
    score: null,
    name: "Mercado Modelo",
    validation_pending: false,
    mar_id: null,
    parent_mar_id: "11111111-1111-1111-1111-111111111111",
    contacts_summary: {
      phone_masked: "**1234",
      website: "https://mercadomodelo.ba.gov.br",
    },
  },
  {
    id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    entity_type: "attraction",
    uf: "BA",
    routing: "in_progress",
    sub_state: "aguardando_consulta_whatsapp",
    score: 68.5,
    name: "Elevador Lacerda",
    validation_pending: true,
    mar_id: null,
    parent_mar_id: "11111111-1111-1111-1111-111111111111",
    contacts_summary: {
      phone_masked: "**5678",
      website: null,
    },
  },
];

export const sampleAtrativoDetail: AtrativoDetail = {
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  entity_type: "attraction",
  uf: "BA",
  routing: "in_progress",
  sub_state: "signals_gathered",
  score: 61.0,
  name: "Mercado Modelo",
  validation_pending: false,
  mar_id: null,
  parent_mar_id: "11111111-1111-1111-1111-111111111111",
  contacts_summary: {
    phone_masked: "**1234",
    website: "https://mercadomodelo.ba.gov.br",
  },
  score_breakdown: {
    origem: 80,
    completude: 60,
    corroboracao: 50,
    atualidade: 70,
    validacao_humana: 0,
  },
  normalized: {
    name: "Mercado Modelo",
    phone_masked: "**1234",
    uf: "BA",
    municipality: "Salvador",
    type: "market",
  },
  audit_log: [
    {
      action: "atrativo_discovered",
      actor: "discovery_agent",
      after_state: { sub_state: "discovered", routing: "in_progress" },
      created_at: "2026-06-10T09:00:00Z",
    },
    {
      action: "sub_state_advanced",
      actor: "contact_finder_agent",
      after_state: { sub_state: "contacts_found" },
      created_at: "2026-06-11T10:30:00Z",
    },
    {
      action: "sub_state_advanced",
      actor: "signal_agent",
      after_state: { sub_state: "signals_gathered" },
      created_at: "2026-06-12T14:00:00Z",
    },
  ],
  parent_destino: { mar_id: "11111111-1111-1111-1111-111111111111", name: "Salvador" },
};

/** GET list — success. Supports sub_state + uf + parent_mar_id query param filtering. */
export function atrativosListSuccess(items: AtrativoListItem[] = sampleAtrativos) {
  return http.get(BASE, ({ request }) => {
    const url = new URL(request.url);
    const uf = url.searchParams.get("uf");
    const sub_state = url.searchParams.get("sub_state");
    const parent_mar_id = url.searchParams.get("parent_mar_id");

    let filtered = items;
    if (uf) filtered = filtered.filter((i) => i.uf === uf);
    if (sub_state) filtered = filtered.filter((i) => i.sub_state === sub_state);
    if (parent_mar_id) filtered = filtered.filter((i) => i.parent_mar_id === parent_mar_id);

    return HttpResponse.json({
      items: filtered,
      total: filtered.length,
      offset: 0,
      limit: 50,
    });
  });
}

/** GET list — empty (zero items, valid envelope). */
export function atrativosListEmpty() {
  return http.get(BASE, () =>
    HttpResponse.json({ items: [], total: 0, offset: 0, limit: 50 }),
  );
}

/** GET list — error. */
export function atrativosListError(statusCode = 500) {
  return http.get(BASE, () =>
    HttpResponse.json({ detail: "boom" }, { status: statusCode }),
  );
}

/** GET detail — success. */
export function atrativoDetailSuccess(detail: AtrativoDetail = sampleAtrativoDetail) {
  return http.get(`${BASE}/:id`, () => HttpResponse.json(detail));
}

/** GET detail — error. */
export function atrativoDetailError(statusCode = 500) {
  return http.get(`${BASE}/:id`, () =>
    HttpResponse.json({ detail: "boom" }, { status: statusCode }),
  );
}

/** PATCH advance sub_state — success (200). */
export function atrativoAdvanceSuccess() {
  return http.patch(`${BASE}/:id/advance`, () =>
    HttpResponse.json({ status: "ok", sub_state: "contacts_found" }),
  );
}

/** PATCH descarte — success (200). */
export function atrativoDescarteSuccess() {
  return http.patch(`${BASE}/:id/descarte`, () =>
    HttpResponse.json({
      status: "ok",
      routing: "dlq",
    }),
  );
}

/** Catch-all 401 for any atrativos route (session-expired path). */
export function atrativosUnauthorized() {
  const unauth = () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 });
  return [http.all(BASE, unauth), http.all(`${BASE}/*`, unauth)];
}

/** Default barrel: list+detail+mutations success. */
export const atrativoHandlers = [
  atrativosListSuccess(),
  atrativoDetailSuccess(),
  atrativoAdvanceSuccess(),
  atrativoDescarteSuccess(),
];
