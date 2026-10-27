/**
 * Painel shared data helpers.
 *
 * The kanban board model (PainelCard, columns, usePainelBoard) left with the
 * kanban (#88); Registros reads the list endpoints directly. What stays:
 *   - BR_UFS, the UF list behind every UF picker;
 *   - usePainelMetrics, truthful per-entity totals from the list ENVELOPE
 *     `total` (server count, never the loaded-array length).
 */

import { useQuery } from "@tanstack/react-query";

import { atrativoKeys, fetchAtrativoList } from "@/lib/atrativos-api";
import { destinoKeys, fetchDestinoList } from "@/lib/destinos-api";
import { ENGINE_REFETCH_INTERVAL_MS } from "@/lib/engine-api";
import { fetchNascenteList, nascenteKeys } from "@/lib/nascente-api";

export interface EntityMetric {
  total: number;
  mar: number;
  falha: number;
  pct: number;
}

/** The 27 BR UF codes. */
export const BR_UFS: string[] = [
  "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO",
  "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
  "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
];

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

/**
 * Derive truthful per-entity metrics from the list ENVELOPE `total` (server
 * count, not loaded-array length) plus the Nascente column count from engine
 * counts. Each filtered count uses `limit: 1` to keep payloads tiny.
 *
 * Metrics are UF-scoped when a UF is selected (pass `uf`) so the pills reflect
 * the same slice as the board; with `uf = null` they reflect the WHOLE base.
 */
export function usePainelMetrics(
  uf: string | null = null,
  intervalMs: number = ENGINE_REFETCH_INTERVAL_MS,
): {
  destino: EntityMetric;
  atrativo: EntityMetric;
  nascenteCount: number;
  isPending: boolean;
} {
  // undefined ⇒ no ?uf param ⇒ whole-base totals ("Todas"); a UF ⇒ scoped counts.
  const ufParam = uf ?? undefined;
  const destinoTotal = useQuery({
    queryKey: destinoKeys.list({ count: "total", uf }),
    queryFn: () => fetchDestinoList({ limit: 1, uf: ufParam }),
    refetchInterval: intervalMs,
  });
  const destinoMar = useQuery({
    queryKey: destinoKeys.list({ count: "mar", uf }),
    queryFn: () => fetchDestinoList({ routing: "mar", limit: 1, uf: ufParam }),
    refetchInterval: intervalMs,
  });
  const destinoFalha = useQuery({
    queryKey: destinoKeys.list({ count: "descarte", uf }),
    queryFn: () => fetchDestinoList({ routing: "descarte", limit: 1, uf: ufParam }),
    refetchInterval: intervalMs,
  });

  const atrativoTotal = useQuery({
    queryKey: atrativoKeys.list({ count: "total", uf }),
    queryFn: () => fetchAtrativoList({ limit: 1, uf: ufParam }),
    refetchInterval: intervalMs,
  });
  const atrativoMar = useQuery({
    queryKey: atrativoKeys.list({ count: "mar", uf }),
    queryFn: () => fetchAtrativoList({ routing: "mar", limit: 1, uf: ufParam }),
    refetchInterval: intervalMs,
  });
  const atrativoFalha = useQuery({
    queryKey: atrativoKeys.list({ count: "descarte", uf }),
    queryFn: () => fetchAtrativoList({ routing: "descarte", limit: 1, uf: ufParam }),
    refetchInterval: intervalMs,
  });

  // Nascente count from the nascente list ENVELOPE total (current versions
  // only). The Nascente column is count-only (QA F2): it renders no cards —
  // every record shows once in its routed column — so this pill is the true
  // server total (same aggregate semantics as the Monitor view).
  const nascenteTotal = useQuery({
    queryKey: nascenteKeys.list({ count: "total", uf }),
    queryFn: () => fetchNascenteList({ limit: 1, uf: ufParam }),
    refetchInterval: intervalMs,
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
