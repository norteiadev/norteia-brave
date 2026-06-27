"use client";

import type { EntityMetric, PainelEntityType } from "@/lib/painel-data";

interface MetricCardProps {
  label: string;
  type: PainelEntityType;
  m: EntityMetric;
}

/**
 * One presentational metric card (Destinos or Atrativos). Shows the in-scope
 * total, sincronizados (green / --status-mar), falhas (red / --status-descarte)
 * and a progresso% bar whose fill width tracks `m.pct`. Numbers use Geist Mono.
 * Tokens are the scoped painel CSS vars only — no hardcoded hex.
 */
function MetricCard({ label, type, m }: MetricCardProps) {
  return (
    <div className="flex-1 rounded-[11px] border border-[var(--painel-border-outer)] bg-[var(--card)] p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="h-[9px] w-[9px] rounded-[3px] bg-[var(--painel-navy)]" />
          <span className="text-[13px] font-semibold">{label}</span>
        </div>
        <div className="flex items-baseline gap-1.5">
          <span
            data-testid={`metric-${type}-total`}
            className="font-mono text-[21px] font-semibold tracking-[-0.5px]"
          >
            {m.total}
          </span>
          <span className="text-[11px] text-[var(--painel-muted-2)]">
            no escopo
          </span>
        </div>
      </div>
      <div className="mt-[11px] flex items-end gap-[18px]">
        <div>
          <div className="mb-0.5 text-[10px] uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
            Sincronizados
          </div>
          <div
            data-testid={`metric-${type}-mar`}
            className="font-mono text-[15px] font-semibold text-[var(--status-mar)]"
          >
            {m.mar}
          </div>
        </div>
        <div>
          <div className="mb-0.5 text-[10px] uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
            Falhas
          </div>
          <div
            data-testid={`metric-${type}-falha`}
            className="font-mono text-[15px] font-semibold text-[var(--status-descarte)]"
          >
            {m.falha}
          </div>
        </div>
        <div className="min-w-[60px] flex-1">
          <div className="mb-1 flex justify-between text-[10px] text-[var(--painel-muted-2)]">
            <span>Progresso</span>
            <span data-testid={`metric-${type}-pct`} className="font-mono">
              {m.pct}%
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-[var(--painel-chip)]">
            <div
              className="h-full rounded-full bg-[var(--status-mar)]"
              style={{ width: `${m.pct}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

export interface PainelMetricsProps {
  destino: EntityMetric;
  atrativo: EntityMetric;
}

/**
 * The Painel header's two metric cards. Purely presentational and props-driven:
 * the container (plan 17-05) sources the `EntityMetric`s from `usePainelMetrics`.
 * This component never calls a hook or `buildMetrics`.
 */
export function PainelMetrics({ destino, atrativo }: PainelMetricsProps) {
  return (
    <div className="flex gap-[14px]">
      <MetricCard label="Destinos" type="destino" m={destino} />
      <MetricCard label="Atrativos" type="atrativo" m={atrativo} />
    </div>
  );
}
