"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  COST_WINDOWS,
  type CostGroupBy,
  type CostRow,
  costKeys,
  fetchCost,
  formatUsd,
  totalCalls,
  totalTokens,
  totalUsd,
} from "@/lib/cost-api";

/** Default window index into COST_WINDOWS → '7d'. */
const DEFAULT_WINDOW_INDEX = 1;

const GROUP_OPTIONS: { key: CostGroupBy; label: string }[] = [
  { key: "lane", label: "Por lane" },
  { key: "model", label: "Por modelo" },
];

/** Bar fill color: navy for lanes, a cyan-ish blue for models (design line 952). */
const BAR_COLOR_LANE = "var(--painel-navy)";
const BAR_COLOR_MODEL = "oklch(0.5 0.13 200)";

function fmtNum(value: number): string {
  return Math.round(value).toLocaleString("pt-BR");
}

/**
 * Custo & LLM view (Painel light theme). Reads the real cost aggregation through
 * the BFF (`GET /api/v1/cost`) via TanStack Query, re-skinned to the light design
 * (design lines 343-376). Two segmented controls drive the query: the group
 * dimension (lane / model) and the time window (24h / 7d / 30d / Tudo). Rows are
 * sorted desc by USD spend and rendered as proportional bars.
 *
 * Purely token-driven (scoped `--painel-*` vars); the only literal is the model
 * bar accent (no token exists for it), kept inline per the design.
 */
export function PainelCusto() {
  const [group, setGroup] = useState<CostGroupBy>("lane");
  const [windowIndex, setWindowIndex] = useState(DEFAULT_WINDOW_INDEX);

  const win = COST_WINDOWS[windowIndex];
  const hours = win.hours;

  const { data } = useQuery({
    queryKey: costKeys.data(group, hours),
    queryFn: () => fetchCost(group, hours),
  });

  const rows: CostRow[] = data?.rows ?? [];
  const sorted = [...rows].sort((a, b) => b.usd_cost - a.usd_cost);

  const usdTotal = totalUsd(rows);
  const tokTotal = totalTokens(rows);
  const callTotal = totalCalls(rows);
  const maxUsd = sorted.length ? Math.max(...sorted.map((r) => r.usd_cost)) : 1;

  const groupLabel = group === "lane" ? "lane" : "modelo";
  const barColor = group === "lane" ? BAR_COLOR_LANE : BAR_COLOR_MODEL;

  return (
    <div className="h-full overflow-y-auto px-[22px] pb-7 pt-5">
      {/* Segmented controls: group + window */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex gap-0.5 rounded-[9px] bg-[var(--painel-chip)] p-[3px]">
          {GROUP_OPTIONS.map((opt) => (
            <Seg
              key={opt.key}
              testId={`cost-group-${opt.key}`}
              active={group === opt.key}
              onClick={() => setGroup(opt.key)}
            >
              {opt.label}
            </Seg>
          ))}
        </div>
        <div className="inline-flex gap-0.5 rounded-[9px] bg-[var(--painel-chip)] p-[3px]">
          {COST_WINDOWS.map((w, i) => (
            <Seg
              key={w.label}
              testId={`cost-window-${w.label}`}
              active={windowIndex === i}
              onClick={() => setWindowIndex(i)}
            >
              {w.label}
            </Seg>
          ))}
        </div>
      </div>

      {/* Summary cards */}
      <div className="mb-[18px] grid grid-cols-3 gap-[14px]">
        <SummaryCard label="Gasto total (USD)">
          <span
            data-testid="cost-total-usd"
            className="font-mono text-[26px] font-semibold tracking-[-0.5px] text-[var(--painel-navy)]"
          >
            {formatUsd(usdTotal)}
          </span>
        </SummaryCard>
        <SummaryCard label="Tokens">
          <span
            data-testid="cost-total-tokens"
            className="font-mono text-[26px] font-semibold tracking-[-0.5px]"
          >
            {tokTotal.toLocaleString("pt-BR")}
          </span>
        </SummaryCard>
        <SummaryCard label="Chamadas LLM">
          <span
            data-testid="cost-total-calls"
            className="font-mono text-[26px] font-semibold tracking-[-0.5px]"
          >
            {callTotal.toLocaleString("pt-BR")}
          </span>
        </SummaryCard>
      </div>

      {/* Bars card */}
      <div className="max-w-[860px] rounded-[13px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-5 py-[18px]">
        <div className="mb-4 flex items-baseline justify-between">
          <span className="text-[13px] font-semibold">
            Gasto por {groupLabel}
          </span>
          <span className="text-[11px] text-[var(--painel-muted-2)]">
            janela {win.label}
          </span>
        </div>
        {sorted.length === 0 ? (
          <p
            data-testid="cost-bars"
            className="text-[12.5px] text-[var(--painel-muted)]"
          >
            Sem dados na janela
          </p>
        ) : (
          <div
            data-testid="cost-bars"
            className="flex flex-col gap-[15px]"
          >
            {sorted.map((row) => {
              const width = Math.max(2, (row.usd_cost / maxUsd) * 100);
              const pct = usdTotal
                ? ((row.usd_cost / usdTotal) * 100).toFixed(1)
                : "0.0";
              return (
                <div
                  key={row.key}
                  data-testid="cost-bar"
                  className="flex flex-col gap-[7px]"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-[9px]">
                      <span
                        className="h-[9px] w-[9px] flex-shrink-0 rounded-[3px]"
                        style={{ background: barColor }}
                      />
                      <span className="overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[12.5px] font-semibold">
                        {row.key}
                      </span>
                    </div>
                    <span className="whitespace-nowrap font-mono text-[13px] font-semibold text-[var(--painel-navy)]">
                      {formatUsd(row.usd_cost)}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="h-[9px] flex-1 overflow-hidden rounded-full bg-[var(--painel-chip)]">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${width}%`,
                          background: barColor,
                          transition: "width .35s ease",
                        }}
                      />
                    </div>
                    <span className="w-[46px] text-right font-mono text-[11px] text-[var(--painel-muted-2)]">
                      {pct}%
                    </span>
                  </div>
                  <div className="flex items-center gap-4 font-mono text-[10.5px] text-[var(--painel-muted-2)]">
                    <span>{fmtNum(row.tokens)} tokens</span>
                    <span>{fmtNum(row.count)} chamadas</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function Seg({
  testId,
  active,
  onClick,
  children,
}: {
  testId: string;
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      data-active={active ? "true" : "false"}
      aria-pressed={active}
      onClick={onClick}
      className={`flex h-7 items-center rounded-[7px] px-[11px] text-[12.5px] font-semibold transition-colors ${
        active
          ? "bg-[var(--card)] text-[var(--painel-navy)] shadow-sm"
          : "text-[var(--painel-muted)]"
      }`}
    >
      {children}
    </button>
  );
}

function SummaryCard({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-[12px] border border-[var(--painel-border-outer)] bg-[var(--card)] px-[17px] py-[15px]">
      <div className="mb-[7px] text-[10.5px] uppercase tracking-[0.4px] text-[var(--painel-muted-2)]">
        {label}
      </div>
      {children}
    </div>
  );
}
