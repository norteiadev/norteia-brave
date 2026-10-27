import { http, HttpResponse } from "msw";

import type {
  AtrativoDetail,
  AtrativoListItem,
  FailureCard,
  FailureCardLog,
  PromoteBulkDryRunResult,
  PromoteBulkRunResult,
} from "@/lib/atrativos-api";

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
// Failure Log endpoints live under the same double-prefix BFF mount.
const FAILURES_BASE = "http://localhost:3000/api/api/v1/failures";

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
    descricao_editorial:
      "No coração de Salvador, o Mercado Modelo reúne artesanato baiano, música e " +
      "sabores em um casarão histórico à beira da Baía de Todos-os-Santos.",
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
  // Append-only Brave pipeline timeline (RecordEvent rows, canonical_key-keyed).
  events: [
    {
      stage: "tripadvisor_synced",
      status: "ok",
      message: "Mercado Modelo",
      data: null,
      created_at: "2026-06-10T09:00:00Z",
    },
    {
      stage: "municipio_resolved",
      status: "ok",
      message: null,
      data: { ibge_code: "2927408" },
      created_at: "2026-06-10T09:00:01Z",
    },
    {
      stage: "parent_destino_linked",
      status: "ok",
      message: null,
      data: { parent_rio_id: "11111111-1111-1111-1111-111111111111" },
      created_at: "2026-06-10T09:00:02Z",
    },
    {
      stage: "validated",
      status: "ok",
      message: null,
      data: null,
      created_at: "2026-06-10T09:00:03Z",
    },
    {
      stage: "ingested",
      status: "ok",
      message: null,
      data: null,
      created_at: "2026-06-10T09:00:04Z",
    },
    {
      stage: "scored",
      status: "ok",
      message: null,
      data: { score: 61.0, score_version: "v1" },
      created_at: "2026-06-10T09:00:05Z",
    },
    {
      stage: "routed",
      status: "ok",
      message: null,
      data: { routing: "in_progress", dlq_reason: null },
      created_at: "2026-06-10T09:00:06Z",
    },
  ],
  dlq_reason: null,
  source: "tripadvisor",
  processed_at: "2026-06-10T09:00:06Z",
  score_version: "v1",
};

/**
 * GET list — envelope totals keyed by `?routing=` (the `limit=1` count queries
 * behind the sidebar badges). A routing absent from `totals` counts 0.
 */
export function atrativosCountByRouting(totals: Record<string, number>) {
  return http.get(BASE, ({ request }) => {
    const routing = new URL(request.url).searchParams.get("routing") ?? "";
    return HttpResponse.json({ items: [], total: totals[routing] ?? 0, offset: 0, limit: 1 });
  });
}

/**
 * GET list — server-side `?routing=` / `?uf=` filters plus `offset`/`limit`
 * paging over `items`, with the envelope `total` of the filtered set (the
 * Registros tabs and their "N de TOTAL").
 */
export function atrativosByRouting(items: AtrativoListItem[]) {
  return http.get(BASE, ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const routing = sp.get("routing");
    const uf = sp.get("uf");
    const offset = Number(sp.get("offset") ?? 0);
    const limit = Number(sp.get("limit") ?? 50);
    const filtered = items.filter(
      (i) => (!routing || i.routing === routing) && (!uf || i.uf === uf),
    );
    return HttpResponse.json({
      items: filtered.slice(offset, offset + limit),
      total: filtered.length,
      offset,
      limit,
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

/** PATCH edit — success (200, { status: "ok" }). */
export function atrativoEditSuccess() {
  return http.patch(`${BASE}/:id/edit`, () =>
    HttpResponse.json({ status: "ok" }),
  );
}

/** POST promote-bulk — dry-run counts. Same URL as the real run: tests swap
 *  handlers with `server.use()` between the two requests. */
export function promoteBulkDryRunSuccess(
  overrides?: Partial<PromoteBulkDryRunResult>,
) {
  return http.post(`${BASE}/promote-bulk`, () =>
    HttpResponse.json({
      candidates: 12,
      excluded: { below_score: 3, no_description: 2, recency: 1 },
      would_promote: 12,
      ...overrides,
    } satisfies PromoteBulkDryRunResult),
  );
}

/** POST promote-bulk — real run result. */
export function promoteBulkRunSuccess(
  overrides?: Partial<PromoteBulkRunResult>,
) {
  return http.post(`${BASE}/promote-bulk`, () =>
    HttpResponse.json({
      batch_id: "batch-1",
      promoted: 10,
      held: [],
      failed: [],
      push_failed: [],
      remaining: 0,
      ...overrides,
    } satisfies PromoteBulkRunResult),
  );
}

/** POST promote-bulk — dry-run with nothing eligible. */
export function promoteBulkZeroCandidates() {
  return promoteBulkDryRunSuccess({
    candidates: 0,
    excluded: { below_score: 0, no_description: 0, recency: 0 },
    would_promote: 0,
  });
}

/**
 * Sample Falha-column cards (GET /api/v1/failures/cards) — a bare JSON list.
 * Carries the REAL atrativo identity (name/uf) instead of an opaque task_name.
 */
export const sampleFailureCards: FailureCard[] = [
  {
    source_ref: "tripadvisor:attraction:99999",
    name: "Praia Do Bosque",
    uf: "ES",
    entity_type: "attraction",
    last_stage: "quarantined",
    error: "ibge_unmatched: 'Praia Do Bosque'",
    quarantined_at: "2026-06-30T12:00:00Z",
  },
];

/**
 * Sample failure log (GET /api/v1/failures/cards/log?source_ref=…) — a single
 * terminal quarantine event (status=fail) + accumulated identity. LGPD: only
 * público-geo + engineering fields (locationId/name/uf/reason).
 */
export const sampleFailureCardLog: FailureCardLog = {
  events: [
    {
      stage: "quarantined",
      status: "fail",
      message: "ibge_unmatched: 'Praia Do Bosque'",
      data: {
        locationId: "99999",
        name: "Praia Do Bosque",
        uf: "ES",
        reason: "ibge_unmatched",
      },
      created_at: "2026-06-30T12:00:00Z",
    },
  ],
  identity: {
    name: "Praia Do Bosque",
    uf: "ES",
    entity_type: "attraction",
    last_error: "ibge_unmatched: 'Praia Do Bosque'",
  },
};

/** GET /failures/cards — success (bare list). */
export function failureCardsSuccess(cards: FailureCard[] = sampleFailureCards) {
  return http.get(`${FAILURES_BASE}/cards`, () => HttpResponse.json(cards));
}

/** GET /failures/cards/log — success (events + identity). */
export function failureCardLogSuccess(log: FailureCardLog = sampleFailureCardLog) {
  return http.get(`${FAILURES_BASE}/cards/log`, () => HttpResponse.json(log));
}

/** Catch-all 401 for any atrativos route (session-expired path). */
export function atrativosUnauthorized() {
  const unauth = () =>
    HttpResponse.json({ detail: "Unauthorized" }, { status: 401 });
  return [http.all(BASE, unauth), http.all(`${BASE}/*`, unauth)];
}

/** Default barrel: list+detail+mutations success. */
export const atrativoHandlers = [
  atrativoDetailSuccess(),
  atrativoAdvanceSuccess(),
  atrativoDescarteSuccess(),
  atrativoEditSuccess(),
];
