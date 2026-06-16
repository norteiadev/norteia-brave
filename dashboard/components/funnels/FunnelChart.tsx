"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, XAxis, YAxis } from "recharts";
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "@/lib/api-client";
import {
  type FunnelData,
  type FunnelEntityType,
  type FunnelFilters,
  funnelKeys,
  fetchFunnels,
  isFunnelEmpty,
  toStageBars,
} from "@/lib/funnels-api";
import { UF_PRIORITY } from "@/lib/dlq-api";
import { Button } from "@/components/ui/button";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * FunnelChart — destinos/atrativos stage bars by UF/source (DASH-05, UI-SPEC).
 *
 * A Recharts bar of the per-stage record counts (ingerido → em progresso →
 * mar/dlq/descarte) via the shadcn `chart` wrapper, primary-blue series (the one
 * large-fill use UI-SPEC allows). Filters for entity_type (destinos/atrativos)
 * and UF scope the GROUP BY through the read-only `GET /api/v1/funnels` aggregate
 * — aggregate counts only, no PII.
 *
 * View states mirror the cost slice: Skeleton (loading), empty "Sem dados no
 * período" copy, 401 session-expired, and a retry on other errors.
 */
const chartConfig = {
  count: { label: "registros", color: "var(--primary)" },
} satisfies ChartConfig;

const ENTITY_FILTERS: { label: string; value: FunnelEntityType | null }[] = [
  { label: "Todos", value: null },
  { label: "Destinos", value: "destination" },
  { label: "Atrativos", value: "attraction" },
];

export function FunnelChart() {
  const [entityType, setEntityType] = useState<FunnelEntityType | null>(null);
  const [uf, setUf] = useState<string | null>(null);

  const filters: FunnelFilters = { entityType, uf };
  const { data, isPending, isError, error, refetch } = useQuery<FunnelData>({
    queryKey: funnelKeys.data(filters),
    queryFn: () => fetchFunnels(filters),
    refetchOnWindowFocus: false,
  });

  return (
    <div data-testid="funnel-chart" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1" role="group" aria-label="Lane">
          {ENTITY_FILTERS.map((f) => (
            <Button
              key={f.label}
              size="sm"
              variant={f.value === entityType ? "default" : "outline"}
              onClick={() => setEntityType(f.value)}
            >
              {f.label}
            </Button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="UF">
          <Button
            size="sm"
            variant={uf === null ? "default" : "outline"}
            className="h-7 font-mono text-[12px]"
            onClick={() => setUf(null)}
          >
            BR
          </Button>
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
      </div>

      <FunnelChartBody
        data={data}
        isPending={isPending}
        isError={isError}
        error={error}
        onRetry={() => refetch()}
      />
    </div>
  );
}

function FunnelChartBody({
  data,
  isPending,
  isError,
  error,
  onRetry,
}: {
  data: FunnelData | undefined;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  if (isPending) {
    return (
      <Skeleton
        data-testid="funnel-chart-skeleton"
        className="h-64 w-full rounded-lg"
      />
    );
  }

  if (isError) {
    if (error instanceof ApiError && error.status === 401) {
      return (
        <p className="text-[14px] text-muted-foreground">
          Sessão expirada ou token inválido
        </p>
      );
    }
    return (
      <div className="flex flex-col items-start gap-2">
        <p className="text-[14px] text-muted-foreground">
          Não foi possível carregar
        </p>
        <Button size="sm" variant="outline" onClick={onRetry}>
          Tentar novamente
        </Button>
      </div>
    );
  }

  if (!data || isFunnelEmpty(data)) {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-[14px] font-medium">Sem dados no período</p>
        <p className="text-[12px] text-muted-foreground">
          Ajuste a janela de tempo ou aguarde atividade do pipeline.
        </p>
      </div>
    );
  }

  const series = toStageBars(data);

  return (
    <ChartContainer config={chartConfig} className="h-64 w-full">
      <BarChart accessibilityLayer data={series}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="label" tickLine={false} axisLine={false} />
        <YAxis tickLine={false} axisLine={false} width={48} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar dataKey="count" fill="var(--color-count)" radius={4}>
          <LabelList
            dataKey="count"
            position="top"
            className="fill-foreground font-mono text-[11px] tabular-nums"
          />
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}
