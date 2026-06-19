/**
 * Atrativos data layer (D-04).
 *
 * Query keys + typed fetchers for the Atrativos CMS slice. Every call goes
 * through the BFF via `apiFetch` (relative `/api/...`, operator Bearer
 * attached) — never to FastAPI directly.
 *
 * Backing endpoints (brave/api/routers/cms.py):
 *   GET   /api/v1/atrativos                         — paginated list
 *   GET   /api/v1/atrativos/{id}                    — full detail
 *   PATCH /api/v1/atrativos/{id}/advance            — advance sub_state FSM
 *   PATCH /api/v1/atrativos/{id}/descarte           — reject → descarte
 *
 * PII contract: contacts_summary exposes phone_masked ONLY (never phone_e164).
 * The backend applies _safe_normalized before responding; this client never
 * re-exposes the raw field.
 */

import { apiFetch } from "@/lib/api-client";

/** A single row in the Atrativos CMS list (GET /api/v1/atrativos). */
export interface AtrativoListItem {
  id: string;
  entity_type: "attraction";
  uf: string | null;
  routing: string;
  sub_state: string | null;
  score: number | null;
  name: string | null;
  validation_pending: boolean; // sub_state === 'aguardando_consulta_whatsapp'
  mar_id: string | null;
  parent_mar_id: string | null;
  contacts_summary: {
    phone_masked: string | null; // NEVER phone_e164 — already masked by backend
    website: string | null;
  } | null;
}

/** A single audit log row from the AuditLog table. */
export interface AuditLogRow {
  action: string;
  actor: string | null;
  after_state: Record<string, unknown> | null;
  created_at: string | null;
}

/** Full Atrativo detail (GET /api/v1/atrativos/{id}). */
export interface AtrativoDetail extends AtrativoListItem {
  score_breakdown: Record<string, unknown>;
  normalized: Record<string, unknown>; // _safe_normalized applied by backend — no phone_e164
  audit_log: AuditLogRow[];
  parent_destino: { mar_id: string; name: string } | null;
}

/** Generic mutation result. */
export interface MutationResult {
  status: string;
  rio_id?: string;
  routing?: string;
  sub_state?: string;
}

/** TanStack query keys — all Atrativo keys share the ['atrativos'] prefix so a
 *  single `invalidateQueries(['atrativos'])` after a mutation refetches list+detail. */
export const atrativoKeys = {
  all: ["atrativos"] as const,
  list: (filters: Record<string, unknown>) =>
    ["atrativos", "list", filters] as const,
  detail: (id: string) => ["atrativos", "detail", id] as const,
};

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function fetchAtrativoList(params: {
  uf?: string;
  sub_state?: string;
  parent_mar_id?: string;
  routing?: string;
  offset?: number;
  limit?: number;
}): Promise<{ items: AtrativoListItem[]; total: number; offset: number; limit: number }> {
  return apiFetch(
    `api/v1/atrativos${qs({
      uf: params.uf,
      sub_state: params.sub_state,
      parent_mar_id: params.parent_mar_id,
      routing: params.routing,
      offset: params.offset,
      limit: params.limit,
    })}`,
  );
}

export function fetchAtrativoDetail(id: string): Promise<AtrativoDetail> {
  return apiFetch<AtrativoDetail>(`api/v1/atrativos/${id}`);
}

export function advanceAtrativo(
  id: string,
  body: { expected_state: string; next_state: string },
): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/atrativos/${id}/advance`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function descartarAtrativo(id: string): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/atrativos/${id}/descarte`, {
    method: "PATCH",
  });
}
