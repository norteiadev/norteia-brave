"use client";

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { StatusBadge } from "@/components/dlq/StatusBadge";
import { Button } from "@/components/ui/button";
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
  fetchGateQueue,
  gateKeys,
  type GateQueueItem,
} from "@/lib/gate-api";
import { cn } from "@/lib/utils";

/**
 * GateQueue — the WhatsApp-gate master list (DASH-03, reuses the DLQ D-06 scaffold).
 *
 * Same shape as the DLQ `QueueList` (TanStack Table v8 + shadcn `table`, the
 * BA/RJ/SP/SC/CE/PE-first UF filter, 36px dense rows, mono `canonical_key`/score,
 * a `StatusBadge` per row, row-click → detail). Bound to
 * `GET /api/v1/atrativos/gate?uf&limit`; the server already scopes the queue to
 * `entity_type=attraction` + `sub_state=aguardando_consulta_whatsapp`.
 *
 * No batch action here (the gate approves one outreach contact at a time);
 * selecting a row drives the `GateReviewPanel` via `onSelect`.
 *
 * Offline-tested view states (D-07): success / empty / error / 401.
 */
export function GateQueue({
  selectedId,
  onSelect,
}: {
  selectedId?: string | null;
  onSelect?: (item: GateQueueItem) => void;
}) {
  const [uf, setUf] = useState<string>(UF_PRIORITY[0]);

  const query = useQuery({
    queryKey: gateKeys.list(uf),
    queryFn: () => fetchGateQueue(uf),
  });

  const data = useMemo(() => query.data ?? [], [query.data]);

  const columns = useMemo<ColumnDef<GateQueueItem>[]>(
    () => [
      {
        id: "canonical_key",
        header: "canonical_key",
        cell: ({ row }) => (
          <span className="font-mono text-[12px] tabular-nums">
            {row.original.canonical_key ?? row.original.rio_id.slice(0, 8)}
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
    getRowId: (r) => r.rio_id,
    getCoreRowModel: getCoreRowModel(),
  });

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
            onClick={() => setUf(code)}
          >
            {code}
          </Button>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <span className="text-[12px] text-muted-foreground">
          Fila de gate — {uf} · aguardando_consulta_whatsapp
        </span>
      </div>

      <div className="flex-1 overflow-auto rounded-md border">
        {query.isLoading ? (
          <QueueSkeleton />
        ) : query.isError ? (
          <QueueError error={query.error} onRetry={() => query.refetch()} />
        ) : data.length === 0 ? (
          <QueueEmpty />
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
                      {flexRender(h.column.columnDef.header, h.getContext())}
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
                    selectedId === row.original.rio_id ? "selected" : undefined
                  }
                  className={cn(
                    "h-9 cursor-pointer",
                    selectedId === row.original.rio_id && "bg-muted",
                  )}
                  onClick={() => onSelect?.(row.original)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className="py-1">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
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
    <div className="flex flex-col gap-1 p-2" data-testid="gate-queue-skeleton">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-9 animate-pulse rounded bg-muted" />
      ))}
    </div>
  );
}

function QueueEmpty() {
  return (
    <div className="flex flex-col items-center justify-center gap-1 p-12 text-center">
      <h3 className="text-[14px] font-semibold">Fila de gate vazia</h3>
      <p className="text-[12px] text-muted-foreground">
        Nenhum atrativo em aguardando_consulta_whatsapp. Novos candidatos
        aparecem conforme o pipeline pontua registros borderline.
      </p>
    </div>
  );
}

function QueueError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
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
