/**
 * WhatsApp gate data layer (DASH-03).
 *
 * Query keys + typed fetchers for the gate slice. Every call goes through the BFF
 * via `apiFetch` (relative `/api/...`, operator Bearer attached) — never to
 * FastAPI directly. Mutations call the EXISTING atrativos_gate.py endpoints (no
 * new mutations are introduced by the dashboard — Phase 3 owns the gate router).
 *
 * Backing endpoints (brave/api/routers/atrativos_gate.py):
 *   GET   /api/v1/atrativos/gate?uf&limit               — list aguardando queue
 *   PATCH /api/v1/atrativos/gate/{rio_id}/approve        — approve → outreach enqueued
 *   PATCH /api/v1/atrativos/gate/{rio_id}/reject         — reject → dlq/descarte
 *
 * The gate GET returns the FULL row (normalized, score, etc.) — there is no
 * separate detail endpoint, so the row itself feeds the GateReviewPanel.
 *
 * LGPD (RESEARCH §3 R3 / T-04-18): the backend masks `phone_e164` server-side.
 * The UI receives only `phone_masked` (already minimized) and MUST NOT reconstruct
 * a raw E.164 number. We deliberately do NOT model a raw-phone field on the type.
 */

import { apiFetch } from "@/lib/api-client";

/** UI-SPEC D-06 ordering: steward-priority states first, then the rest. */
export const UF_PRIORITY = ["BA", "RJ", "SP", "SC", "CE", "PE"] as const;

/** The gate sub_state the queue is scoped to (Phase 3 WhatsApp gate). */
export const GATE_SUB_STATE = "aguardando_consulta_whatsapp";

/**
 * A single row in the gate queue (GET /api/v1/atrativos/gate).
 *
 * Mirrors the atrativos_gate.py response shape. `normalized` is the Rio
 * normalized payload — any phone inside it is already masked server-side; the UI
 * surfaces it via `maskedPhoneFrom` and never reconstructs the raw number.
 */
export interface GateQueueItem {
  rio_id: string;
  nascente_id: string;
  entity_type: string;
  uf: string | null;
  sub_state: string | null;
  routing: string;
  dlq_reason: string | null;
  score: number | null;
  score_version: string | null;
  canonical_key: string | null;
  normalized: Record<string, unknown>;
}

/**
 * Ramp + WhatsApp quality-rating context (Phase 3 send-path state).
 *
 * `quality_rating` drives the destructive RED treatment in the UI (UI-SPEC):
 * GREEN = healthy, AMBER/YELLOW = throttle, RED = auto-pause (sends blocked
 * server-side). `ramp_remaining` is the remaining volume-ramp cap the operator
 * sees before approving outreach. The ramp is ENFORCED in the Phase 3 send path,
 * not the UI (T-04-20); the UI only displays it.
 */
export interface RampQualityContext {
  quality_rating: "GREEN" | "AMBER" | "YELLOW" | "RED" | (string & {});
  ramp_remaining: number | null;
  ramp_cap: number | null;
  ramp_used: number | null;
  paused: boolean;
}

/** TanStack query keys — all gate keys share the ['gate'] prefix so a single
 *  `invalidateQueries(['gate'])` after a mutation refetches the queue + context. */
export const gateKeys = {
  all: ["gate"] as const,
  list: (uf?: string) => ["gate", "list", { uf: uf ?? null }] as const,
  context: () => ["gate", "context"] as const,
};

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function fetchGateQueue(uf?: string, limit = 50): Promise<GateQueueItem[]> {
  return apiFetch<GateQueueItem[]>(
    `api/v1/atrativos/gate${qs({ uf, limit })}`,
  );
}

export function fetchRampContext(): Promise<RampQualityContext> {
  return apiFetch<RampQualityContext>("api/v1/atrativos/whatsapp/ramp-context");
}

export interface GateMutationResult {
  status: string;
  rio_id?: string;
  routing?: string;
}

export function approveGate(rioId: string): Promise<GateMutationResult> {
  return apiFetch<GateMutationResult>(
    `api/v1/atrativos/gate/${rioId}/approve`,
    { method: "PATCH" },
  );
}

export function rejectGate(rioId: string): Promise<GateMutationResult> {
  return apiFetch<GateMutationResult>(
    `api/v1/atrativos/gate/${rioId}/reject`,
    { method: "PATCH" },
  );
}

/**
 * Extract an ALREADY-MASKED phone for display from a normalized payload.
 *
 * LGPD minimization (T-04-18): we read ONLY pre-masked fields the backend emits
 * (`phone_masked` / `telefone_minimizado`). We NEVER read `phone_e164` and never
 * reconstruct a raw number from parts — if no masked field is present we return
 * null and the UI shows nothing. The label in the UI is "telefone (minimizado)".
 */
export function maskedPhoneFrom(
  normalized: Record<string, unknown> | null | undefined,
): string | null {
  if (!normalized) return null;
  const candidate =
    normalized["phone_masked"] ??
    normalized["telefone_minimizado"] ??
    normalized["phone_minimized"];
  return typeof candidate === "string" && candidate.length > 0
    ? candidate
    : null;
}
