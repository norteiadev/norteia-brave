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
 *   PATCH /api/v1/atrativos/{id}/edit               — edit canonical fields (200)
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
  source: string | null; // Nascente origin: tripadvisor | ibge
  municipio?: string | null; // público-geo município nome (resolved at ingest)
  municipio_id?: string | null; // IBGE code
  validation_pending: boolean; // sub_state === 'aguardando_consulta_whatsapp'
  mar_id: string | null;
  parent_mar_id: string | null;
  contacts_summary: {
    phone_masked: string | null; // NEVER phone_e164 — already masked by backend
    website: string | null;
  } | null;
  /**
   * Eligible for the manual DLQ→WhatsApp move (Phase H) — true iff the atrativo
   * has NO horário AND NO preço (server rule `_is_whatsapp_eligible`). OPTIONAL:
   * the list endpoint may omit it; absent is treated as eligible client-side and
   * the batch endpoint's atomic 422 remains the authoritative gate.
   */
  whatsapp_eligible?: boolean;
}

/** A single audit log row from the AuditLog table. */
export interface AuditLogRow {
  action: string;
  actor: string | null;
  after_state: Record<string, unknown> | null;
  created_at: string | null;
}

/**
 * One append-only pipeline timeline event (RecordEvent table). Emitted at every
 * Brave stage — success, skip, AND failure (quarantine). LGPD: `data` only ever
 * carries público-geo + engineering fields (score, routing, dlq_reason, ibge
 * motivo, name/uf, locationId) — NEVER phone/PII/review text.
 */
export interface RecordEvent {
  stage: string;
  status: "ok" | "fail" | "skip";
  message: string | null;
  data: Record<string, unknown> | null;
  created_at: string | null;
}

/** Full Atrativo detail (GET /api/v1/atrativos/{id}). */
export interface AtrativoDetail extends AtrativoListItem {
  score_breakdown: Record<string, unknown>;
  normalized: Record<string, unknown>; // _safe_normalized applied by backend — no phone_e164
  audit_log: AuditLogRow[];
  parent_destino: { mar_id: string; name: string } | null;
  /** Append-only pipeline timeline (RecordEvent rows keyed on canonical_key). */
  events: RecordEvent[];
  /** DLQ routing reason (populated when routed to DLQ), else null. */
  dlq_reason: string | null;
  /** Ingest source of the backing Nascente record (rio.nascente.source). */
  source: string | null;
  /** ISO timestamp the record was last processed, else null. */
  processed_at: string | null;
  /** reliability score-engine version tag, else null. */
  score_version: string | null;
}

/**
 * One Falha-column card sourced from the RecordEvent fail-timeline
 * (GET /api/v1/failures/cards). Unlike the legacy PoisonQuarantine FailureItem,
 * this carries the REAL atrativo identity (name/uf) instead of the opaque
 * task_name, plus the universal `source_ref` drawer key.
 */
export interface FailureCard {
  source_ref: string;
  name: string | null;
  uf: string | null;
  entity_type: string | null;
  last_stage: string;
  error: string | null;
  quarantined_at: string | null;
}

/** Accumulated identity for a failure card's Log tab (no PII). */
export interface FailureCardLogIdentity {
  name: string | null;
  uf: string | null;
  entity_type: string | null;
  last_error: string | null;
}

/**
 * Log payload for a Falha card without a Rio row
 * (GET /api/v1/failures/cards/log?source_ref=…): the append-only event timeline
 * plus an accumulated identity block. Returns empty events + null identity when
 * no events and no matching legacy poison row exist (HTTP 200, never 404).
 */
export interface FailureCardLog {
  events: RecordEvent[];
  identity: FailureCardLogIdentity;
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

/**
 * Load the Falha-column cards from the RecordEvent fail-timeline
 * (GET /api/v1/failures/cards). Returns a bare JSON list (not an envelope).
 * These feed the Painel Falha lane with real name/uf identity.
 */
export function fetchFailureCards(): Promise<FailureCard[]> {
  return apiFetch<FailureCard[]>("api/v1/failures/cards");
}

/**
 * Load the append-only Log timeline for a Falha card that has no Rio row
 * (GET /api/v1/failures/cards/log?source_ref=…). Always 200 — empty events +
 * null identity when the source_ref is unknown.
 */
export function fetchFailureCardLog(sourceRef: string): Promise<FailureCardLog> {
  return apiFetch<FailureCardLog>(
    `api/v1/failures/cards/log${qs({ source_ref: sourceRef })}`,
  );
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

/** Edit canonical fields on an atrativo's normalized payload (D-04, T-08-05).
 *  Merges `fields` into rio.normalized server-side; returns { status: "ok" }.
 *  The backend strips phone_e164 — callers should not send PII fields. */
export function editAtrativo(
  id: string,
  fields: Record<string, unknown>,
): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/atrativos/${id}/edit`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields }),
  });
}
