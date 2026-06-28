/**
 * Nascente data layer (Painel board — Nascente column).
 *
 * Query keys + typed fetcher for the raw Nascente list. The Nascente column
 * surfaces the immutable raw-payload layer as READ-ONLY cards (no mutations,
 * no drag): nascente → rio is automatic and immediate, so a nascente card is
 * purely a "what the motor just ingested" view.
 *
 * Backing endpoint (brave/api/routers/engine.py):
 *   GET /api/v1/nascente — paginated list (current versions only, newest first)
 *
 * Every call goes through the BFF via `apiFetch` (relative `/api/...`, operator
 * Bearer attached) — never to FastAPI directly.
 */

import { apiFetch } from "@/lib/api-client";

/** A single row in the Nascente list (GET /api/v1/nascente). */
export interface NascenteListItem {
  id: string;
  entity_type: string; // "destination" | "attraction"
  uf: string | null;
  source: string | null;
  name: string | null;
  ingested_at: string | null;
}

/** TanStack query keys — share the ['nascente'] prefix for bulk invalidation. */
export const nascenteKeys = {
  all: ["nascente"] as const,
  list: (filters: Record<string, unknown>) =>
    ["nascente", "list", filters] as const,
};

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function fetchNascenteList(params: {
  uf?: string;
  entity_type?: string;
  offset?: number;
  limit?: number;
}): Promise<{
  items: NascenteListItem[];
  total: number;
  offset: number;
  limit: number;
}> {
  return apiFetch(
    `api/v1/nascente${qs({
      uf: params.uf,
      entity_type: params.entity_type,
      offset: params.offset,
      limit: params.limit,
    })}`,
  );
}
