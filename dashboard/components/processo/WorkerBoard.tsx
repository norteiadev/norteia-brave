"use client";

import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  WORKERS_REFETCH_INTERVAL_MS,
  fetchWorkers,
  workersKeys,
} from "@/lib/workers-api";
import { cn } from "@/lib/utils";

/**
 * WorkerBoard (D-05, §15.7).
 *
 * Live-polled (10s) Celery worker status: per-worker tile (UP/DOWN), queue
 * depths, and beat schedule summary. Gracefully degrades when the broker is
 * offline — renders an amber banner ("Broker indisponível") WITHOUT throwing an
 * error, because broker_reachable=false is an expected operational state (the
 * endpoint itself is reachable; only Redis/AMQP is down).
 *
 * Trust boundary: read-only observability only. No Celery control actions
 * (no revoke/terminate) — T-08-18 disposition: accept.
 */
export function WorkerBoard() {
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: workersKeys.data(),
    queryFn: fetchWorkers,
    refetchInterval: WORKERS_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });

  if (isPending) {
    return (
      <div
        data-testid="worker-board-skeleton"
        className="grid grid-cols-1 gap-4 sm:grid-cols-3"
      >
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-24 rounded-lg" />
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

  const { broker_reachable, workers, queues, beat_schedule } = data;

  return (
    <div
      data-testid="worker-board"
      className="flex flex-col gap-4"
    >
      {/* Broker-down banner — shown when Redis/broker is offline */}
      {!broker_reachable && (
        <div
          data-testid="broker-down-banner"
          role="alert"
          className="rounded-md border border-amber-500/50 bg-amber-500/10 px-4 py-2 text-[14px] font-medium text-amber-600"
        >
          Broker indisponível — nenhum worker respondeu
        </div>
      )}

      {/* Worker tiles */}
      {workers.length === 0 && !isPending ? (
        <p className="text-[14px] text-muted-foreground">
          Nenhum worker ativo
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {workers.map((w) => {
            const isUp = w.status === "up";
            const shortName = w.hostname.replace(/^celery@/, "");
            return (
              <div
                key={w.hostname}
                data-testid={`worker-tile-${shortName}`}
                className="flex flex-col gap-1 rounded-lg border bg-card p-4"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[13px] font-medium">
                    {shortName}
                  </span>
                  <span
                    className={cn(
                      "rounded-full px-2 py-0.5 text-[11px] font-bold uppercase",
                      isUp
                        ? "bg-[var(--status-mar)]/15 text-[var(--status-mar)]"
                        : "bg-[var(--status-descarte)]/15 text-[var(--status-descarte)]",
                    )}
                  >
                    {isUp ? "UP" : "DOWN"}
                  </span>
                </div>
                <div className="flex gap-4 text-[12px] text-muted-foreground">
                  <span>
                    <span className="font-semibold tabular-nums text-foreground">
                      {w.active_count}
                    </span>{" "}
                    ativas
                  </span>
                  <span>
                    <span className="font-semibold tabular-nums text-foreground">
                      {w.reserved_count}
                    </span>{" "}
                    reservadas
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Queue depths */}
      <div className="flex flex-wrap gap-6 text-[12px] text-muted-foreground">
        <span>
          <span className="font-semibold uppercase tracking-wide">
            brave.sweep
          </span>
          :{" "}
          <span className="font-mono tabular-nums text-foreground">
            {queues["brave.sweep"] !== null ? queues["brave.sweep"] : "—"}
          </span>
        </span>
        <span>
          <span className="font-semibold uppercase tracking-wide">celery</span>:{" "}
          <span className="font-mono tabular-nums text-foreground">
            {queues["celery"] !== null ? queues["celery"] : "—"}
          </span>
        </span>
        <span>
          <span className="font-semibold uppercase tracking-wide">beat</span>:{" "}
          <span className="font-mono tabular-nums text-foreground">
            {beat_schedule.entries}
          </span>{" "}
          entradas agendadas · fila:{" "}
          <span className="font-mono">{beat_schedule.queues.join(", ")}</span>
        </span>
      </div>
    </div>
  );
}
