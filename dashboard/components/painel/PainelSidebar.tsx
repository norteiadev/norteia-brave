"use client";

import { NAV_GROUPS, type PainelNavGroup, type PainelViewKey } from "./nav";

/**
 * PainelSidebar — 232px white left column for the Painel Brave shell.
 *
 * Logo header · two nav groups (Processamento / Operação) of view buttons ·
 * operator footer (navy "OP" avatar / Operador Brave / CMS Territorial).
 * Colors come exclusively from the scoped `.painel-light` CSS vars — no
 * hardcoded hex in classNames. pt-BR copy matches the design contract.
 */

interface PainelSidebarProps {
  active: PainelViewKey;
  onSelect: (key: PainelViewKey) => void;
}

/** Stable per-group test ids (also pins the two literal group labels here). */
const GROUP_TESTID: Record<PainelNavGroup, string> = {
  "Processamento": "painel-nav-group-processamento",
  "Operação": "painel-nav-group-operacao",
};

/** Inline glyphs per view (reuse the design contract's 16px outline SVGs). */
const NAV_ICONS: Record<PainelViewKey, React.ReactNode> = {
  painel: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <rect x="1.5" y="1.5" width="5.5" height="5.5" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
      <rect x="9" y="1.5" width="5.5" height="5.5" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
      <rect x="1.5" y="9" width="5.5" height="5.5" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
      <rect x="9" y="9" width="5.5" height="5.5" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  ),
  duplicados: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <circle cx="6" cy="8" r="4.2" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="10" cy="8" r="4.2" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  ),
  mapeamento: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <circle cx="3" cy="8" r="1.7" fill="currentColor" />
      <path d="M5 8 H11" stroke="currentColor" strokeWidth="1.4" />
      <path d="M9 5.5 L11.5 8 L9 10.5" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  varreduras: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <circle cx="8" cy="8" r="6.2" stroke="currentColor" strokeWidth="1.4" />
      <path d="M8 4.3 V8 L10.6 9.6" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" />
    </svg>
  ),
  conversas: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M2 4.5C2 3.4 2.9 2.5 4 2.5H12C13.1 2.5 14 3.4 14 4.5V9.5C14 10.6 13.1 11.5 12 11.5H6L3 14V11.5C2.4 11.5 2 11 2 10.5V4.5Z" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinejoin="round" />
    </svg>
  ),
  custo: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M2 13 V3 M2 13 H14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <rect x="4.5" y="7" width="2.2" height="4" fill="currentColor" />
      <rect x="8" y="4.5" width="2.2" height="6.5" fill="currentColor" />
      <rect x="11.5" y="8.5" width="2.2" height="2.5" fill="currentColor" />
    </svg>
  ),
};

export function PainelSidebar({ active, onSelect }: PainelSidebarProps) {
  return (
    <div
      className="flex h-full w-[232px] flex-shrink-0 flex-col border-r bg-[var(--card)]"
      style={{ borderColor: "var(--painel-border-outer)" }}
    >
      {/* Logo header */}
      <div
        className="border-b px-[18px] pb-[14px] pt-[18px]"
        style={{ borderColor: "var(--painel-border-inner)" }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/logo-norteia-brave.svg"
          alt="Norteia Brave"
          className="block h-[30px] w-auto"
        />
      </div>

      {/* Nav groups */}
      <nav className="flex flex-1 flex-col gap-[3px] p-[14px_12px]">
        {NAV_GROUPS.map(({ group, items }) => (
          <div
            key={group}
            data-testid={GROUP_TESTID[group]}
            className="flex flex-col gap-[3px]"
          >
            <div className="px-[10px] pb-[8px] pt-[4px] text-[10px] font-semibold uppercase tracking-[0.5px] text-[var(--painel-muted-2)]">
              {group}
            </div>
            {items.map((item) => {
              const isActive = item.key === active;
              return (
                <button
                  key={item.key}
                  type="button"
                  data-view={item.key}
                  data-testid={`painel-nav-item-${item.key}`}
                  data-active={isActive ? "true" : undefined}
                  aria-current={isActive ? "page" : undefined}
                  onClick={() => onSelect(item.key)}
                  className={`flex items-center gap-[10px] rounded-[8px] px-[10px] py-[8px] text-left text-[13px] font-medium transition-colors ${
                    isActive
                      ? "bg-[var(--painel-chip)] text-[var(--painel-navy)]"
                      : "text-[var(--painel-text)] hover:bg-[var(--painel-chip)]"
                  }`}
                >
                  <span className="flex-shrink-0">{NAV_ICONS[item.key]}</span>
                  {item.label}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      {/* Operator footer */}
      <div
        className="flex items-center gap-[9px] border-t px-[18px] py-[14px]"
        style={{ borderColor: "var(--painel-border-inner)" }}
      >
        <span className="grid h-[26px] w-[26px] place-items-center rounded-full bg-[var(--painel-navy)] text-[11px] font-bold text-white">
          OP
        </span>
        <div className="flex min-w-0 flex-col leading-[1.25]">
          <span className="text-[12px] font-semibold">Operador Brave</span>
          <span className="text-[10.5px] text-[var(--painel-muted-2)]">
            CMS Territorial
          </span>
        </div>
      </div>
    </div>
  );
}
