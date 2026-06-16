import { http, HttpResponse } from "msw";

import type { GateQueueItem, RampQualityContext } from "@/lib/gate-api";

/**
 * MSW handlers for the WhatsApp gate slice (DASH-03, D-07 offline harness).
 *
 * Same BFF double-prefix convention as the DLQ handlers: the browser client
 * (`apiFetch`) calls a RELATIVE `/api/...` URL, and the BFF maps `/api/<rest>` →
 * FastAPI `/<rest>`, so reaching FastAPI's `/api/v1/atrativos/gate` means the
 * browser requests `/api/api/v1/atrativos/gate`. In jsdom the relative path
 * resolves against `http://localhost:3000`, so we match the absolute form.
 *
 * LGPD (T-04-18): the sample rows carry ONLY a pre-masked `phone_masked` field.
 * No raw `phone_e164` ever appears in any handler — mirroring the production
 * server contract (the backend masks before the boundary). The GateQueue test
 * asserts no raw full number renders.
 */

const GATE = "http://localhost:3000/api/api/v1/atrativos/gate";
const RAMP = "http://localhost:3000/api/api/v1/atrativos/whatsapp/ramp-context";

export const sampleGateItems: GateQueueItem[] = [
  {
    rio_id: "aaaa1111-1111-1111-1111-111111111111",
    nascente_id: "bbbb1111-1111-1111-1111-111111111111",
    entity_type: "attraction",
    uf: "BA",
    sub_state: "aguardando_consulta_whatsapp",
    routing: "rio",
    dlq_reason: null,
    score: 78.5,
    score_version: "v1",
    canonical_key: "ba:salvador:farol-da-barra",
    normalized: {
      name: "Farol da Barra",
      uf: "BA",
      municipality: "Salvador",
      // Pre-masked phone ONLY — never a raw e164 (LGPD T-04-18).
      phone_masked: "+55 71 9••••-••42",
      score_breakdown: {
        origem: 80,
        completude: 70,
        corroboracao: 60,
        atualidade: 90,
        validacao_humana: 0,
      },
    },
  },
  {
    rio_id: "aaaa2222-2222-2222-2222-222222222222",
    nascente_id: "bbbb2222-2222-2222-2222-222222222222",
    entity_type: "attraction",
    uf: "RJ",
    sub_state: "aguardando_consulta_whatsapp",
    routing: "rio",
    dlq_reason: null,
    score: 71.0,
    score_version: "v1",
    canonical_key: "rj:rio:pao-de-acucar",
    normalized: {
      name: "Pão de Açúcar",
      uf: "RJ",
      municipality: "Rio de Janeiro",
      phone_masked: "+55 21 9••••-••07",
      score_breakdown: {
        origem: 70,
        completude: 65,
        corroboracao: 55,
        atualidade: 85,
        validacao_humana: 0,
      },
    },
  },
];

export const sampleRampContext: RampQualityContext = {
  quality_rating: "GREEN",
  ramp_remaining: 120,
  ramp_cap: 250,
  ramp_used: 130,
  paused: false,
};

/** GET gate queue — success (filters by uf like the real endpoint). */
export function gateListSuccess(items: GateQueueItem[] = sampleGateItems) {
  return http.get(GATE, ({ request }) => {
    const url = new URL(request.url);
    const uf = url.searchParams.get("uf");
    const filtered = uf ? items.filter((i) => i.uf === uf) : items;
    return HttpResponse.json(filtered);
  });
}

export function gateListEmpty() {
  return http.get(GATE, () => HttpResponse.json([]));
}

export function gateListError(status = 500) {
  return http.get(GATE, () => HttpResponse.json({ detail: "boom" }, { status }));
}

/** PATCH approve — 202 accepted (outreach enqueued). */
export function gateApproveSuccess() {
  return http.patch(`${GATE}/:rioId/approve`, ({ params }) =>
    HttpResponse.json(
      { status: "accepted", rio_id: String(params.rioId) },
      { status: 202 },
    ),
  );
}

/** PATCH reject — 200 ok (routed to dlq). */
export function gateRejectSuccess() {
  return http.patch(`${GATE}/:rioId/reject`, ({ params }) =>
    HttpResponse.json({
      status: "ok",
      routing: "dlq",
      rio_id: String(params.rioId),
    }),
  );
}

/** Ramp/quality context — success. */
export function rampContextSuccess(ctx: RampQualityContext = sampleRampContext) {
  return http.get(RAMP, () => HttpResponse.json(ctx));
}

export function rampContextRed() {
  return rampContextSuccess({
    quality_rating: "RED",
    ramp_remaining: 0,
    ramp_cap: 250,
    ramp_used: 250,
    paused: true,
  });
}

export function rampContextError(status = 500) {
  return http.get(RAMP, () => HttpResponse.json({ detail: "boom" }, { status }));
}

/** Catch-all 401 for any gate route (session-expired path) — covers the bare
 *  queue endpoint, the per-record approve/reject sub-paths, and ramp context. */
export function gateUnauthorized() {
  const unauth = () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 });
  return [
    http.all(GATE, unauth),
    http.all(`${GATE}/*`, unauth),
    http.all(RAMP, unauth),
  ];
}

/** Default barrel: queue + ramp + mutations success (suites override per state). */
export const gateHandlers = [
  gateListSuccess(),
  rampContextSuccess(),
  gateApproveSuccess(),
  gateRejectSuccess(),
];
