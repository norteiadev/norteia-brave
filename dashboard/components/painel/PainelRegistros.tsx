"use client";

import type { RegistrosTab } from "./nav";

/** Registros (#88): Revisão / Mar / Falha tabs. Stub: a later slice fills it in. */
export function PainelRegistros({
  tab,
  onTabChange,
}: {
  tab: RegistrosTab;
  onTabChange: (tab: RegistrosTab) => void;
}) {
  void onTabChange;
  return (
    <section data-testid="painel-registros" data-tab={tab} className="p-6">
      <h2 className="text-[15px] font-semibold text-[var(--ink)]">Registros</h2>
    </section>
  );
}
