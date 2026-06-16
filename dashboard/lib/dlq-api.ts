/**
 * DLQ data layer (DASH-01).
 *
 * Query keys + typed fetchers for the DLQ slice. Every call goes through the BFF
 * via `apiFetch` (relative `/api/...`, operator Bearer attached) — never to
 * FastAPI directly. Mutations call the EXISTING dlq.py endpoints (no new
 * mutations are introduced by the dashboard).
 *
 * Backing endpoints (brave/api/routers/dlq.py + dashboard.py):
 *   GET   /api/v1/dlq?uf&entity_type&limit              — list
 *   GET   /api/v1/dlq/{rio_id}                          — detail (dashboard.py)
 *   PATCH /api/v1/dlq/{rio_id}/validate                 — validar e publicar
 *   PATCH /api/v1/dlq/{rio_id}/descarte                 — rejeitar → descarte
 *   PATCH /api/v1/dlq/{rio_id}/reprocess                — reprocessar
 *   POST  /api/v1/dlq/validate-batch?uf&entity_type&limit — validar lote por estado
 */

import { apiFetch } from "@/lib/api-client";

/** UI-SPEC D-06 ordering: steward-priority states first, then alphabetical rest. */
export const UF_PRIORITY = ["BA", "RJ", "SP", "SC", "CE", "PE"] as const;

export type Routing = "mar" | "dlq" | "descarte";

/** A single row in the DLQ master list (GET /api/v1/dlq). */
export interface DlqListItem {
  id: string;
  nascente_id: string;
  entity_type: string;
  uf: string | null;
  routing: string;
  dlq_reason: string | null;
  score: number | null;
  score_version: string | null;
  canonical_key: string | null;
}

export interface WhatsAppLogEntry {
  id: string;
  action: string;
  actor: string | null;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  created_at: string | null;
}

/** Full DLQ detail (GET /api/v1/dlq/{rio_id}, dashboard.py). */
export interface DlqDetail {
  id: string;
  routing: string;
  sub_state: string | null;
  dlq_reason: string | null;
  score: number | null;
  score_version: string | null;
  score_breakdown: Record<string, unknown>;
  normalized: Record<string, unknown>;
  nascente_payload: Record<string, unknown>;
  signals: Record<string, unknown>;
  whatsapp_log: WhatsAppLogEntry[];
}

/** TanStack query keys — all DLQ keys share the ['dlq'] prefix so a single
 *  `invalidateQueries(['dlq'])` after a mutation refetches the list + detail. */
export const dlqKeys = {
  all: ["dlq"] as const,
  list: (uf?: string, entityType?: string) =>
    ["dlq", "list", { uf: uf ?? null, entityType: entityType ?? null }] as const,
  detail: (rioId: string) => ["dlq", "detail", rioId] as const,
};

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function fetchDlqList(
  uf?: string,
  entityType?: string,
  limit = 50,
): Promise<DlqListItem[]> {
  return apiFetch<DlqListItem[]>(
    `api/v1/dlq${qs({ uf, entity_type: entityType, limit })}`,
  );
}

export function fetchDlqDetail(rioId: string): Promise<DlqDetail> {
  return apiFetch<DlqDetail>(`api/v1/dlq/${rioId}`);
}

export interface MutationResult {
  status: string;
  rio_id?: string;
  routing?: string;
}

export function validateDlqRecord(rioId: string): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/dlq/${rioId}/validate`, {
    method: "PATCH",
  });
}

export function descarteDlqRecord(rioId: string): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/dlq/${rioId}/descarte`, {
    method: "PATCH",
  });
}

export function reprocessDlqRecord(rioId: string): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/dlq/${rioId}/reprocess`, {
    method: "PATCH",
  });
}

export interface BatchResult {
  status: string;
  uf: string;
  validated: number;
}

export function validateDlqBatch(
  uf: string,
  entityType = "destination",
  limit = 100,
): Promise<BatchResult> {
  return apiFetch<BatchResult>(
    `api/v1/dlq/validate-batch${qs({ uf, entity_type: entityType, limit })}`,
    { method: "POST" },
  );
}
