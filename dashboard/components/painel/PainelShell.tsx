"use client";

import { createContext, useContext, type ReactNode } from "react";

import { PainelSidebar, type PainelTheme } from "./PainelSidebar";
import type { PainelViewKey, RegistrosTab } from "./nav";
import { useStoredState } from "./useStoredState";

/**
 * PainelShell — the single-shell chrome for the Painel Brave CMS.
 *
 * Collapsible sidebar + a topbar slot + a flexible content slot. The shell root
 * IS the `.painel-light` wrapper and carries `data-painel-theme` (light/dark,
 * remembered in localStorage "brave.painelTheme"), so every token in
 * app/globals.css follows the operator's theme. No hardcoded hex here.
 */

interface PainelShellProps {
  active: PainelViewKey;
  /** Active Registros tab; highlights Revisão/Mar/Falha when active is "registros". */
  registrosTab?: RegistrosTab;
  onSelect: (key: PainelViewKey, tab?: RegistrosTab) => void;
  /** Topbar element (page title/subtitle + engine controls). */
  topbar: ReactNode;
  /** Active view body. */
  children: ReactNode;
}

const THEMES = ["light", "dark"] as const;

// Portaled content (dialogs) renders outside the shell's data-painel-theme
// subtree; it re-applies the theme from here.
const PainelThemeContext = createContext<PainelTheme>("light");
export const usePainelTheme = () => useContext(PainelThemeContext);

export function PainelShell({
  active,
  registrosTab,
  onSelect,
  topbar,
  children,
}: PainelShellProps) {
  const [theme, setTheme] = useStoredState<PainelTheme>("brave.painelTheme", THEMES);
  return (
    <div
      className="painel-light relative flex h-screen w-full overflow-hidden text-[13px]"
      data-painel-theme={theme}
      data-testid="painel-shell"
    >
      <aside className="z-10 flex-shrink-0">
        <PainelSidebar
          active={active}
          registrosTab={registrosTab}
          onSelect={onSelect}
          theme={theme}
          onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
        />
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <PainelThemeContext.Provider value={theme}>
          {topbar}
          <div className="relative min-h-0 flex-1">{children}</div>
        </PainelThemeContext.Provider>
      </main>
    </div>
  );
}
