/**
 * Painel data layer (17-02, UI-PAINEL-1).
 *
 * The single tested module that shapes the existing destinos/atrativos list
 * endpoints into the Painel (Kanban) board: a unified `PainelCard` model, pure
 * React-free selectors, and two TanStack-Query hooks.
 *
 * Design notes (locked by 17-CONTEXT + 17-02-PLAN):
 *   - Cards come from the rio-backed list endpoints. nascente-only records are
 *     NOT surfaced by those lists, so the Nascente COLUMN shows a count (from
 *     engine counts) but has no draggable cards this slice.
 *   - Metrics read the server-side envelope `total` (NOT the loaded-array
 *     length) so "total no escopo" / "progresso %" cannot silently undercount.
 *     They reflect the WHOLE base; the UF scope filters the BOARD only.
 *   - LGPD: `toPainelCards` maps an explicit allow-list of list-safe fields.
 *     phone_e164 / phone_masked / contacts_summary NEVER enter a PainelCard.
 */

import { useQuery } from "@tanstack/react-query";

import {
  atrativoKeys,
  fetchAtrativoList,
  type AtrativoListItem,
} from "@/lib/atrativos-api";
import {
  destinoKeys,
  fetchDestinoList,
  type DestinoListItem,
} from "@/lib/destinos-api";
import { engineKeys, fetchEngineStatus } from "@/lib/engine-api";

// --- Types ---

export type PainelEntityType = "destino" | "atrativo";

export type PainelColumnKey =
  | "nascente"
  | "in_progress"
  | "mar"
  | "dlq"
  | "descarte";

export type TypeFilter = "all" | "destino" | "atrativo";

export interface EntityMetric {
  total: number;
  mar: number;
  falha: number;
  pct: number;
}

/**
 * A board card. Intentionally a flat, PII-free projection of a list item —
 * the only fields the Kanban needs. `source`/`error` are null this slice
 * (no list field today); the card plan supplies a generic ⚠ falha label for
 * descarte cards and hides the source label when null.
 */
export interface PainelCard {
  id: string;
  type: PainelEntityType;
  name: string | null;
  uf: string | null;
  municipality: string | null;
  routing: string;
  column: PainelColumnKey;
  score: number | null;
  source: string | null;
  duplicate: boolean;
  error: string | null;
}

// --- Constants ---

/** The 5 ordered stage columns (copy matches the design canvas, pt-BR). */
export const COLUMN_DEFS: { key: PainelColumnKey; label: string }[] = [
  { key: "nascente", label: "Nascente" },
  { key: "in_progress", label: "Em processamento" },
  { key: "mar", label: "Sincronizado" },
  { key: "dlq", label: "Revisão" },
  { key: "descarte", label: "Descarte" },
];

/** The 27 BR UF codes (copied from EngineControl so the filters plan imports here). */
export const BR_UFS: string[] = [
  "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO",
  "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
  "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
];

const KNOWN_COLUMNS: ReadonlySet<string> = new Set([
  "in_progress",
  "mar",
  "dlq",
  "descarte",
]);

// --- Pure selectors (React-free) ---

/** Map a list item's `routing` to a column key; unknown/empty → "nascente". */
export function routingToColumn(routing: string): PainelColumnKey {
  return KNOWN_COLUMNS.has(routing)
    ? (routing as PainelColumnKey)
    : "nascente";
}

/** Destino município best-effort: last `:`-segment of canonical_key, else null. */
function municipalityFromCanonicalKey(canonicalKey: string | null): string | null {
  if (!canonicalKey) return null;
  const parts = canonicalKey.split(":");
  const last = parts[parts.length - 1];
  return last ? last : null;
}

/**
 * Project list items into unified PainelCards.
 *
 * PII guard (T-17-02-01): explicit allow-list — only the fields below are read.
 * phone_e164 / phone_masked / contacts_summary are NEVER copied.
 * `duplicate = validation_pending` for BOTH entity types (destinos have no
 * atrativo-style dedup flag, so the validation-pending flag IS the slice-1
 * "possível duplicado" hint — explicit + uniform).
 */
export function toPainelCards(
  destinos: DestinoListItem[],
  atrativos: AtrativoListItem[],
): PainelCard[] {
  const destinoCards: PainelCard[] = destinos.map((d) => ({
    id: d.id,
    type: "destino" as const,
    name: d.name,
    uf: d.uf,
    municipality: municipalityFromCanonicalKey(d.canonical_key),
    routing: d.routing,
    column: routingToColumn(d.routing),
    score: d.score,
    source: null,
    duplicate: d.validation_pending,
    error: null,
  }));

  const atrativoCards: PainelCard[] = atrativos.map((a) => ({
    id: a.id,
    type: "atrativo" as const,
    name: a.name,
    uf: a.uf,
    municipality: null, // atrativo município is a later detail slice
    routing: a.routing,
    column: routingToColumn(a.routing),
    score: a.score,
    source: null,
    duplicate: a.validation_pending,
    error: null,
  }));

  return [...destinoCards, ...atrativoCards];
}

/** Apply the type filter (Tudo/Destinos/Atrativos) and UF scope client-side. */
export function filterCards(
  cards: PainelCard[],
  { type, ufs }: { type: TypeFilter; ufs: string[] },
): PainelCard[] {
  return cards.filter((c) => {
    if (type !== "all" && c.type !== type) return false;
    if (ufs.length > 0 && (c.uf == null || !ufs.includes(c.uf))) return false;
    return true;
  });
}

/** Bucket cards into the 5 ordered stage columns. */
export function buildColumns(
  cards: PainelCard[],
): { key: PainelColumnKey; label: string; cards: PainelCard[] }[] {
  return COLUMN_DEFS.map(({ key, label }) => ({
    key,
    label,
    cards: cards.filter((c) => c.column === key),
  }));
}

/** Derive an EntityMetric; pct is mar/total rounded (0 when total is 0). */
export function computeMetric(
  total: number,
  mar: number,
  falha: number,
): EntityMetric {
  return {
    total,
    mar,
    falha,
    pct: total > 0 ? Math.round((mar / total) * 100) : 0,
  };
}

// --- Hooks (over the BFF + TanStack Query) ---

/**
 * Load destinos + atrativos lists (board scope) and build the unified card[].
 * Uses a generous limit so the board shows all in-scope records this slice.
 */
export function usePainelBoard(): {
  cards: PainelCard[];
  isPending: boolean;
  isError: boolean;
} {
  const destinosQuery = useQuery({
    queryKey: destinoKeys.list({ board: true }),
    queryFn: () => fetchDestinoList({ limit: 500 }),
  });
  const atrativosQuery = useQuery({
    queryKey: atrativoKeys.list({ board: true }),
    queryFn: () => fetchAtrativoList({ limit: 500 }),
  });

  const cards =
    destinosQuery.data && atrativosQuery.data
      ? toPainelCards(destinosQuery.data.items, atrativosQuery.data.items)
      : [];

  return {
    cards,
    isPending: destinosQuery.isPending || atrativosQuery.isPending,
    isError: destinosQuery.isError || atrativosQuery.isError,
  };
}

/**
 * Derive truthful per-entity metrics from the list ENVELOPE `total` (server
 * count, not loaded-array length) plus the Nascente column count from engine
 * counts. Each filtered count uses `limit: 1` to keep payloads tiny.
 *
 * Metrics reflect the WHOLE base (not UF-scoped) — the UF scope filters the
 * board only this slice.
 */
export function usePainelMetrics(): {
  destino: EntityMetric;
  atrativo: EntityMetric;
  nascenteCount: number;
  isPending: boolean;
} {
  const destinoTotal = useQuery({
    queryKey: destinoKeys.list({ count: "total" }),
    queryFn: () => fetchDestinoList({ limit: 1 }),
  });
  const destinoMar = useQuery({
    queryKey: destinoKeys.list({ count: "mar" }),
    queryFn: () => fetchDestinoList({ routing: "mar", limit: 1 }),
  });
  const destinoFalha = useQuery({
    queryKey: destinoKeys.list({ count: "descarte" }),
    queryFn: () => fetchDestinoList({ routing: "descarte", limit: 1 }),
  });

  const atrativoTotal = useQuery({
    queryKey: atrativoKeys.list({ count: "total" }),
    queryFn: () => fetchAtrativoList({ limit: 1 }),
  });
  const atrativoMar = useQuery({
    queryKey: atrativoKeys.list({ count: "mar" }),
    queryFn: () => fetchAtrativoList({ routing: "mar", limit: 1 }),
  });
  const atrativoFalha = useQuery({
    queryKey: atrativoKeys.list({ count: "descarte" }),
    queryFn: () => fetchAtrativoList({ routing: "descarte", limit: 1 }),
  });

  const engine = useQuery({
    queryKey: engineKeys.status,
    queryFn: fetchEngineStatus,
  });

  const queries = [
    destinoTotal,
    destinoMar,
    destinoFalha,
    atrativoTotal,
    atrativoMar,
    atrativoFalha,
    engine,
  ];

  return {
    destino: computeMetric(
      destinoTotal.data?.total ?? 0,
      destinoMar.data?.total ?? 0,
      destinoFalha.data?.total ?? 0,
    ),
    atrativo: computeMetric(
      atrativoTotal.data?.total ?? 0,
      atrativoMar.data?.total ?? 0,
      atrativoFalha.data?.total ?? 0,
    ),
    nascenteCount: engine.data?.counts.nascente ?? 0,
    isPending: queries.some((q) => q.isPending),
  };
}
