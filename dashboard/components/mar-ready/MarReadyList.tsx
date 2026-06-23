"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import {
  usePromoteMarReadyBatch,
  usePromoteMarReadyRecord,
} from "@/components/mar-ready/MarReadyActions";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import { fetchMarReadyList, marReadyKeys, type MarReadyItem } from "@/lib/mar-ready-api";

/**
 * MarReadyList — list of TripAdvisor attractions ready for manual promotion to Mar.
 *
 * TanStack Query polls the mar-ready list. Each row has a "Promover" button
 * (optimistic single promote). Multi-select enables bulk "Promover selecionados"
 * gated by a confirm dialog before the batch POST.
 *
 * Empty state: "Nenhum atrativo pronto para promoção".
 * Loading state: skeleton rows.
 * Error state: fetch error with retry.
 */
export function MarReadyList({ uf }: { uf?: string }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const query = useQuery({
    queryKey: marReadyKeys.list(uf),
    queryFn: () => fetchMarReadyList(uf),
  });

  const promote = usePromoteMarReadyRecord();
  const batch = usePromoteMarReadyBatch();

  const items = query.data ?? [];
  const selectedCount = selected.size;

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function handleBatchConfirm() {
    const ufs = uf ? [uf] : [...new Set(items.filter((i) => selected.has(i.id)).map((i) => i.uf))];
    batch.mutate({ ufs });
    setSelected(new Set());
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-muted-foreground">
          {selectedCount > 0
            ? `${selectedCount} selecionado(s)`
            : "Atrativos prontos para promoção → Mar"}
        </span>

        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              size="sm"
              disabled={selectedCount === 0 || batch.isPending}
              data-testid="mar-ready-batch-btn"
            >
              Promover selecionados ({selectedCount})
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                Promover {selectedCount} atrativo(s) selecionado(s)?
              </AlertDialogTitle>
              <AlertDialogDescription>
                Todos serão promovidos ao Mar. Ação não reversível automaticamente.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancelar</AlertDialogCancel>
              <AlertDialogAction onClick={handleBatchConfirm}>
                Promover selecionados
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      <div className="overflow-auto rounded-md border">
        {query.isLoading ? (
          <MarReadySkeleton />
        ) : query.isError ? (
          <MarReadyError error={query.error} onRetry={() => query.refetch()} />
        ) : items.length === 0 ? (
          <MarReadyEmpty />
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b bg-muted/40">
                <th className="py-2 pl-3 text-left font-semibold uppercase text-[11px] text-muted-foreground">
                  Sel
                </th>
                <th className="py-2 px-2 text-left font-semibold uppercase text-[11px] text-muted-foreground">
                  canonical_key
                </th>
                <th className="py-2 px-2 text-left font-semibold uppercase text-[11px] text-muted-foreground">
                  UF
                </th>
                <th className="py-2 px-2 text-left font-semibold uppercase text-[11px] text-muted-foreground">
                  score
                </th>
                <th className="py-2 px-2 text-left font-semibold uppercase text-[11px] text-muted-foreground">
                  ação
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <MarReadyRow
                  key={item.id}
                  item={item}
                  selected={selected.has(item.id)}
                  onToggle={() => toggleSelect(item.id)}
                  onPromote={() => promote.mutate(item.id)}
                  promoting={promote.isPending}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function MarReadyRow({
  item,
  selected,
  onToggle,
  onPromote,
  promoting,
}: {
  item: MarReadyItem;
  selected: boolean;
  onToggle: () => void;
  onPromote: () => void;
  promoting: boolean;
}) {
  return (
    <tr className="border-b last:border-0 h-10" data-testid={`mar-ready-row-${item.id}`}>
      <td className="py-1 pl-3">
        <input
          type="checkbox"
          aria-label={`Selecionar ${item.canonical_key}`}
          checked={selected}
          onChange={onToggle}
        />
      </td>
      <td className="py-1 px-2 font-mono text-[12px] text-muted-foreground">
        {item.canonical_key}
      </td>
      <td className="py-1 px-2 font-mono text-[12px]">{item.uf}</td>
      <td className="py-1 px-2 font-mono text-[12px] tabular-nums">
        {item.score.toFixed(1)}
      </td>
      <td className="py-1 px-2">
        <Button
          size="sm"
          disabled={promoting}
          onClick={onPromote}
          data-testid={`mar-ready-promote-${item.id}`}
        >
          Promover
        </Button>
      </td>
    </tr>
  );
}

function MarReadySkeleton() {
  return (
    <div className="flex flex-col gap-1 p-2" data-testid="mar-ready-skeleton">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-10 animate-pulse rounded bg-muted" />
      ))}
    </div>
  );
}

function MarReadyEmpty() {
  return (
    <div className="flex flex-col items-center justify-center gap-1 p-12 text-center">
      <h3 className="text-[14px] font-semibold">Nenhum atrativo pronto para promoção</h3>
      <p className="text-[12px] text-muted-foreground">
        Nenhum atrativo TripAdvisor atingiu os critérios de Mar Ready (atualidade ≥70 e
        corroboração ≥60). Aguarde o próximo ciclo de coleta.
      </p>
    </div>
  );
}

function MarReadyError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const status = error instanceof ApiError ? error.status : undefined;
  if (status === 401) {
    return (
      <div className="flex flex-col items-center justify-center gap-1 p-12 text-center">
        <h3 className="text-[14px] font-semibold">Sessão expirada ou token inválido</h3>
        <p className="text-[12px] text-muted-foreground">
          Faça login novamente para continuar.
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-12 text-center">
      <h3 className="text-[14px] font-semibold">Não foi possível carregar</h3>
      <p className="text-[12px] text-muted-foreground">
        Falha ao consultar a API ({status ?? "rede"}).
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}
