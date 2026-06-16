"use client";

import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { ApiError } from "@/lib/api-client";
import {
  type CostGroupBy,
  type CostRow,
  type CostWindowHours,
  formatUsd,
  totalUsd,
} from "@/lib/cost-api";
import { Button } from "@/components/ui/button";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";

import { useCost } from "./useCost";

/**
 * Shared cost bar-chart body (DASH-04, UI-SPEC §5).
 *
 * A Recharts bar of summed USD spend per group (lane or model) via the shadcn
 * `chart` wrapper, primary-blue series — the ONE place primary blue is a large
 * fill (UI-SPEC). The total USD sits above the chart as a Geist Mono tabular-nums
 * readout so the figure is assertable even where the SVG has no measured size
 * (jsdom). `CostByLaneChart` / `CostByModelChart` are thin wrappers that pin the
 * `groupBy` and the test id.
 *
 * View states mirror the monitor slice: Skeleton (loading), empty "Sem dados no
 * período" copy, 401 session-expired, and a retry on other errors.
 */
const chartConfig = {
  usd_cost: { label: "USD", color: "var(--primary)" },
} satisfies ChartConfig;

export interface CostBarChartProps {
  groupBy: CostGroupBy;
  windowHours: CostWindowHours;
  /** Section heading + the data-testid root (e.g. "cost-by-lane"). */
  testId: string;
  emptyKeyLabel: string;
}

export function CostBarChart({
  groupBy,
  windowHours,
  testId,
  emptyKeyLabel,
}: CostBarChartProps) {
  const { data, isPending, isError, error, refetch } = useCost(
    groupBy,
    windowHours,
  );

  if (isPending) {
    return (
      <Skeleton
        data-testid={`${testId}-skeleton`}
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
        <Button size="sm" variant="outline" onClick={() => refetch()}>
          Tentar novamente
        </Button>
      </div>
    );
  }

  const rows: CostRow[] = data.rows;
  const empty = rows.length === 0 || rows.every((r) => r.usd_cost === 0);
  if (empty) {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-[14px] font-medium">Sem dados no período</p>
        <p className="text-[12px] text-muted-foreground">
          Ajuste a janela de tempo ou aguarde atividade do pipeline.
        </p>
      </div>
    );
  }

  // Largest spenders first; truncate the displayed key so model slugs stay legible.
  const series = [...rows]
    .sort((a, b) => b.usd_cost - a.usd_cost)
    .map((r) => ({
      key: r.key,
      label: r.key.length > 22 ? `…${r.key.slice(-21)}` : r.key,
      usd_cost: r.usd_cost,
      tokens: r.tokens,
      count: r.count,
    }));

  return (
    <div data-testid={testId} className="flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[28px] font-semibold leading-none tabular-nums text-primary">
          {formatUsd(totalUsd(rows))}
        </span>
        <span className="text-[12px] uppercase tracking-wide text-muted-foreground">
          Gasto total · por {emptyKeyLabel}
        </span>
      </div>

      <ChartContainer config={chartConfig} className="h-64 w-full">
        <BarChart accessibilityLayer data={series}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} />
          <YAxis tickLine={false} axisLine={false} width={48} />
          <ChartTooltip content={<ChartTooltipContent />} />
          <Bar dataKey="usd_cost" fill="var(--color-usd_cost)" radius={4} />
        </BarChart>
      </ChartContainer>
    </div>
  );
}
