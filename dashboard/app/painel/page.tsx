"use client";

import { useState } from "react";

import { PainelConversas } from "@/components/painel/PainelConversas";
import { PainelCusto } from "@/components/painel/PainelCusto";
import { PainelDuplicados } from "@/components/painel/PainelDuplicados";
import { PainelMapeamento } from "@/components/painel/PainelMapeamento";
import { PainelShell } from "@/components/painel/PainelShell";
import { PainelTopbar } from "@/components/painel/PainelTopbar";
import { PainelVarreduras } from "@/components/painel/PainelVarreduras";
import { PainelView } from "@/components/painel/PainelView";
import { type PainelViewKey } from "@/components/painel/nav";

/**
 * /painel — the Painel Brave single-shell (phase 17.1, slice 2).
 *
 * A NEW route ALONGSIDE the existing 10 dark routes (non-breaking). The whole
 * subtree is wrapped in `.painel-light` to apply the scoped light surface
 * WITHOUT flipping the global dark theme. View switching is local state (SPA
 * style), not nested Next routes. All six views are now real: the Painel Kanban
 * (with the record-edit Drawer reachable from a card), Duplicados, Mapeamento,
 * Varreduras, Conversas and Custo. The Origem modal + depth-required Motor toggle
 * live in the topbar.
 */

/** Static title/subtitle per active view (design NAV copy, lines 502-509). */
const VIEW_HEADERS: Record<
  PainelViewKey,
  { title: string; subtitle: string }
> = {
  painel: {
    title: "Painel de Processamento",
    subtitle: "Fluxo Nascente → Rio → Mar em quadro Kanban",
  },
  duplicados: {
    title: "Revisão de Duplicados",
    subtitle: "Camada de validação · candidatos vs. registros no Mar",
  },
  mapeamento: {
    title: "Mapeamento da Origem",
    subtitle: "Camada data-mapper · campo bruto → estrutura canônica",
  },
  varreduras: {
    title: "Histórico de Varreduras",
    subtitle: "Runs do motor por UF, fonte e profundidade",
  },
  conversas: {
    title: "Conversas WhatsApp",
    subtitle:
      "Transcrições do gate de atrativos · telefones minimizados (LGPD)",
  },
  custo: {
    title: "Custo & LLM",
    subtitle: "Gasto agregado por lane e por modelo",
  },
};

function viewHeader(view: PainelViewKey): { title: string; subtitle: string } {
  return VIEW_HEADERS[view];
}

/** Render the active view body — all six are real (no "Em breve" placeholder). */
function PainelBody({ view }: { view: PainelViewKey }) {
  switch (view) {
    case "painel":
      return <PainelView />;
    case "duplicados":
      return <PainelDuplicados />;
    case "mapeamento":
      return <PainelMapeamento />;
    case "varreduras":
      return <PainelVarreduras />;
    case "conversas":
      return <PainelConversas />;
    case "custo":
      return <PainelCusto />;
  }
}

export default function PainelPage() {
  const [view, setView] = useState<PainelViewKey>("painel");
  const { title, subtitle } = viewHeader(view);

  return (
    <div className="painel-light h-screen">
      <PainelShell
        active={view}
        onSelect={setView}
        topbar={<PainelTopbar title={title} subtitle={subtitle} />}
      >
        <PainelBody view={view} />
      </PainelShell>
    </div>
  );
}
