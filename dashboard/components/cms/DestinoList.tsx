"use client";

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { StageBadge } from "@/components/cms/StageBadge";
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
  destinoKeys,
  fetchDestinoList,
  type DestinoListItem,
} from "@/lib/destinos-api";
import { cn } from "@/lib/utils";

/**
 * DestinoList — the Destinos CMS master list (D-03).
 *
 * TanStack Table v8 + shadcn `table`: UF dropdown + routing filter, 36px dense
 * rows, mono canonical_key/score, StageBadge per row. Row click drives the
 * DetailPanel via `onSelect`.
 *
 * Offline-tested view states (D-07): success / empty / error / 401.
 */
export function DestinoList({
  selectedId,
  onSelect,
}: {
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}) {
  const [uf, setUf] = useState<string | undefined>(undefined);
  const [routing, setRouting] = useState<string | undefined>(undefined);

  const query = useQuery({
    queryKey: destinoKeys.list({ uf, routing }),
    queryFn: () => fetchDestinoList({ uf, routing }),
    staleTime: 30_000,
  });

  const data = useMemo(() => query.data?.items ?? [], [query.data]);

  const columns = useMemo<ColumnDef<DestinoListItem>[]>(
    () => [
      {
        id: "name",
        header: "Nome",
        cell: ({ row }) => (
          <span className="font-mono text-[12px]">
            {row.original.name ?? row.original.id.slice(0, 8)}
          </span>
        ),
      },
      {
        id: "uf",
        header: "UF",
        cell: ({ row }) => (
          <span className="font-mono text-[12px]">{row.original.uf ?? "—"}</span>
        ),
      },
      {
        id: "score",
        header: "Score",
        cell: ({ row }) => (
          <span className="font-mono text-[12px] tabular-nums">
            {row.original.score != null
              ? row.original.score.toFixed(1)
              : "—"}
          </span>
        ),
      },
      {
        id: "routing",
        header: "Estado",
        cell: ({ row }) => (
          <StageBadge
            routing={row.original.routing}
            score={row.original.score}
          />
        ),
      },
      {
        id: "validation",
        header: "",
        cell: ({ row }) =>
          row.original.validation_pending ? (
            <StageBadge validationPending />
          ) : null,
      },
    ],
    [],
  );

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="flex h-full flex-col gap-3">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        {/* UF filter */}
        <select
          value={uf ?? ""}
          onChange={(e) => setUf(e.target.value || undefined)}
          className="h-7 rounded border bg-background px-2 font-mono text-[12px]"
          aria-label="Filtrar por UF"
        >
          <option value="">Todos UF</option>
          {["BA", "RJ", "SP", "SC", "CE", "PE"].map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>

        {/* Routing filter */}
        <div className="flex gap-1" role="group" aria-label="Filtrar por estado">
          {[
            { value: undefined, label: "Todos" },
            { value: "mar", label: "Mar" },
            { value: "dlq", label: "DLQ" },
            { value: "descarte", label: "Descarte" },
          ].map((opt) => (
            <Button
              key={opt.label}
              size="sm"
              variant={routing === opt.value ? "default" : "outline"}
              className="h-7 font-mono text-[12px]"
              onClick={() => setRouting(opt.value)}
            >
              {opt.label}
            </Button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto rounded-md border">
        {query.isLoading ? (
          <DestinoSkeleton />
        ) : query.isError ? (
          <DestinoError error={query.error} onRetry={() => query.refetch()} />
        ) : data.length === 0 ? (
          <DestinoEmpty />
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
                  className={cn(
                    "h-9 cursor-pointer",
                    selectedId === row.original.id && "bg-muted",
                  )}
                  onClick={() => onSelect?.(row.original.id)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className="py-1">
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext(),
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

function DestinoSkeleton() {
  return (
    <div className="flex flex-col gap-1 p-2" data-testid="destino-list-skeleton">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-9 animate-pulse rounded bg-muted" />
      ))}
    </div>
  );
}

function DestinoEmpty() {
  return (
    <div className="flex flex-col items-center justify-center gap-1 p-12 text-center">
      <h3 className="text-[14px] font-semibold">Sem destinos</h3>
      <p className="text-[12px] text-muted-foreground">
        Nenhum destino encontrado para este filtro.
      </p>
    </div>
  );
}

function DestinoError({
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
