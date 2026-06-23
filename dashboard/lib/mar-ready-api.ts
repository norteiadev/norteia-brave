/**
 * Mar-Ready data layer (Phase 11, TA-06 / TA-07).
 *
 * Query keys + typed fetchers for the Mar-Ready slice. Every call goes through
 * the BFF via `apiFetch` (relative `/api/...`, operator Bearer attached) — never
 * to FastAPI directly.
 *
 * Backing endpoints (brave/api/routers/atrativos.py):
 *   GET   /api/v1/atrativos/mar-ready            — list TripAdvisor attractions ready for Mar promotion
 *   PATCH /api/v1/atrativos/{rio_id}/promote      — promote single attraction to Mar
 *   POST  /api/v1/atrativos/promote-batch         — batch promote by UF
 */

import { apiFetch } from "@/lib/api-client";

/** A single row in the Mar-Ready list (GET /api/v1/atrativos/mar-ready). */
export interface MarReadyItem {
  id: string;
  canonical_key: string;
  uf: string;
  score: number;
  source: string;
}

/** TanStack query keys — all mar-ready keys share the ['mar-ready'] prefix so a single
 *  `invalidateQueries(['mar-ready'])` after a mutation refetches the list. */
export const marReadyKeys = {
  all: ["mar-ready"] as const,
  list: (uf?: string) =>
    ["mar-ready", "list", { uf: uf ?? null }] as const,
};

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function fetchMarReadyList(uf?: string): Promise<MarReadyItem[]> {
  return apiFetch<MarReadyItem[]>(
    `api/v1/atrativos/mar-ready${qs({ uf })}`,
  );
}

export interface PromoteResult {
  status: string;
  rio_id: string;
  routing: string;
}

export function promoteAtrativo(rioId: string): Promise<PromoteResult> {
  return apiFetch<PromoteResult>(`api/v1/atrativos/${rioId}/promote`, {
    method: "PATCH",
  });
}

export interface BatchPromoteResult {
  status: string;
  uf: string;
  promoted: number;
}

export function promoteAtrativoBatch(
  ufs: string[],
  limit = 100,
): Promise<BatchPromoteResult> {
  const uf = ufs[0] ?? "";
  return apiFetch<BatchPromoteResult>(
    `api/v1/atrativos/promote-batch${qs({ uf, limit })}`,
    { method: "POST" },
  );
}
