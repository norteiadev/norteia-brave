"use client";

import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  COST_WINDOWS,
  type CostData,
  type CostGroupBy,
  type CostRow,
  costKeys,
  fetchCost,
  totalCalls,
  totalTokens,
  totalUsd,
} from "@/lib/cost-api";

import { CostMeter } from "./CostMeter";
import { useCurrency } from "./currency";

/** Default window index into COST_WINDOWS → '7d'. */
const DEFAULT_WINDOW_INDEX = 1;

const GROUP_OPTIONS: { key: CostGroupBy; label: string }[] = [
  { key: "lane", label: "Por lane" },
  { key: "model", label: "Por modelo" },
];

// LLM calls cost fractions of a cent, so their figures keep 4 decimals.
const LLM_DIGITS = { digits: 4 };

function fmtNum(value: number): string {
  return Math.round(value).toLocaleString("pt-BR");
}

/**
 * Custo & LLM view (#82 Phase 0). LLM spend from `GET /api/v1/cost` grouped by
 * lane or model over a window, plus the Places/Apify cost-guard caps that the
 * same response carries. Every money value goes through useCurrency().format so
 * the USD/BRL switch re-renders all of them. Colors only from the POC tokens.
 */
export function PainelCusto() {
  const { format } = useCurrency();
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
  const maxUsd = sorted.length ? Math.max(...sorted.map((r) => r.usd_cost)) : 1;

  const groupLabel = group === "lane" ? "lane" : "modelo";
  const barColor = group === "lane" ? "var(--navy)" : "var(--rio)";

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto px-[22px] pb-7 pt-5">
      {/* Segmented controls: group + window */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SegGroup label="Agrupar por">
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
        </SegGroup>
        <SegGroup label="Janela">
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
        </SegGroup>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Kpi label={`Gasto LLM · ${win.label}`}>
          <span data-testid="cost-total-usd">{format(usdTotal, LLM_DIGITS)}</span>
        </Kpi>
        <Kpi label="Tokens">
          <span data-testid="cost-total-tokens">{totalTokens(rows).toLocaleString("pt-BR")}</span>
        </Kpi>
        <Kpi label="Chamadas LLM">
          <span data-testid="cost-total-calls">{totalCalls(rows).toLocaleString("pt-BR")}</span>
        </Kpi>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        {/* Bars panel */}
        <section className="min-w-0 rounded-[12px] border border-[var(--line)] bg-[var(--surface)] p-4">
          <div className="mb-4 flex items-baseline justify-between gap-3">
            <h2 className="text-[13.5px] font-semibold text-[var(--ink)]">Gasto por {groupLabel}</h2>
            <span className="text-[12px] text-[var(--ink-3)]">janela {win.label}</span>
          </div>
          {sorted.length === 0 ? (
            <p data-testid="cost-bars" className="text-[12.5px] text-[var(--ink-3)]">
              Sem dados na janela
            </p>
          ) : (
            <div data-testid="cost-bars" className="flex flex-col gap-[15px]">
              {sorted.map((row) => {
                const width = Math.max(2, (row.usd_cost / maxUsd) * 100);
                const pct = usdTotal ? ((row.usd_cost / usdTotal) * 100).toFixed(1) : "0.0";
                return (
                  <div key={row.key} data-testid="cost-bar" className="flex flex-col gap-[7px]">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-[9px]">
                        <span
                          className="h-[9px] w-[9px] flex-shrink-0 rounded-[3px]"
                          style={{ background: barColor }}
                        />
                        <span className="overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[12.5px] font-semibold text-[var(--ink)]">
                          {row.key}
                        </span>
                      </div>
                      <span className="whitespace-nowrap font-mono text-[13px] font-semibold text-[var(--ink)]">
                        {format(row.usd_cost, LLM_DIGITS)}
                      </span>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="h-[6px] flex-1 overflow-hidden rounded-full bg-[var(--surface-2)]">
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${width}%`, background: barColor, transition: "width .35s ease" }}
                        />
                      </div>
                      <span className="w-[46px] text-right font-mono text-[11px] text-[var(--ink-3)]">
                        {pct}%
                      </span>
                    </div>
                    <div className="flex items-center gap-4 font-mono text-[10.5px] text-[var(--ink-3)]">
                      <span>{fmtNum(row.tokens)} tokens</span>
                      <span>{fmtNum(row.count)} chamadas</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <CostCaps data={data} />
      </div>
    </div>
  );
}

/**
 * The Places/Apify cost-guard caps (dashboard.py _places_cost / _apify_cost).
 * Read-only: the caps come from env; the guard pauses the motor when one trips.
 */
function CostCaps({ data }: { data: CostData | undefined }) {
  return (
    <section
      aria-label="Tetos do cost guard"
      className="flex min-w-0 flex-col gap-3 rounded-[12px] border border-[var(--line)] bg-[var(--surface)] p-4"
    >
      <div className="flex flex-wrap items-baseline gap-x-[10px]">
        <h2 className="text-[13.5px] font-semibold text-[var(--ink)]">Tetos do cost guard</h2>
        <span className="text-[12px] text-[var(--ink-3)]">o motor pausa ao estourar</span>
      </div>
      {data ? (
        <>
          <div className="text-[12px] text-[var(--ink-3)]">
            Google Places · chave{" "}
            <code className="font-mono text-[11.5px] text-[var(--ink-2)]">{data.places.key_id}</code>
          </div>
          <CostMeter
            testId="cap-places-today"
            label="Places hoje"
            hint="places_daily_usd_cap"
            used={data.places.usd_today}
            cap={data.places.daily_cap_usd}
          />
          <CostMeter
            testId="cap-places-total"
            label="Crédito Places"
            hint="places_total_usd_cap · nunca zera"
            used={data.places.usd_total}
            cap={data.places.total_cap_usd}
          />
          <div className="text-[12px] text-[var(--ink-3)]">Apify</div>
          <CostMeter
            testId="cap-apify-today"
            label="Apify hoje"
            hint="apify_daily_usd_cap"
            used={data.apify.usd_today}
            cap={data.apify.daily_cap_usd}
          />
          <CostMeter
            testId="cap-apify-total"
            label="Apify total"
            hint="apify_total_usd_cap"
            used={data.apify.usd_total}
            cap={data.apify.total_cap_usd}
          />
        </>
      ) : null}
      <p className="m-0 text-[12px] leading-[1.45] text-[var(--ink-3)]">
        “Hoje” é o dia UTC dos contadores (zera às 00:00 UTC, 21:00 em Brasília). O usd_daily_budget
        limita só LLM + busca; Places e Apify têm os tetos acima. Os tetos vêm do .env.
      </p>
    </section>
  );
}

function SegGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="inline-flex gap-0.5 rounded-[9px] bg-[var(--surface-2)] p-[3px]">
      {children}
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
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      data-active={active ? "true" : "false"}
      aria-pressed={active}
      onClick={onClick}
      className={`flex h-7 items-center rounded-[7px] px-[11px] text-[12.5px] font-semibold transition-colors ${
        active ? "bg-[var(--surface)] text-[var(--ink)] shadow-sm" : "text-[var(--ink-3)]"
      }`}
    >
      {children}
    </button>
  );
}

function Kpi({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-[12px] border border-[var(--line)] bg-[var(--surface)] p-[14px]">
      <div className="text-[12px] font-semibold text-[var(--ink-2)]">{label}</div>
      <div className="font-mono text-[22px] font-semibold tracking-[-0.8px] text-[var(--ink)]">{children}</div>
    </div>
  );
}
