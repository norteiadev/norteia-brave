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

import { ApiError, apiFetch } from "@/lib/api-client";

/** UI-SPEC D-06 ordering: steward-priority states first, then alphabetical rest. */
export const UF_PRIORITY = ["BA", "RJ", "SP", "SC", "CE", "PE"] as const;

export type Routing = "mar" | "dlq" | "descarte";

/** A single row in the DLQ master list (GET /api/v1/dlq). */
export interface DlqListItem {
  id: string;
  nascente_id: string;
  entity_type: string;
  uf: string | null;
  // público-geo identity for the Revisão table (may be null on legacy rows).
  name: string | null;
  municipio: string | null;
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

// ---------------------------------------------------------------------------
// Manual DLQ→WhatsApp move (atrativos) — Phase H, POST /api/v1/dlq/whatsapp-batch
// ---------------------------------------------------------------------------

/**
 * Result of an accepted DLQ→WhatsApp batch (HTTP 202). `moved` = the total moved
 * off DLQ; it splits into `outreach` (a WhatsApp number was already captured →
 * conversa iniciada) and `discovery` (no number → LLM number-discovery kicked off).
 */
export interface WhatsAppBatchResult {
  status: string;
  moved: number;
  outreach: number;
  discovery: number;
}

/** One ineligible record from the atomic 422 breakdown. */
export interface WhatsAppIneligibleItem {
  rio_id: string;
  reason: string;
}

/** The structured 422 body: `{ error: "ineligible_records", ineligible: [...] }`. */
export interface WhatsAppIneligibleDetail {
  error: "ineligible_records";
  ineligible: WhatsAppIneligibleItem[];
}

/** PT-BR copy for each server-side ineligibility reason. */
export const WHATSAPP_INELIGIBLE_REASONS: Record<string, string> = {
  not_found: "não encontrado",
  not_attraction: "não é atrativo",
  not_in_dlq: "não está na DLQ",
  already_in_whatsapp: "já em WhatsApp",
  has_horario_or_preco: "já tem horário/preço",
};

/**
 * Manually move DLQ atrativos into the WhatsApp column (the single Phase-F entry).
 *
 * Body is `{ rio_ids }`. The move is ATOMIC: if ANY id is ineligible or invalid
 * the whole request 422s (nothing moved) with a per-item breakdown on
 * `ApiError.detail` (an OBJECT, surfaced via `ineligibleFrom`). Auth-before-lock:
 * 401 for an unauthenticated caller, then 423 while the Motor is LIGADO.
 */
export function moveDlqToWhatsApp(rioIds: string[]): Promise<WhatsAppBatchResult> {
  return apiFetch<WhatsAppBatchResult>("api/v1/dlq/whatsapp-batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rio_ids: rioIds }),
  });
}

/**
 * Extract the per-item ineligibility breakdown from a batch error, or null when
 * the error is not the structured `ineligible_records` 422 (e.g. 401/423/500 or
 * the pydantic empty-list 422 whose detail is an array).
 */
export function ineligibleFrom(err: unknown): WhatsAppIneligibleItem[] | null {
  if (err instanceof ApiError && err.status === 422) {
    const detail = err.detail as WhatsAppIneligibleDetail | undefined;
    if (
      detail &&
      typeof detail === "object" &&
      detail.error === "ineligible_records" &&
      Array.isArray(detail.ineligible)
    ) {
      return detail.ineligible;
    }
  }
  return null;
}
