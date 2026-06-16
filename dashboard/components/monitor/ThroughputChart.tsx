"use client";

import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { ApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";

import { useMonitor } from "./useMonitor";

/**
 * ThroughputChart (DASH-02, UI-SPEC §5).
 *
 * A Recharts bar chart of the per-routing Rio distribution (the layers that make up
 * the windowed throughput), drawn in the primary-blue series via the shadcn `chart`
 * wrapper. The prominent windowed `throughput` count sits above the chart as a
 * Display-size readout so the figure is assertable even where the SVG has no
 * measured size (jsdom). Polls live via `useMonitor` (`refetchInterval`).
 *
 * The primary series is the ONE place the primary blue acts as a large fill
 * (UI-SPEC: chart primary series is allowed); status colors stay off it.
 */
const chartConfig = {
  count: { label: "Registros", color: "var(--primary)" },
} satisfies ChartConfig;

export function ThroughputChart({ sinceHours = 24 }: { sinceHours?: number }) {
  const { data, isPending, isError, error, refetch } = useMonitor(sinceHours);

  if (isPending) {
    return <Skeleton data-testid="throughput-skeleton" className="h-64 w-full rounded-lg" />;
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

  const { volume, throughput } = data;
  const series = [
    { layer: "Em curso", count: volume.rio_count.in_progress },
    { layer: "Mar", count: volume.rio_count.mar },
    { layer: "DLQ", count: volume.rio_count.dlq },
    { layer: "Descarte", count: volume.rio_count.descarte },
  ];

  const empty = throughput === 0 && series.every((s) => s.count === 0);
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

  return (
    <div data-testid="throughput-chart" className="flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <span className="text-[28px] font-semibold leading-none tabular-nums text-primary">
          {throughput.toLocaleString("pt-BR")}
        </span>
        <span className="text-[12px] uppercase tracking-wide text-muted-foreground">
          Registros processados · {sinceHours}h
        </span>
      </div>

      <ChartContainer config={chartConfig} className="h-64 w-full">
        <BarChart accessibilityLayer data={series}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="layer" tickLine={false} axisLine={false} />
          <YAxis tickLine={false} axisLine={false} width={36} />
          <ChartTooltip content={<ChartTooltipContent />} />
          <Bar dataKey="count" fill="var(--color-count)" radius={4} />
        </BarChart>
      </ChartContainer>
    </div>
  );
}
