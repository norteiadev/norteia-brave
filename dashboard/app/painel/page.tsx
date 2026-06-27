"use client";

import { useState } from "react";

import { PainelConversas } from "@/components/painel/PainelConversas";
import { PainelCusto } from "@/components/painel/PainelCusto";
import { PainelMapeamento } from "@/components/painel/PainelMapeamento";
import { PainelShell } from "@/components/painel/PainelShell";
import { PainelTopbar } from "@/components/painel/PainelTopbar";
import { PainelView } from "@/components/painel/PainelView";
import { NAV_ITEMS, type PainelViewKey } from "@/components/painel/nav";

/**
 * /painel — the Painel Brave single-shell (phase 17, slice 1).
 *
 * A NEW route ALONGSIDE the existing 10 dark routes (non-breaking). The whole
 * subtree is wrapped in `.painel-light` to apply the scoped light surface
 * WITHOUT flipping the global dark theme. View switching is local state (SPA
 * style), not nested Next routes. Only `painel` is implemented; the other five
 * views render a centered "Em breve" placeholder.
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

/** Centered placeholder for the not-yet-built views. */
function EmBreve({ label }: { label: string }) {
  return (
    <div
      className="grid h-full place-items-center"
      data-testid="painel-em-breve"
    >
      <div
        className="rounded-[13px] border bg-[var(--card)] px-[28px] py-[24px] text-center"
        style={{ borderColor: "var(--painel-border-outer)" }}
      >
        <div className="text-[14px] font-semibold text-[var(--painel-text)]">
          Em breve
        </div>
        <div className="mt-[4px] text-[12px] text-[var(--painel-muted)]">
          {label}
        </div>
      </div>
    </div>
  );
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
        {view === "painel" ? (
          <PainelView />
        ) : view === "mapeamento" ? (
          <PainelMapeamento />
        ) : view === "conversas" ? (
          <PainelConversas />
        ) : view === "custo" ? (
          <PainelCusto />
        ) : (
          <EmBreve label={NAV_ITEMS[view].label} />
        )}
      </PainelShell>
    </div>
  );
}
