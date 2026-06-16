"use client";

import { ApiError } from "@/lib/api-client";
import {
  type CostWindowHours,
  formatUsd,
  totalCalls,
  totalTokens,
  totalUsd,
} from "@/lib/cost-api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { useCost } from "./useCost";

/**
 * CostSummary (DASH-04, UI-SPEC §5/§3).
 *
 * The headline totals for the cost view: total USD spend, total tokens, and call
 * count over the window. USD + tokens render in Geist Mono tabular-nums (UI-SPEC:
 * USD/tokens are monospace data). Reads the lane aggregation (`group_by=lane`) and
 * sums across rows — the totals are dimension-independent, so lane is sufficient.
 *
 * View states mirror the charts: Skeleton (loading), empty "Sem dados no período"
 * copy, 401 session-expired, and a retry on other errors.
 */
export function CostSummary({
  windowHours = 24 * 7,
}: {
  windowHours?: CostWindowHours;
}) {
  const { data, isPending, isError, error, refetch } = useCost(
    "lane",
    windowHours,
  );

  if (isPending) {
    return (
      <Skeleton data-testid="cost-summary-skeleton" className="h-24 w-full rounded-lg" />
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

  const rows = data.rows;
  if (rows.length === 0) {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-[14px] font-medium">Sem dados no período</p>
        <p className="text-[12px] text-muted-foreground">
          Ajuste a janela de tempo ou aguarde atividade do pipeline.
        </p>
      </div>
    );
  }

  const usd = totalUsd(rows);
  const tokens = totalTokens(rows);
  const calls = totalCalls(rows);

  return (
    <div
      data-testid="cost-summary"
      className="grid grid-cols-1 gap-4 sm:grid-cols-3"
    >
      <SummaryStat label="Gasto total (USD)">
        <span className="font-mono text-[28px] font-semibold leading-none tabular-nums text-primary">
          {formatUsd(usd)}
        </span>
      </SummaryStat>
      <SummaryStat label="Tokens">
        <span className="font-mono text-[28px] font-semibold leading-none tabular-nums">
          {tokens.toLocaleString("pt-BR")}
        </span>
      </SummaryStat>
      <SummaryStat label="Chamadas LLM">
        <span className="font-mono text-[28px] font-semibold leading-none tabular-nums">
          {calls.toLocaleString("pt-BR")}
        </span>
      </SummaryStat>
    </div>
  );
}

function SummaryStat({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-md border p-4">
      <span className="text-[12px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {children}
    </div>
  );
}
