/**
 * Destinos data layer (D-03).
 *
 * Query keys + typed fetchers for the Destinos CMS slice. Every call goes
 * through the BFF via `apiFetch` (relative `/api/...`, operator Bearer
 * attached) — never to FastAPI directly.
 *
 * Backing endpoints (brave/api/routers/cms.py):
 *   GET   /api/v1/destinos                         — paginated list
 *   GET   /api/v1/destinos/{id}                    — full detail
 *   PATCH /api/v1/destinos/{id}/promote            — promote → Mar (202)
 *   PATCH /api/v1/destinos/{id}/descarte           — reject → descarte
 *   PATCH /api/v1/destinos/{id}/reprocess          — trigger reprocess (202)
 *   PATCH /api/v1/destinos/{id}/edit               — edit canonical fields (200)
 */

import { apiFetch } from "@/lib/api-client";

/** A single row in the Destinos CMS list (GET /api/v1/destinos). */
export interface DestinoListItem {
  id: string;
  entity_type: string;
  uf: string | null;
  routing: string;
  score: number | null;
  name: string | null;
  source: string | null; // Nascente origin: tripadvisor | ibge | mtur
  canonical_key: string | null;
  municipio?: string | null; // público-geo município nome (resolved at ingest)
  municipio_id?: string | null; // IBGE code
  validation_pending: boolean;
  mar_id: string | null;
  published_at: string | null;
}

/** A single audit log row from the AuditLog table. */
export interface AuditLogRow {
  action: string;
  actor: string | null;
  after_state: Record<string, unknown> | null;
  created_at: string | null;
}

/** Full Destino detail (GET /api/v1/destinos/{id}). */
export interface DestinoDetail extends DestinoListItem {
  score_breakdown: Record<string, unknown>;
  normalized: Record<string, unknown>;
  source: string | null;
  audit_log: AuditLogRow[];
  child_atrativos: {
    total: number;
    by_sub_state: Record<string, number>;
  };
}

/** Generic mutation result. */
export interface MutationResult {
  status: string;
  rio_id?: string;
  routing?: string;
}

/** TanStack query keys — all Destino keys share the ['destinos'] prefix so a
 *  single `invalidateQueries(['destinos'])` after a mutation refetches list+detail. */
export const destinoKeys = {
  all: ["destinos"] as const,
  list: (filters: Record<string, unknown>) =>
    ["destinos", "list", filters] as const,
  detail: (id: string) => ["destinos", "detail", id] as const,
};

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function fetchDestinoList(params: {
  uf?: string;
  routing?: string;
  offset?: number;
  limit?: number;
}): Promise<{ items: DestinoListItem[]; total: number; offset: number; limit: number }> {
  return apiFetch(
    `api/v1/destinos${qs({ uf: params.uf, routing: params.routing, offset: params.offset, limit: params.limit })}`,
  );
}

export function fetchDestinoDetail(id: string): Promise<DestinoDetail> {
  return apiFetch<DestinoDetail>(`api/v1/destinos/${id}`);
}

export function promoteDestino(id: string): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/destinos/${id}/promote`, {
    method: "PATCH",
  });
}

export function descarteDestino(id: string): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/destinos/${id}/descarte`, {
    method: "PATCH",
  });
}

export function reprocessDestino(id: string): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/destinos/${id}/reprocess`, {
    method: "PATCH",
  });
}

/** Edit canonical fields on a destino's normalized payload (D-03, T-08-05).
 *  Merges `fields` into rio.normalized server-side; returns { status: "ok" }.
 *  The backend strips phone_e164 — callers should not send PII fields. */
export function editDestino(
  id: string,
  fields: Record<string, unknown>,
): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/destinos/${id}/edit`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields }),
  });
}
