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
import { PainelRegistros } from "@/components/painel/PainelRegistros";
import { PainelShell } from "@/components/painel/PainelShell";
import { PainelTopbar } from "@/components/painel/PainelTopbar";
import { PainelVarreduras } from "@/components/painel/PainelVarreduras";
import { PainelVisaoGeral } from "@/components/painel/PainelVisaoGeral";
import { CurrencyProvider } from "@/components/painel/currency";
import { type PainelViewKey, type RegistrosTab } from "@/components/painel/nav";
import { getOperatorToken } from "@/lib/api-client";

/**
 * /painel — the Painel Brave single-shell (phase 17.1, slice 2).
 *
 * View switching is local state (SPA style), not nested Next routes. The shell
 * root is the scoped `.painel-light` wrapper (light/dark per operator) so the
 * global dark theme is untouched. The redesign (#83) opens on Visão geral;
 * Revisão / Mar / Falha open the Registros view on their tab. The old kanban is
 * no longer reachable. Money renders through CurrencyProvider (USD/BRL).
 */

/** Static title/subtitle per active view (design NAV copy, lines 502-509). */
const VIEW_HEADERS: Record<
  PainelViewKey,
  { title: string; subtitle: string }
> = {
  visao: {
    title: "Visão geral",
    subtitle: "Estado do pipeline Nascente → Rio → Mar agora",
  },
  registros: {
    title: "Registros",
    subtitle: "Revisão, Mar e Falha: lista por etapa, filtrável por UF",
  },
  duplicados: {
    title: "Duplicados",
    subtitle: "Pares que podem ser o mesmo lugar: compare campo a campo e decida",
  },
  mapeamento: {
    title: "Mapeamento da Origem",
    subtitle: "Camada data-mapper · campo bruto → estrutura canônica",
  },
  varreduras: {
    title: "Varreduras",
    subtitle: "Iniciar sweeps por UF, sessão do TripAdvisor e histórico de runs",
  },
  conversas: {
    title: "Conversas WhatsApp",
    subtitle:
      "Transcrições do gate de atrativos · telefones minimizados (LGPD)",
  },
  custo: {
    title: "Custo & LLM",
    subtitle: "Quanto o Brave gastou, com quem, e quanto falta para cada teto",
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
    subtitle: "Score de confiabilidade, lanes e limites (overlay config_settings)",
  },
};

function viewHeader(view: PainelViewKey): { title: string; subtitle: string } {
  return VIEW_HEADERS[view];
}

interface PainelBodyProps {
  view: PainelViewKey;
  tab: RegistrosTab;
  onNavigate: (view: PainelViewKey, tab?: RegistrosTab) => void;
}

/** Render the active view body. */
function PainelBody({ view, tab, onNavigate }: PainelBodyProps) {
  switch (view) {
    case "visao":
      return (
        <PainelVisaoGeral
          onOpenRegistros={(t) => onNavigate("registros", t)}
          onOpenView={(v) => onNavigate(v)}
        />
      );
    case "registros":
      return <PainelRegistros tab={tab} onTabChange={(t) => onNavigate("registros", t)} />;
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
  const [view, setView] = useState<PainelViewKey>("visao");
  const [tab, setTab] = useState<RegistrosTab>("revisao");
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

  const navigate = (next: PainelViewKey, nextTab?: RegistrosTab) => {
    setView(next);
    if (nextTab) setTab(nextTab);
  };

  return (
    <CurrencyProvider>
      <PainelShell
        active={view}
        registrosTab={tab}
        onSelect={navigate}
        topbar={<PainelTopbar title={title} subtitle={subtitle} />}
      >
        <PainelBody view={view} tab={tab} onNavigate={navigate} />
      </PainelShell>
    </CurrencyProvider>
  );
}
