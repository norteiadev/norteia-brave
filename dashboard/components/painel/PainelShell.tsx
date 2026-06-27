"use client";

import type { ReactNode } from "react";

import { PainelSidebar } from "./PainelSidebar";
import type { PainelViewKey } from "./nav";

/**
 * PainelShell — the single-shell chrome for the Painel Brave CMS.
 *
 * Composes the 232px sidebar + a 58px topbar slot + a flexible content slot
 * (h-screen flex frame from the design contract). The shell is theme-agnostic:
 * the `/painel` page wraps it in `.painel-light` to apply the scoped light
 * surface. All colors resolve from CSS vars — no hardcoded hex here.
 */

interface PainelShellProps {
  active: PainelViewKey;
  onSelect: (key: PainelViewKey) => void;
  /** Topbar element (page title/subtitle + engine controls). */
  topbar: ReactNode;
  /** Active view body. */
  children: ReactNode;
}

export function PainelShell({
  active,
  onSelect,
  topbar,
  children,
}: PainelShellProps) {
  return (
    <div
      className="relative flex h-screen w-full overflow-hidden text-[13px]"
      data-testid="painel-shell"
    >
      <aside className="z-10 flex-shrink-0">
        <PainelSidebar active={active} onSelect={onSelect} />
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        {topbar}
        <div className="relative min-h-0 flex-1">{children}</div>
      </main>
    </div>
  );
}
