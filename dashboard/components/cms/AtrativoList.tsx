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
  atrativoKeys,
  fetchAtrativoList,
  type AtrativoListItem,
} from "@/lib/atrativos-api";
import { cn } from "@/lib/utils";

/**
 * AtrativoList — the Atrativos CMS master list (D-04).
 *
 * TanStack Table v8 + shadcn `table`: UF dropdown + sub_state filter + optional
 * parent_mar_id text input, 36px dense rows, StageBadge per row (sub_state +
 * score + routing). Row click drives the DetailPanel via `onSelect`.
 *
 * contacts_summary.phone_masked is NOT rendered in the list — contacts surface
 * only in the detail view. phone_e164 is never present in any API response.
 *
 * Offline-tested view states: success / empty / error / 401.
 */
export function AtrativoList({
  selectedId,
  onSelect,
}: {
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}) {
  const [uf, setUf] = useState<string | undefined>(undefined);
  const [subState, setSubState] = useState<string | undefined>(undefined);
  const [parentMarId, setParentMarId] = useState<string | undefined>(undefined);

  const query = useQuery({
    queryKey: atrativoKeys.list({ uf, sub_state: subState, parent_mar_id: parentMarId }),
    queryFn: () => fetchAtrativoList({ uf, sub_state: subState, parent_mar_id: parentMarId }),
    staleTime: 30_000,
  });

  const data = useMemo(() => query.data?.items ?? [], [query.data]);

  const columns = useMemo<ColumnDef<AtrativoListItem>[]>(
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
        id: "sub_state",
        header: "Sub-estado",
        cell: ({ row }) => (
          <StageBadge subState={row.original.sub_state} />
        ),
      },
      {
        id: "score",
        header: "Score",
        cell: ({ row }) => (
          <StageBadge score={row.original.score} />
        ),
      },
      {
        id: "routing",
        header: "Estado",
        cell: ({ row }) => (
          <StageBadge routing={row.original.routing} />
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

        {/* Sub-state filter */}
        <select
          value={subState ?? ""}
          onChange={(e) => setSubState(e.target.value || undefined)}
          className="h-7 rounded border bg-background px-2 font-mono text-[12px]"
          aria-label="Filtrar por sub-estado"
        >
          <option value="">Todos sub-estados</option>
          <option value="discovered">Descoberto</option>
          <option value="contacts_found">Contatos</option>
          <option value="signals_gathered">Sinais</option>
          <option value="aguardando_consulta_whatsapp">Aguardando WA</option>
          <option value="whatsapp_in_progress">Em outreach</option>
        </select>

        {/* Optional parent_mar_id text filter */}
        <input
          type="text"
          value={parentMarId ?? ""}
          onChange={(e) => setParentMarId(e.target.value || undefined)}
          placeholder="Destino pai (ID)"
          className="h-7 rounded border bg-background px-2 font-mono text-[12px] w-44"
          aria-label="Filtrar por destino pai"
        />
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto rounded-md border">
        {query.isLoading ? (
          <AtrativoSkeleton />
        ) : query.isError ? (
          <AtrativoError error={query.error} onRetry={() => query.refetch()} />
        ) : data.length === 0 ? (
          <AtrativoEmpty />
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

function AtrativoSkeleton() {
  return (
    <div className="flex flex-col gap-1 p-2" data-testid="atrativo-list-skeleton">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-9 animate-pulse rounded bg-muted" />
      ))}
    </div>
  );
}

function AtrativoEmpty() {
  return (
    <div className="flex flex-col items-center justify-center gap-1 p-12 text-center">
      <h3 className="text-[14px] font-semibold">Sem atrativos</h3>
      <p className="text-[12px] text-muted-foreground">
        Sem atrativos para este filtro.
      </p>
    </div>
  );
}

function AtrativoError({
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
