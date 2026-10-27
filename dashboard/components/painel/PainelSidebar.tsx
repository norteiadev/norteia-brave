"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowRightLeft,
  Copy,
  DollarSign,
  Inbox,
  LayoutDashboard,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  SquareCheckBig,
  Terminal,
  TriangleAlert,
  Waves,
  type LucideIcon,
} from "lucide-react";

import { atrativoKeys, fetchAtrativoList, fetchFailureCards } from "@/lib/atrativos-api";
import { dedupKeys, fetchDedupPairs } from "@/lib/dedup-api";
import { engineKeys } from "@/lib/engine-api";

import { NAV_GROUPS, type PainelNavItem, type PainelViewKey, type RegistrosTab } from "./nav";
import { useStoredState } from "./useStoredState";

/**
 * PainelSidebar — the POC shell's left column (#83).
 *
 * Expanded: Norteia lockup + grouped nav with work-waiting counts. Collapsed
 * (remembered in localStorage "brave.sidebarCollapsed"): only the Norteia symbol
 * and icons; every link keeps its name via aria-label and a title tooltip.
 * Footer: theme toggle + collapse toggle. Colors only from CSS vars.
 */

export type PainelTheme = "light" | "dark";

interface PainelSidebarProps {
  active: PainelViewKey;
  registrosTab?: RegistrosTab;
  onSelect: (key: PainelViewKey, tab?: RegistrosTab) => void;
  theme: PainelTheme;
  onToggleTheme: () => void;
}

const NAV_ICONS: Record<string, LucideIcon> = {
  visao: LayoutDashboard,
  "registros-revisao": SquareCheckBig,
  "registros-mar": Waves,
  "registros-falha": TriangleAlert,
  duplicados: Copy,
  varreduras: Search,
  custo: DollarSign,
  mapeamento: ArrowRightLeft,
  revisao: Inbox,
  monitor: Activity,
  logs: Terminal,
  config: Settings,
};

const COUNT_REFETCH_MS = 60_000;

/**
 * Work-waiting counts from data the API already returns: list-envelope totals
 * (limit 1), the failure-card list length and the dedup pair total. Keys match
 * the existing queries so mutations that invalidate them refresh the badges.
 * A count stays hidden while unknown (loading or error).
 */
function useNavCounts(): Partial<Record<string, number>> {
  const opts = { refetchInterval: COUNT_REFETCH_MS };
  const revisao = useQuery({
    ...opts,
    queryKey: atrativoKeys.list({ count: "dlq" }),
    queryFn: () => fetchAtrativoList({ routing: "dlq", limit: 1 }),
    select: (r) => r.total,
  });
  const mar = useQuery({
    ...opts,
    queryKey: atrativoKeys.list({ count: "mar" }),
    queryFn: () => fetchAtrativoList({ routing: "mar", limit: 1 }),
    select: (r) => r.total,
  });
  const falha = useQuery({
    ...opts,
    queryKey: engineKeys.failures,
    queryFn: () => fetchFailureCards(),
    select: (cards) => cards.length,
  });
  const duplicados = useQuery({
    ...opts,
    queryKey: dedupKeys.pairs(),
    queryFn: () => fetchDedupPairs(),
    select: (r) => r.total,
  });
  return {
    "registros-revisao": revisao.data,
    "registros-mar": mar.data,
    "registros-falha": falha.data,
    duplicados: duplicados.data,
  };
}

function isActive(item: PainelNavItem, active: PainelViewKey, tab?: RegistrosTab) {
  return item.key === active && (item.tab === undefined || item.tab === tab);
}

const footBtn =
  "flex w-full items-center gap-[9px] rounded-[7px] px-[8px] py-[7px] text-[12.5px] font-medium text-[var(--ink-3)] hover:bg-[var(--surface-3)] hover:text-[var(--ink)]";

export function PainelSidebar({
  active,
  registrosTab,
  onSelect,
  theme,
  onToggleTheme,
}: PainelSidebarProps) {
  const [collapsedPref, setCollapsedPref] = useStoredState("brave.sidebarCollapsed", [
    "false",
    "true",
  ] as const);
  const collapsed = collapsedPref === "true";
  const counts = useNavCounts();
  const center = collapsed ? "justify-center px-0" : "";

  return (
    <div
      className={`flex h-full flex-shrink-0 flex-col gap-[18px] overflow-y-auto overflow-x-hidden border-r border-[var(--line)] bg-[var(--surface)] px-[12px] py-[18px] transition-[width] ${
        collapsed ? "w-[72px]" : "w-[224px]"
      }`}
    >
      <div className="flex flex-col gap-[6px] px-[6px]">
        {collapsed ? (
          <div
            role="img"
            aria-label="Norteia"
            data-testid="painel-brand-symbol"
            className="painel-brand-symbol"
          />
        ) : (
          <>
            <div
              role="img"
              aria-label="Norteia"
              data-testid="painel-brand-logo"
              className="painel-brand-logo"
            />
            <span className="text-[11.5px] tracking-[0.2px] text-[var(--ink-3)]">
              Brave · CMS territorial
            </span>
          </>
        )}
      </div>

      {NAV_GROUPS.map(({ group, items }) => (
        <nav key={group} aria-label={group} className="flex flex-col gap-[2px]">
          {!collapsed && (
            <div className="px-[8px] pb-[4px] text-[10.5px] uppercase tracking-[0.6px] text-[var(--ink-3)]">
              {group}
            </div>
          )}
          {items.map((item) => {
            const Icon = NAV_ICONS[item.id];
            const current = isActive(item, active, registrosTab);
            const count = counts[item.id];
            const countText = count === undefined ? undefined : count.toLocaleString("pt-BR");
            return (
              <button
                key={item.id}
                type="button"
                data-testid={`painel-nav-item-${item.id}`}
                aria-current={current ? "page" : undefined}
                aria-label={collapsed ? item.label : undefined}
                title={collapsed ? (countText ? `${item.label} · ${countText}` : item.label) : undefined}
                onClick={() => onSelect(item.key, item.tab)}
                className={`flex items-center gap-[9px] whitespace-nowrap rounded-[7px] px-[8px] py-[7px] text-left text-[13px] ${center} ${
                  current
                    ? "bg-[var(--surface-2)] font-semibold text-[var(--ink)]"
                    : "font-medium text-[var(--ink-2)] hover:bg-[var(--surface-3)]"
                }`}
              >
                <Icon className="size-4 flex-none" strokeWidth={1.8} aria-hidden />
                {!collapsed && (
                  <>
                    <span>{item.label}</span>
                    {countText !== undefined && (
                      <span
                        data-testid={`painel-nav-count-${item.id}`}
                        className="ml-auto font-mono text-[11px] font-semibold text-[var(--ink-3)]"
                      >
                        {countText}
                      </span>
                    )}
                  </>
                )}
              </button>
            );
          })}
        </nav>
      ))}

      <div className="mt-auto flex flex-col gap-[2px]">
        <button
          type="button"
          aria-pressed={theme === "dark"}
          aria-label="Tema escuro"
          title={collapsed ? "Tema escuro" : undefined}
          onClick={onToggleTheme}
          className={`${footBtn} ${center}`}
        >
          <Moon className="size-4 flex-none" strokeWidth={1.8} aria-hidden />
          {!collapsed && <span>Tema escuro</span>}
        </button>
        <button
          type="button"
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expandir menu" : "Recolher menu"}
          title={collapsed ? "Expandir menu" : undefined}
          onClick={() => setCollapsedPref(collapsed ? "false" : "true")}
          className={`${footBtn} ${center}`}
        >
          {collapsed ? (
            <PanelLeftOpen className="size-4 flex-none" strokeWidth={1.8} aria-hidden />
          ) : (
            <PanelLeftClose className="size-4 flex-none" strokeWidth={1.8} aria-hidden />
          )}
          {!collapsed && <span>Recolher menu</span>}
        </button>
      </div>
    </div>
  );
}
