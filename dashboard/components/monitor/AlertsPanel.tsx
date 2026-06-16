"use client";

import { ApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { useMonitor } from "./useMonitor";

/**
 * AlertsPanel (DASH-02, UI-SPEC).
 *
 * The failure-alert surface. UI-SPEC reserves the `destructive` red specifically
 * for failure alerts — so the panel turns destructive when the poison-quarantine
 * count is > 0 OR the WhatsApp quality flag is RED. When neither fires it renders a
 * calm "tudo certo" state. Polls live via `useMonitor` (`refetchInterval`).
 */
export function AlertsPanel({ sinceHours = 24 }: { sinceHours?: number }) {
  const { data, isPending, isError, error, refetch } = useMonitor(sinceHours);

  if (isPending) {
    return <Skeleton data-testid="alerts-skeleton" className="h-24 rounded-lg" />;
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

  const { failures, quality } = data.alerts;
  const alerting = failures > 0 || quality;

  if (!alerting) {
    return (
      <div
        data-testid="alerts-ok"
        className="flex flex-col gap-1 rounded-lg border bg-card p-4"
      >
        <span className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
          Alertas
        </span>
        <span className="text-[14px] text-emerald-500">
          Sem falhas no período
        </span>
      </div>
    );
  }

  return (
    <div
      data-testid="alerts-failure"
      role="alert"
      className="flex flex-col gap-2 rounded-lg border border-destructive bg-destructive/10 p-4 text-destructive"
    >
      <span className="text-[12px] font-semibold uppercase tracking-wide">
        Alertas de falha
      </span>
      {failures > 0 ? (
        <span className="text-[14px]">
          <span className="text-[28px] font-semibold leading-none tabular-nums">
            {failures.toLocaleString("pt-BR")}
          </span>{" "}
          mensagens em quarentena (poison)
        </span>
      ) : null}
      {quality ? (
        <span className="text-[14px] font-medium">
          Qualidade WhatsApp RED — envios pausados
        </span>
      ) : null}
    </div>
  );
}
