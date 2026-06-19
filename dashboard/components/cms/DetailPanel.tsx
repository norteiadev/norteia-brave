"use client";

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { JourneyStepper } from "@/components/cms/JourneyStepper";
import { StageBadge } from "@/components/cms/StageBadge";
import { ScoreBreakdownPanel } from "@/components/dlq/ScoreBreakdownPanel";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";

/**
 * DetailPanel — generic CMS detail panel (D-03, D-04).
 *
 * Action-agnostic: the action bar is injected via `actions` so destinos and
 * atrativos pages can each provide their own action set (promote/descarte/
 * reprocess for destinos; advance/descarte for atrativos).
 *
 * The panel owns only the fetch lifecycle and render. It accepts:
 *  - `rioId`: the record to fetch (null → placeholder)
 *  - `fetchDetail`: the typed fetcher (fetchDestinoDetail or fetchAtrativoDetail)
 *  - `queryKeys`: TanStack key factory with `.detail(id)` method
 *  - `entityType`: "destination" | "attraction" (drives JourneyStepper step set)
 *  - `actions`: optional render-prop injected at the bottom of the panel
 *
 * View states: loading → Skeleton; 401 → Sessão expirada; error → retry;
 *              null rioId → "Selecione um item à esquerda"
 */

interface RecordBase {
  id: string;
  routing: string;
  sub_state?: string | null;
  score?: number | null;
  score_breakdown?: Record<string, unknown>;
  normalized?: Record<string, unknown>;
  canonical_key?: string | null;
  source?: string | null;
  audit_log?: Array<{
    action: string;
    actor: string | null;
    after_state: Record<string, unknown> | null;
    created_at: string | null;
  }>;
}

export interface DetailPanelProps<T extends RecordBase> {
  rioId: string | null;
  fetchDetail: (id: string) => Promise<T>;
  queryKeys: { detail: (id: string) => readonly unknown[] };
  entityType: "destination" | "attraction";
  actions?: (detail: T) => ReactNode;
}

export function DetailPanel<T extends RecordBase>({
  rioId,
  fetchDetail,
  queryKeys,
  entityType,
  actions,
}: DetailPanelProps<T>) {
  const query = useQuery({
    queryKey: rioId ? queryKeys.detail(rioId) : ["detail", "none"],
    queryFn: () => fetchDetail(rioId as string),
    enabled: rioId != null,
  });

  if (rioId == null) {
    return (
      <div className="flex h-full items-center justify-center p-12 text-center text-[14px] text-muted-foreground">
        Selecione um item à esquerda para ver o detalhe.
      </div>
    );
  }

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-4 p-6" data-testid="detail-panel-skeleton">
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (query.isError) {
    const status =
      query.error instanceof ApiError ? query.error.status : undefined;

    if (status === 401) {
      return (
        <div className="flex flex-col items-center justify-center gap-2 p-12 text-center">
          <h3 className="text-[20px] font-semibold">
            Sessão expirada ou token inválido
          </h3>
          <p className="text-[14px] text-muted-foreground">
            Faça login novamente para continuar.
          </p>
        </div>
      );
    }

    return (
      <div className="flex flex-col items-center justify-center gap-3 p-12 text-center">
        <h3 className="text-[20px] font-semibold">Não foi possível carregar</h3>
        <p className="text-[14px] text-muted-foreground">
          Falha ao consultar a API ({status ?? "rede"}). Verifique se o serviço
          Brave está no ar e tente novamente.
        </p>
        <Button variant="outline" size="sm" onClick={() => query.refetch()}>
          Tentar novamente
        </Button>
      </div>
    );
  }

  const detail = query.data as T;
  const displayName =
    (detail.normalized?.name as string | undefined) ??
    detail.canonical_key ??
    detail.id.slice(0, 8);

  return (
    <div className="flex h-full flex-col gap-6 overflow-auto p-6">
      <header className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="text-[20px] font-semibold leading-tight">
            {displayName}
          </h2>
          <span className="font-mono text-[12px] text-muted-foreground tabular-nums">
            {detail.id}
          </span>
        </div>
        <StageBadge
          routing={detail.routing}
          subState={detail.sub_state}
          score={detail.score}
          source={detail.source}
        />
      </header>

      {detail.score_breakdown && (
        <ScoreBreakdownPanel
          breakdown={detail.score_breakdown}
          score={detail.score ?? null}
        />
      )}

      {detail.audit_log && (
        <>
          <Separator />
          <section className="flex flex-col gap-2">
            <h3 className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
              Jornada
            </h3>
            <JourneyStepper
              entityType={entityType}
              routing={detail.routing}
              subState={detail.sub_state}
              score={detail.score}
              auditLog={detail.audit_log}
            />
          </section>
        </>
      )}

      {actions ? (
        <>
          <Separator />
          <div className="flex flex-wrap items-center gap-2">
            {actions(detail)}
          </div>
        </>
      ) : null}
    </div>
  );
}
