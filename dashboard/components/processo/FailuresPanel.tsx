"use client";

import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  WORKERS_REFETCH_INTERVAL_MS,
  fetchFailures,
  workersKeys,
} from "@/lib/workers-api";

/**
 * FailuresPanel (D-05, §15.7).
 *
 * PoisonQuarantine recent failures panel. Lists the most recent quarantined
 * Celery task items: task_name (font-mono), truncated error message, and
 * quarantine timestamp. Shows a totals footer + by_task breakdown chips.
 *
 * Polls every 10s (same interval as WorkerBoard) to surface new failures fast.
 */
export function FailuresPanel() {
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: workersKeys.failures(),
    queryFn: () => fetchFailures(),
    refetchInterval: WORKERS_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });

  if (isPending) {
    return (
      <div
        data-testid="failures-panel-skeleton"
        className="flex flex-col gap-2"
      >
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-12 rounded-md" />
        ))}
      </div>
    );
  }

  if (isError) {
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

  const { total, by_task, items } = data;

  return (
    <div data-testid="failures-panel" className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[14px] font-semibold">Quarentena recente</h2>
        <span className="text-[12px] text-muted-foreground">
          Total:{" "}
          <span className="font-semibold tabular-nums text-foreground">
            {total}
          </span>{" "}
          {total === 1 ? "falha" : "falhas"}
        </span>
      </div>

      {/* by_task chips */}
      {Object.keys(by_task).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(by_task).map(([taskName, count]) => (
            <span
              key={taskName}
              className="rounded-full border border-destructive/30 bg-destructive/10 px-2 py-0.5 font-mono text-[11px] text-destructive"
            >
              {taskName}: {count}
            </span>
          ))}
        </div>
      )}

      {/* Failure items */}
      {total === 0 ? (
        <p
          data-testid="failures-empty"
          className="text-[14px] text-muted-foreground"
        >
          Nenhuma falha recente
        </p>
      ) : (
        <ul className="flex flex-col gap-2" role="list">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex flex-col gap-0.5 rounded-md border bg-card p-3"
            >
              <span className="font-mono text-[12px] font-semibold text-destructive">
                {item.task_name}
              </span>
              <span className="text-[12px] text-muted-foreground">
                {item.error_message.slice(0, 100)}
                {item.error_message.length > 100 ? "…" : ""}
              </span>
              {item.quarantined_at && (
                <span className="font-mono text-[11px] text-muted-foreground tabular-nums">
                  {formatTs(item.quarantined_at)}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function formatTs(ts: string): string {
  try {
    return new Date(ts).toLocaleString("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return ts;
  }
}
