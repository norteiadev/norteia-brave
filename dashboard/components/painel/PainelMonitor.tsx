"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchFunnels, funnelKeys, toStageBars } from "@/lib/funnels-api";
import {
  MONITOR_REFETCH_INTERVAL_MS,
  fetchMonitor,
  monitorKeys,
} from "@/lib/monitor-api";

/**
 * PainelMonitor — the "Monitor & Funis" painel view (phase H).
 *
 * Folds the old dark /monitor + /funnels routes into one painel-light surface:
 *   - volume tiles + throughput + failure/quality alerts (GET /api/v1/monitor)
 *   - the per-layer funnel bars ingested → in_progress → mar/dlq/descarte
 *     (GET /api/v1/funnels, collapsed via toStageBars)
 *
 * Read-only, self-loading (TanStack Query); the monitor block polls at the same
 * 10s cadence the ops console uses elsewhere. Pure `--painel-*` token styling.
 */

const SINCE_HOURS = 24;

export function PainelMonitor() {
  const { data: monitor } = useQuery({
    queryKey: monitorKeys.data(SINCE_HOURS),
    queryFn: () => fetchMonitor(SINCE_HOURS),
    refetchInterval: MONITOR_REFETCH_INTERVAL_MS,
  });

  const { data: funnel } = useQuery({
    queryKey: funnelKeys.data({}),
    queryFn: () => fetchFunnels({}),
  });

  const bars = funnel ? toStageBars(funnel) : [];
  const maxBar = Math.max(1, ...bars.map((b) => b.count));
  const funnelEmpty = bars.length > 0 && bars.every((b) => b.count === 0);

  return (
    <div className="h-full overflow-y-auto px-[22px] pb-7 pt-5">
      {/* Volume tiles */}
      <div className="mb-[14px] grid grid-cols-2 gap-[14px] sm:grid-cols-4">
        <Tile
          label="Nascente"
          testId="monitor-nascente"
          value={monitor?.volume.nascente_count}
        />
        <Tile
          label="Rio · em progresso"
          testId="monitor-rio-inprogress"
          value={monitor?.volume.rio_count.in_progress}
        />
        <Tile
          label="Mar · publicado"
          testId="monitor-mar"
          value={monitor?.volume.mar_count}
          accent="oklch(0.5 0.13 150)"
        />
        <Tile
          label="DLQ · revisão"
          testId="monitor-dlq"
          value={monitor?.volume.rio_count.dlq}
          accent="oklch(0.55 0.13 75)"
        />
      </div>

      {/* Throughput + alerts */}
      <div className="mb-[18px] grid grid-cols-2 gap-[14px] sm:grid-cols-3">
        <Tile
          label={`Throughput (${SINCE_HOURS}h)`}
          testId="monitor-throughput"
          value={monitor?.throughput}
        />
        <Tile
          label="Falhas · quarentena"
          testId="monitor-failures"
          value={monitor?.alerts.failures}
          accent={
            monitor?.alerts.failures ? "oklch(0.55 0.20 27)" : undefined
          }
        />
        <div className="rounded-[12px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[17px] py-[15px]">
          <div className="mb-[9px] text-[10.5px] uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
            Qualidade WhatsApp
          </div>
          <span
            data-testid="monitor-quality"
            data-alerting={monitor?.alerts.quality ? "true" : "false"}
            className="inline-flex rounded-[5px] px-[9px] py-[3px] text-[12px] font-semibold"
            style={
              monitor?.alerts.quality
                ? {
                    color: "oklch(0.5 0.18 27)",
                    background:
                      "color-mix(in oklch, oklch(0.55 0.20 27) 13%, white)",
                  }
                : {
                    color: "oklch(0.5 0.13 150)",
                    background:
                      "color-mix(in oklch, oklch(0.62 0.17 150) 14%, white)",
                  }
            }
          >
            {monitor?.alerts.quality ? "RED · auto-pausa" : "OK"}
          </span>
        </div>
      </div>

      {/* Funnel */}
      <div className="max-w-[820px] rounded-[13px] border border-[var(--painel-border-outer)] bg-[var(--card)] p-[18px]">
        <div className="mb-[14px] text-[13px] font-semibold text-[var(--painel-text)]">
          Funil por camada
        </div>
        {funnelEmpty ? (
          <div
            data-testid="funnel-empty"
            className="py-8 text-center text-[13px] text-[var(--painel-muted-2)]"
          >
            Sem registros no período.
          </div>
        ) : (
          <div className="flex flex-col gap-[10px]">
            {bars.map((b) => (
              <div
                key={b.stage}
                data-testid="funnel-bar"
                data-stage={b.stage}
                className="flex items-center gap-3"
              >
                <span className="w-[104px] flex-shrink-0 text-[12px] text-[var(--painel-muted)]">
                  {b.label}
                </span>
                <div className="h-[22px] flex-1 overflow-hidden rounded-[6px] bg-[var(--painel-chip)]">
                  <div
                    className="h-full rounded-[6px] bg-[var(--painel-navy)]"
                    style={{ width: `${(b.count / maxBar) * 100}%` }}
                  />
                </div>
                <span className="w-[68px] flex-shrink-0 text-right font-mono text-[12.5px] font-semibold text-[var(--painel-text)]">
                  {b.count.toLocaleString("pt-BR")}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Tile({
  label,
  value,
  testId,
  accent,
}: {
  label: string;
  value?: number;
  testId: string;
  accent?: string;
}) {
  return (
    <div className="rounded-[12px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[17px] py-[15px]">
      <div className="mb-[7px] text-[10.5px] uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
        {label}
      </div>
      <span
        data-testid={testId}
        className="font-mono text-[26px] font-semibold tracking-[-0.5px]"
        style={{ color: accent ?? "var(--painel-navy)" }}
      >
        {value == null ? "—" : value.toLocaleString("pt-BR")}
      </span>
    </div>
  );
}
