import { http, HttpResponse } from "msw";

import type { MarReadyItem } from "@/lib/mar-ready-api";

/**
 * MSW handlers for the Mar-Ready slice (Phase 11, offline test harness).
 *
 * The browser client (`apiFetch`) calls the BFF at a RELATIVE `/api/...` URL,
 * and the BFF mount maps `/api/<rest>` → FastAPI `/<rest>`. So to reach
 * FastAPI's `/api/v1/atrativos/mar-ready` the browser actually requests
 * `/api/api/v1/atrativos/mar-ready`. MSW intercepts the browser-facing URL —
 * in jsdom the relative path resolves against `http://localhost:3000`, so we
 * match the absolute form.
 *
 * CRITICAL: double-prefix is mandatory — Pitfall 5.
 * BASE = "http://localhost:3000/api/api/v1/atrativos"
 *                               ^^^^ BFF mount   ^^^^ FastAPI router prefix
 */

const BASE = "http://localhost:3000/api/api/v1/atrativos";

export const sampleMarReadyItems: MarReadyItem[] = [
  {
    id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    canonical_key: "tripadvisor:attraction:12345",
    uf: "BA",
    score: 67.0,
    source: "tripadvisor",
  },
  {
    id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    canonical_key: "tripadvisor:attraction:67890",
    uf: "RJ",
    score: 72.5,
    source: "tripadvisor",
  },
];

/** GET /mar-ready — success. */
export function marReadyList(items: MarReadyItem[] = sampleMarReadyItems) {
  return http.get(`${BASE}/mar-ready`, ({ request }) => {
    const url = new URL(request.url);
    const uf = url.searchParams.get("uf");
    const filtered = uf ? items.filter((i) => i.uf === uf) : items;
    return HttpResponse.json(filtered);
  });
}

/** GET /mar-ready — empty list. */
export function marReadyListEmpty() {
  return http.get(`${BASE}/mar-ready`, () => HttpResponse.json([]));
}

/** GET /mar-ready — error. */
export function marReadyListError(statusCode = 500) {
  return http.get(`${BASE}/mar-ready`, () =>
    HttpResponse.json({ detail: "boom" }, { status: statusCode }),
  );
}

/** PATCH /atrativos/:id/promote — success (202). */
export function promoteSuccess(rioId?: string) {
  return http.patch(`${BASE}/:id/promote`, ({ params }) => {
    const id = typeof params.id === "string" ? params.id : params.id[0];
    return HttpResponse.json(
      { status: "accepted", rio_id: rioId ?? id, routing: "mar" },
      { status: 202 },
    );
  });
}

/** PATCH /atrativos/:id/promote — 409 (not mar_ready). */
export function promoteFailure() {
  return http.patch(`${BASE}/:id/promote`, () =>
    HttpResponse.json(
      { detail: "RioRecord is not mar_ready" },
      { status: 409 },
    ),
  );
}

/** POST /atrativos/promote-batch — success (202). */
export function promoteBatchSuccess(promoted = 2) {
  return http.post(`${BASE}/promote-batch`, ({ request }) => {
    const url = new URL(request.url);
    const uf = url.searchParams.get("uf") ?? "";
    return HttpResponse.json(
      { status: "accepted", uf, promoted },
      { status: 202 },
    );
  });
}

/** Default barrel: list + single promote + batch promote success. */
export const marReadyHandlers = [
  marReadyList(),
  promoteSuccess(),
  promoteBatchSuccess(),
];
