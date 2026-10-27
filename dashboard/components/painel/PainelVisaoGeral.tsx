"use client";

import type { RegistrosTab } from "./nav";

/** Visão geral (#82 Phase 0). Stub: a later slice fills in the API-backed parts. */
export function PainelVisaoGeral({
  onOpenRegistros,
}: {
  onOpenRegistros: (tab: RegistrosTab) => void;
}) {
  void onOpenRegistros;
  return (
    <section data-testid="painel-visao-geral" className="p-6">
      <h2 className="text-[15px] font-semibold text-[var(--ink)]">Visão geral</h2>
    </section>
  );
}
