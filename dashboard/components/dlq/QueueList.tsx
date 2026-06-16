"use client";

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type RowSelectionState,
} from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { StatusBadge } from "@/components/dlq/StatusBadge";
import {
  useValidateDlqBatch,
} from "@/components/dlq/dlq-actions";
import { Button } from "@/components/ui/button";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError } from "@/lib/api-client";
import {
  UF_PRIORITY,
  dlqKeys,
  fetchDlqList,
  type DlqListItem,
} from "@/lib/dlq-api";
import { cn } from "@/lib/utils";

/**
 * QueueList — the master list (UI-SPEC D-06).
 *
 * TanStack Table v8 + shadcn `table`: a UF filter defaulting to the
 * BA/RJ/SP/SC/CE/PE steward-priority order, row selection for batch validate,
 * 36px dense rows, mono `canonical_key`/`score`, a `StatusBadge` per row.
 * Selecting a row drives the `ReviewPanel` via `onSelect`.
 *
 * Batch-by-state: the UF filter scopes the list; selected rows enable
 * "Validar lote por estado", gated by the "Validar {n} registros de {UF} em
 * lote?" confirm before the high-impact POST (T-04-13).
 *
 * Offline-tested view states (D-07): success / empty / error / 401.
 */
export function QueueList({
  selectedId,
  onSelect,
  entityType = "destination",
}: {
  selectedId?: string | null;
  onSelect?: (rioId: string) => void;
  entityType?: string;
}) {
  const [uf, setUf] = useState<string>(UF_PRIORITY[0]);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  const query = useQuery({
    queryKey: dlqKeys.list(uf, entityType),
    queryFn: () => fetchDlqList(uf, entityType),
  });

  const batch = useValidateDlqBatch();

  const data = useMemo(() => query.data ?? [], [query.data]);

  const columns = useMemo<ColumnDef<DlqListItem>[]>(
    () => [
      {
        id: "canonical_key",
        header: "canonical_key",
        cell: ({ row }) => (
          <span className="font-mono text-[12px] tabular-nums">
            {row.original.canonical_key ?? row.original.id.slice(0, 8)}
          </span>
        ),
      },
      {
        id: "score",
        header: "score",
        cell: ({ row }) => (
          <span className="font-mono text-[12px] tabular-nums">
            {row.original.score != null ? row.original.score.toFixed(1) : "—"}
          </span>
        ),
      },
      {
        id: "routing",
        header: "estado",
        cell: ({ row }) => <StatusBadge routing={row.original.routing} />,
      },
    ],
    [],
  );

  const table = useReactTable({
    data,
    columns,
    state: { rowSelection },
    onRowSelectionChange: setRowSelection,
    getRowId: (r) => r.id,
    enableRowSelection: true,
    getCoreRowModel: getCoreRowModel(),
  });

  const selectedCount = Object.values(rowSelection).filter(Boolean).length;

  return (
    <div className="flex h-full flex-col gap-3">
      {/* UF filter — BA/RJ/SP/SC/CE/PE priority order (UI-SPEC D-06) */}
      <div className="flex flex-wrap items-center gap-1" role="tablist">
        {UF_PRIORITY.map((code) => (
          <Button
            key={code}
            size="sm"
            variant={uf === code ? "default" : "outline"}
            className="h-7 font-mono text-[12px]"
            aria-pressed={uf === code}
            onClick={() => {
              setUf(code);
              setRowSelection({});
            }}
          >
            {code}
          </Button>
        ))}
      </div>

      {/* Batch validate-by-state — confirm before the high-impact POST */}
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-muted-foreground">
          {selectedCount > 0
            ? `${selectedCount} selecionado(s)`
            : `Fila DLQ — ${uf}`}
        </span>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button size="sm" disabled={selectedCount === 0 || batch.isPending}>
              Validar lote — {uf} ({selectedCount})
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                Validar {selectedCount} registros de {uf} em lote?
              </AlertDialogTitle>
              <AlertDialogDescription>
                Todos serão marcados como validados pelo humano e reprocessados.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancelar</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => {
                  batch.mutate({ uf, entityType });
                  setRowSelection({});
                }}
              >
                Validar lote por estado
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      <div className="flex-1 overflow-auto rounded-md border">
        {query.isLoading ? (
          <QueueSkeleton />
        ) : query.isError ? (
          <QueueError error={query.error} onRetry={() => query.refetch()} />
        ) : data.length === 0 ? (
          <QueueEmpty uf={uf} />
        ) : (
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id} className="h-9">
                  {hg.headers.map((h) => (
                    <TableHead
                      key={h.id}
                      className="text-[12px] font-semibold uppercase"
                    >
                      {flexRender(
                        h.column.columnDef.header,
                        h.getContext(),
                      )}
                    </TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  data-state={
                    selectedId === row.original.id ? "selected" : undefined
                  }
                  className={cn(
                    "h-9 cursor-pointer",
                    selectedId === row.original.id && "bg-muted",
                  )}
                  onClick={() => onSelect?.(row.original.id)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className="py-1">
                      {cell.column.id === "canonical_key" ? (
                        <span className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            aria-label={`Selecionar ${row.original.canonical_key ?? row.original.id}`}
                            checked={row.getIsSelected()}
                            onClick={(e) => e.stopPropagation()}
                            onChange={row.getToggleSelectedHandler()}
                          />
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext(),
                          )}
                        </span>
                      ) : (
                        flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}

function QueueSkeleton() {
  return (
    <div className="flex flex-col gap-1 p-2" data-testid="queue-skeleton">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-9 animate-pulse rounded bg-muted" />
      ))}
    </div>
  );
}

function QueueEmpty({ uf }: { uf: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 p-12 text-center">
      <h3 className="text-[14px] font-semibold">DLQ vazia para este estado</h3>
      <p className="text-[12px] text-muted-foreground">
        Nenhum registro aguardando revisão em {uf}. Selecione outro estado ou
        aguarde a próxima leva do pipeline.
      </p>
    </div>
  );
}

function QueueError({
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
        <h3 className="text-[14px] font-semibold">
          Sessão expirada ou token inválido
        </h3>
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
        Falha ao consultar a API ({status ?? "rede"}). Verifique se o serviço
        Brave está no ar e tente novamente.
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}
