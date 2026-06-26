"use client";

import { useQuery } from "@tanstack/react-query";

import {
  ENGINE_REFETCH_INTERVAL_MS,
  fetchTASweepProgress,
  taSweepKeys,
  type TASweepState,
} from "@/lib/ta-sweep-api";

/**
 * TASweepProgress — live national TripAdvisor sweep monitor (TA-12).
 *
 * Read-only panel that polls GET /api/v1/tripadvisor/sweep/progress every 10s
 * (same cadence as EngineControl) and renders a pages/334 progress bar,
 * attractions ingested, current offset, error count, and a terminal-state pill
 * (running / done / stopped_needs_bootstrap / idle). 401-safe: a session-expired
 * response leaves `data` undefined and the panel renders its idle shell without
 * crashing. Mirrors EngineControl's poll + progress-bar + status-pill posture.
 */

const STATE_LABEL: Record<TASweepState, string> = {
  idle: "Parado",
  running: "Varrendo",
  done: "Concluído",
  stopped_needs_bootstrap: "Precisa bootstrap",
};

const STATE_COLOR: Record<TASweepState, string> = {
  idle: "text-muted-foreground",
  running: "text-emerald-600",
  done: "text-sky-600",
  stopped_needs_bootstrap: "text-amber-600",
};

const STATE_DOT: Record<TASweepState, string> = {
  idle: "bg-muted-foreground",
  running: "bg-emerald-500 animate-pulse",
  done: "bg-sky-500",
  stopped_needs_bootstrap: "bg-amber-500 animate-pulse",
};

export function TASweepProgress() {
  const { data, isPending } = useQuery({
    queryKey: taSweepKeys.status,
    queryFn: fetchTASweepProgress,
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });

  const state: TASweepState = data?.state ?? "idle";
  const progressPct =
    data && data.pages_total > 0
      ? Math.round((data.pages_done / data.pages_total) * 100)
      : 0;

  return (
    <section
      className="rounded-md border p-4"
      data-testid="ta-sweep-progress"
      aria-label="Progresso da varredura TripAdvisor"
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span
            className={`inline-block h-2.5 w-2.5 rounded-full ${STATE_DOT[state]}`}
            aria-hidden
          />
          <div>
            <h2 className="text-[14px] font-semibold">Varredura TripAdvisor</h2>
            <p
              className="text-[12px] text-muted-foreground"
              data-testid="ta-sweep-state-line"
            >
              {isPending ? "Carregando…" : "Coleta nacional · oa{N} · 10s"}
            </p>
          </div>
        </div>

        {/* Terminal-state pill */}
        <span
          data-testid="ta-sweep-state"
          className={`text-[12px] font-medium ${STATE_COLOR[state]}`}
        >
          {STATE_LABEL[state]}
        </span>
      </div>

      {/* Progress — pages fetched this run (pages_done / pages_total) */}
      {data && data.pages_total > 0 && (
        <div className="mt-3" data-testid="ta-sweep-bar">
          <div className="mb-1 flex justify-between text-[11px] text-muted-foreground tabular-nums">
            <span data-testid="ta-sweep-pages">
              Páginas {data.pages_done}/{data.pages_total}
            </span>
            <span data-testid="ta-sweep-pct">{progressPct}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-emerald-500 transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Live counts — what the sweep is producing */}
      {data && (
        <div
          className="mt-4 grid grid-cols-3 gap-3"
          data-testid="ta-sweep-counts"
        >
          <CountTile
            label="Atrativos"
            value={data.attractions_ingested}
            testId="ta-sweep-attractions"
          />
          <CountTile
            label="Offset atual"
            value={data.current_offset}
            testId="ta-sweep-offset"
          />
          <CountTile
            label="Erros"
            value={data.error_count}
            testId="ta-sweep-errors"
          />
        </div>
      )}
    </section>
  );
}

function CountTile({
  label,
  value,
  testId,
}: {
  label: string;
  value: number;
  testId: string;
}) {
  return (
    <div className="rounded-md border px-3 py-2" data-testid={testId}>
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-[18px] font-semibold tabular-nums">{value}</div>
    </div>
  );
}
