"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { PainelConfig } from "@/components/painel/PainelConfig";
import { PainelConversas } from "@/components/painel/PainelConversas";
import { PainelCusto } from "@/components/painel/PainelCusto";
import { PainelDuplicados } from "@/components/painel/PainelDuplicados";
import { PainelLogsView } from "@/components/painel/PainelLogsView";
import { PainelMapeamento } from "@/components/painel/PainelMapeamento";
import { PainelMonitor } from "@/components/painel/PainelMonitor";
import { PainelRevisao } from "@/components/painel/PainelRevisao";
import { PainelShell } from "@/components/painel/PainelShell";
import { PainelTopbar } from "@/components/painel/PainelTopbar";
import { PainelVarreduras } from "@/components/painel/PainelVarreduras";
import { PainelView } from "@/components/painel/PainelView";
import { type PainelViewKey } from "@/components/painel/nav";
import { getOperatorToken } from "@/lib/api-client";

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
  revisao: {
    title: "Revisão · DLQ & Gate",
    subtitle: "Fila DLQ (confiabilidade) e gate WhatsApp de atrativos",
  },
  monitor: {
    title: "Monitor & Funis",
    subtitle: "Volume por camada, throughput e funil por camada",
  },
  logs: {
    title: "Logs do Motor",
    subtitle: "Tail incremental do ring buffer por fonte",
  },
  config: {
    title: "Configuração",
    subtitle: "Fontes, pesos de confiabilidade, limiar do Mar e modo do motor",
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
    case "revisao":
      return <PainelRevisao />;
    case "monitor":
      return <PainelMonitor />;
    case "logs":
      return <PainelLogsView />;
    case "config":
      return <PainelConfig />;
  }
}

export default function PainelPage() {
  const router = useRouter();
  const [view, setView] = useState<PainelViewKey>("painel");
  const [authed, setAuthed] = useState<boolean | null>(null);
  const { title, subtitle } = viewHeader(view);

  // Operator-token gate. Phase H removed the standalone `/` hub (which held the
  // only client gate) and points `/` → `/painel`; the gate moves here so an
  // unauthenticated visitor still lands on /login. The BFF remains the real
  // authority (it 401s data calls without a valid token) — this is just UX.
  useEffect(() => {
    if (!getOperatorToken()) {
      router.replace("/login");
      return;
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot mount gate
    setAuthed(true);
  }, [router]);

  if (authed === null) {
    return (
      <div className="painel-light grid h-screen place-items-center">
        <p className="text-sm text-[var(--painel-muted-2)]">Carregando…</p>
      </div>
    );
  }

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
