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
import {
  ENGINE_REFETCH_INTERVAL_MS,
  engineKeys,
  fetchFailures,
  type FailureItem,
} from "@/lib/engine-api";
import {
  fetchNascenteList,
  nascenteKeys,
  type NascenteListItem,
} from "@/lib/nascente-api";
import { dedupKeys, fetchDedupPairs } from "@/lib/dedup-api";

// --- Types ---

export type PainelEntityType = "destino" | "atrativo";

/**
 * Board column key. The 6 RENDERED stage columns are the ones in COLUMN_DEFS
 * (nascente, rio, whatsapp, mar, dlq, falha). A record with routing="descarte"
 * now maps to the Falha column (phase H) so discarded records are visible.
 * `descarte` is kept as a valid, NON-rendered TARGET key: it is the server
 * column name the drawer "Descartar" path (and rio/dlq→descarte edges) transition
 * to — never a standing board column.
 */
export type PainelColumnKey =
  | "nascente"
  | "rio"
  | "whatsapp"
  | "mar"
  | "dlq"
  | "descarte"
  | "falha";

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
  /**
   * DLQ→WhatsApp eligibility (phase H) — only meaningful for a DLQ-column
   * atrativo. False ⇒ the card already has horário/preço and its selection
   * checkbox is disabled. Absent/true ⇒ selectable (the batch 422 is the
   * authoritative atomic gate). Non-atrativo cards leave this true (unused).
   */
  whatsappEligible?: boolean;
}

// --- Constants ---

/** The 6 ordered stage columns (copy matches the design canvas, pt-BR). */
export const COLUMN_DEFS: { key: PainelColumnKey; label: string }[] = [
  { key: "nascente", label: "Nascente" },
  { key: "rio", label: "Rio · validação" },
  { key: "whatsapp", label: "WhatsApp · contato" },
  { key: "mar", label: "Mar · publicado" },
  { key: "dlq", label: "DLQ · revisão" },
  { key: "falha", label: "Falha" },
];

/** The 27 BR UF codes (copied from EngineControl so the filters plan imports here). */
export const BR_UFS: string[] = [
  "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO",
  "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
  "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
];

/** Routing value → board column. The server twin is `_ROUTING_TO_COLUMN`
 *  (brave/api/routers/cms.py): `in_progress` is the "Rio · validação" column. */
const ROUTING_TO_COLUMN: ReadonlyMap<string, PainelColumnKey> = new Map([
  ["in_progress", "rio"],
  ["mar", "mar"],
  ["dlq", "dlq"],
  // Phase H: descarte-routed records surface in the Falha column (alongside
  // PoisonQuarantine failures) instead of being silently dropped.
  ["descarte", "falha"],
]);

/** The atrativo FSM sub_state that buckets a record into the WhatsApp column. */
const WHATSAPP_SUB_STATE = "aguardando_consulta_whatsapp";

// --- Pure selectors (React-free) ---

/** Map a list item's `routing` to a column key; unknown/empty → "nascente". */
export function routingToColumn(routing: string): PainelColumnKey {
  return ROUTING_TO_COLUMN.get(routing) ?? "nascente";
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
 * `duplicate` is a REAL dedup signal (F3): a card is flagged "possível duplicado"
 * ONLY when its rio id is in `dedupCandidateIds` — the pending candidate↔Mar pairs
 * from GET /api/v1/dedup/pairs (the exact source the Duplicados view uses). It is
 * NOT derived from `validation_pending` (which only means "DLQ pending review" /
 * the WhatsApp gate — unrelated to dedup, so it blanket-flagged the whole DLQ column).
 */
export function toPainelCards(
  destinos: DestinoListItem[],
  atrativos: AtrativoListItem[],
  failures: FailureItem[] = [],
  nascente: NascenteListItem[] = [],
  dedupCandidateIds: ReadonlySet<string> = new Set(),
): PainelCard[] {
  // Nascente cards: the raw immutable ingest layer, READ-ONLY (no routing yet).
  // entity_type is the backend's "destination"/"attraction" — map to the board's
  // destino/atrativo. They always bucket into the Nascente column.
  const nascenteCards: PainelCard[] = nascente.map((n) => ({
    id: n.id,
    type: n.entity_type === "attraction" ? ("atrativo" as const) : ("destino" as const),
    name: n.name,
    uf: n.uf,
    municipality: n.municipio,
    routing: "nascente",
    column: "nascente" as const,
    score: null,
    source: n.source,
    duplicate: false,
    error: null,
  }));

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
    duplicate: dedupCandidateIds.has(d.id),
    error: null,
  }));

  const atrativoCards: PainelCard[] = atrativos.map((a) => ({
    id: a.id,
    type: "atrativo" as const,
    name: a.name,
    uf: a.uf,
    municipality: a.municipio ?? null, // público-geo município resolved at ingest
    routing: a.routing,
    // An atrativo awaiting WhatsApp contact lives in its own column regardless
    // of routing (the gate sub_state wins over the rio routing value).
    column:
      a.sub_state === WHATSAPP_SUB_STATE ? "whatsapp" : routingToColumn(a.routing),
    score: a.score,
    source: null,
    duplicate: dedupCandidateIds.has(a.id),
    error: null,
    // Phase H DLQ→WhatsApp gate: absent from the list ⇒ eligible (the batch 422
    // is authoritative); false ⇒ already has horário/preço → checkbox disabled.
    whatsappEligible: a.whatsapp_eligible ?? true,
  }));

  const falhaCards = failures.map(failureToCard);

  return [...nascenteCards, ...destinoCards, ...atrativoCards, ...falhaCards];
}

/** Infer the entity type of a quarantined task from its task_name. */
function failureEntityType(taskName: string): PainelEntityType {
  return /attraction|atrativo/i.test(taskName) ? "atrativo" : "destino";
}

/**
 * Project a PoisonQuarantine FailureItem (GET /api/v1/failures) into a real,
 * draggable falha card. PII guard (T-17.1-06-03): only the quarantine id, task
 * name, and truncated error reason are read — no payload, no phone.
 */
function failureToCard(f: FailureItem): PainelCard {
  return {
    id: f.id,
    type: failureEntityType(f.task_name),
    name: f.task_name,
    uf: null,
    municipality: null,
    routing: "falha",
    column: "falha",
    score: null,
    source: null,
    duplicate: false,
    error: f.error_message,
  };
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

/** Bucket cards into the 6 ordered stage columns. */
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
export function usePainelBoard(
  intervalMs: number = ENGINE_REFETCH_INTERVAL_MS,
): {
  cards: PainelCard[];
  nascenteCount: number;
  isPending: boolean;
  isError: boolean;
} {
  const destinosQuery = useQuery({
    queryKey: destinoKeys.list({ board: true }),
    queryFn: () => fetchDestinoList({ limit: 500 }),
    refetchInterval: intervalMs,
  });
  const atrativosQuery = useQuery({
    queryKey: atrativoKeys.list({ board: true }),
    queryFn: () => fetchAtrativoList({ limit: 500 }),
    refetchInterval: intervalMs,
  });
  // Falha column: real PoisonQuarantine records (draggable for reprocess). The
  // board still loads if /failures fails — falha just renders empty (additive).
  const failuresQuery = useQuery({
    queryKey: engineKeys.failures,
    queryFn: () => fetchFailures(),
    refetchInterval: intervalMs,
  });
  // Nascente column (bug 4): the REAL unrouted records — nascente rows with no
  // RioRecord twin yet. These are genuine "just ingested, not yet routed" cards
  // (the LEFT JOIN … IS NULL slice), so they no longer double-count the routed
  // layer. The board still builds if this is pending (?? []); nascenteCount is
  // the server ENVELOPE total (the true unrouted count for the column pill).
  const nascenteQuery = useQuery({
    queryKey: nascenteKeys.list({ board: true }),
    queryFn: () => fetchNascenteList({ unrouted: true, limit: 500 }),
    refetchInterval: intervalMs,
  });
  // Dedup pairs (F3): the REAL "possível duplicado" signal — the same
  // compute-on-read candidate↔Mar pairs the Duplicados view shows (shared query
  // cache key). A card is flagged ONLY when its rio id is a pending dedup
  // candidate, never as a blanket flag on the whole DLQ column. The board still
  // loads if this fails — the candidate set just stays empty (no badges).
  const dedupQuery = useQuery({
    queryKey: dedupKeys.pairs(),
    queryFn: () => fetchDedupPairs(),
    refetchInterval: intervalMs,
  });
  const dedupCandidateIds = new Set(
    (dedupQuery.data?.items ?? []).map((p) => p.candidate_rio_id),
  );

  const cards =
    destinosQuery.data && atrativosQuery.data
      ? toPainelCards(
          destinosQuery.data.items,
          atrativosQuery.data.items,
          failuresQuery.data?.items ?? [],
          // Bug 4: feed the REAL unrouted nascente rows as Nascente-column cards.
          // Guard: the board still builds once destinos+atrativos resolve even if
          // the nascente query is still pending (?? []).
          nascenteQuery.data?.items ?? [],
          dedupCandidateIds,
        )
      : [];

  return {
    cards,
    // The Nascente pill shows the TRUE unrouted total (server envelope), not the
    // loaded-array length.
    nascenteCount: nascenteQuery.data?.total ?? 0,
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
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
  });
  const destinoMar = useQuery({
    queryKey: destinoKeys.list({ count: "mar" }),
    queryFn: () => fetchDestinoList({ routing: "mar", limit: 1 }),
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
  });
  const destinoFalha = useQuery({
    queryKey: destinoKeys.list({ count: "descarte" }),
    queryFn: () => fetchDestinoList({ routing: "descarte", limit: 1 }),
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
  });

  const atrativoTotal = useQuery({
    queryKey: atrativoKeys.list({ count: "total" }),
    queryFn: () => fetchAtrativoList({ limit: 1 }),
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
  });
  const atrativoMar = useQuery({
    queryKey: atrativoKeys.list({ count: "mar" }),
    queryFn: () => fetchAtrativoList({ routing: "mar", limit: 1 }),
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
  });
  const atrativoFalha = useQuery({
    queryKey: atrativoKeys.list({ count: "descarte" }),
    queryFn: () => fetchAtrativoList({ routing: "descarte", limit: 1 }),
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
  });

  // Nascente count from the nascente list ENVELOPE total (current versions
  // only). The Nascente column is count-only (QA F2): it renders no cards —
  // every record shows once in its routed column — so this pill is the true
  // server total (same aggregate semantics as the Monitor view).
  const nascenteTotal = useQuery({
    queryKey: nascenteKeys.list({ count: "total" }),
    queryFn: () => fetchNascenteList({ limit: 1 }),
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
  });

  const queries = [
    destinoTotal,
    destinoMar,
    destinoFalha,
    atrativoTotal,
    atrativoMar,
    atrativoFalha,
    nascenteTotal,
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
    nascenteCount: nascenteTotal.data?.total ?? 0,
    isPending: queries.some((q) => q.isPending),
  };
}
