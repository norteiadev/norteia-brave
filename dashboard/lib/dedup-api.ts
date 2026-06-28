/**
 * Duplicados data layer (UI-PAINEL-2).
 *
 * Query keys + typed fetchers for the dedup "Duplicados" slice. Every call goes
 * through the BFF via `apiFetch` (relative `/api/...`, operator Bearer attached)
 * — never to FastAPI directly. This client + its MSW handler together ARE the
 * consumer contract (typed Pydantic ⇄ mirrored MSW handler — A5; no pact-js).
 *
 * Backing endpoints (brave/api/routers/dedup.py):
 *   GET   /api/v1/dedup/pairs?uf=                          — compute-on-read pairs
 *   PATCH /api/v1/dedup/pairs/{candidate_rio_id}/resolve   — merge | keep | discard
 *
 * Similarity is a Python compute-on-read placeholder (token overlap) because
 * RioRecord.embedding is a zero-stub (RESEARCH A1) — the pgvector cosine operator
 * is never invoked on this read path. `similarity_source` labels what the number
 * is so the operator knows the embedding score is a stand-in.
 */

import { apiFetch } from "@/lib/api-client";

/** A single diverged field: the canonical key + the candidate vs Mar values. */
export interface DedupDivergedField {
  field: string;
  candidate: unknown;
  mar: unknown;
}

/**
 * A single candidate↔Mar dedup pair (compute-on-read). Mirrors the backend
 * DedupPairItem Pydantic model field-for-field (extra="forbid").
 */
export interface DedupPairItem {
  candidate_id: string;
  mar_id: string;
  candidate_rio_id: string;
  mar_rio_id: string;
  uf: string;
  municipio: string | null;
  entity_type: string;
  similarity: number;
  similarity_source: string;
  matched_fields: string[];
  diverged_fields: DedupDivergedField[];
}

/** Paginated envelope for GET /api/v1/dedup/pairs (mirrors DedupPairsResponse). */
export interface DedupPairsResponse {
  items: DedupPairItem[];
  total: number;
  offset: number;
  limit: number;
}

/** The resolution action — mirrors the backend ResolveBody Literal. */
export type DedupAction = "merge" | "keep" | "discard";

/** Body for PATCH /api/v1/dedup/pairs/{id}/resolve (mirrors ResolveBody). */
export interface DedupResolveBody {
  action: DedupAction;
  mar_id: string;
}

/** Resolve response: { status, action } (mirrors resolve_pair return). */
export interface DedupResolveResult {
  status: string;
  action: DedupAction;
}

/** TanStack query keys — all dedup keys share the ['dedup'] prefix so a single
 *  `invalidateQueries(['dedup'])` after a resolve refetches the pair list. */
export const dedupKeys = {
  all: ["dedup"] as const,
  pairs: (uf?: string) => ["dedup", "pairs", { uf }] as const,
};

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/** List candidate↔Mar dedup pairs, optionally scoped to a single UF. */
export function fetchDedupPairs(uf?: string): Promise<DedupPairsResponse> {
  return apiFetch<DedupPairsResponse>(`api/v1/dedup/pairs${qs({ uf })}`);
}

/**
 * Resolve a dedup pair: merge (union into existing Mar), keep (suppress the
 * pair), or discard (route the candidate Rio → descarte). The backend requires
 * `mar_id` for every action; pass the pair's `mar_id`.
 */
export function resolveDedupPair(
  candidateRioId: string,
  body: DedupResolveBody,
): Promise<DedupResolveResult> {
  return apiFetch<DedupResolveResult>(
    `api/v1/dedup/pairs/${candidateRioId}/resolve`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}
