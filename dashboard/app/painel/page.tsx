"use client";

import { useEffect, useRef, useState } from "react";
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
import { REGISTROS_TABS, type PainelViewKey, type RegistrosTab } from "@/components/painel/nav";
import { getOperatorToken } from "@/lib/api-client";
import { BR_UFS } from "@/lib/painel-data";

/**
 * /painel — the Painel Brave single-shell (phase 17.1, slice 2).
 *
 * View switching is SPA style, not nested Next routes, but the place lives in
 * the URL (#119): `/painel?view=registros&tab=revisao&uf=BA&id=<rio_id>`. It
 * is synced with window.history (which the App Router integrates) rather than
 * useSearchParams, so the static page needs no Suspense boundary. A
 * reload restores it and a link opens the same record. A new screen or tab
 * pushes a history entry; the UF filter and row moves (J/K) only replace it, so
 * Back returns to the previous screen/tab. The shell root is the scoped
 * `.painel-light` wrapper (light/dark per operator) so the global dark theme is
 * untouched. The redesign (#83) opens on Visão geral; Revisão / Mar / Falha open
 * the Registros view on their tab. Money renders through CurrencyProvider.
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

/** Where the operator is: everything the URL carries. */
interface PainelNav {
  view: PainelViewKey;
  tab: RegistrosTab;
  /** "" = every UF. */
  uf: string;
  id: string | null;
}

const HOME: PainelNav = { view: "visao", tab: "revisao", uf: "", id: null };

/** Unknown values fall back to the defaults (a stale or hand-typed link). */
function parseNav(search: string): PainelNav {
  const p = new URLSearchParams(search);
  const view = p.get("view") ?? "";
  const tab = p.get("tab") ?? "";
  const uf = p.get("uf") ?? "";
  return {
    view: Object.keys(VIEW_HEADERS).includes(view) ? (view as PainelViewKey) : "visao",
    tab: (REGISTROS_TABS as readonly string[]).includes(tab) ? (tab as RegistrosTab) : "revisao",
    uf: BR_UFS.includes(uf) ? uf : "",
    id: p.get("id") || null,
  };
}

/** What the Registros view changes in place (replace, not push). */
type RegistrosPatch = Partial<Pick<PainelNav, "uf" | "id">>;

/** Only Registros carries tab, UF and record. */
function navSearch(n: PainelNav): string {
  const p = new URLSearchParams();
  if (n.view !== "visao") p.set("view", n.view);
  if (n.view === "registros") {
    p.set("tab", n.tab);
    if (n.uf) p.set("uf", n.uf);
    if (n.id) p.set("id", n.id);
  }
  const q = p.toString();
  return q ? `?${q}` : "";
}

interface PainelBodyProps {
  nav: PainelNav;
  onNavigate: (view: PainelViewKey, tab?: RegistrosTab) => void;
  onRegistros: (patch: RegistrosPatch) => void;
  focusStart: number;
}

/** Render the active view body. */
function PainelBody({ nav, onNavigate, onRegistros, focusStart }: PainelBodyProps) {
  switch (nav.view) {
    case "visao":
      return (
        <PainelVisaoGeral
          onOpenRegistros={(t) => onNavigate("registros", t)}
          onOpenView={(v) => onNavigate(v)}
        />
      );
    case "registros":
      return (
        <PainelRegistros
          tab={nav.tab}
          uf={nav.uf}
          selected={nav.id}
          onTabChange={(t) => onNavigate("registros", t)}
          onUfChange={(uf) => onRegistros({ uf, id: null })}
          onSelect={(id) => onRegistros({ id })}
        />
      );
    case "duplicados":
      return <PainelDuplicados />;
    case "mapeamento":
      return <PainelMapeamento />;
    case "varreduras":
      return <PainelVarreduras focusStart={focusStart} />;
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
  const [nav, setNav] = useState<PainelNav>(HOME);
  const [authed, setAuthed] = useState<boolean | null>(null);
  // A nonce: each cold Ligar sets a new value so Varreduras focuses its start
  // panel (even when already on it); any other navigation resets it to 0 so a
  // plain visit to Varreduras does not steal the focus.
  const [focusStart, setFocusStart] = useState(0);
  const starts = useRef(0);
  const { title, subtitle } = viewHeader(nav.view);

  // Operator-token gate. Phase H removed the standalone `/` hub (which held the
  // only client gate) and points `/` → `/painel`; the gate moves here so an
  // unauthenticated visitor still lands on /login. The BFF remains the real
  // authority (it 401s data calls without a valid token) — this is just UX.
  useEffect(() => {
    if (!getOperatorToken()) {
      // Keep the link: the login brings the operator back to it.
      const next = `${window.location.pathname}${window.location.search}`;
      router.replace(`/login?next=${encodeURIComponent(next)}`);
      return;
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot mount gate
    setAuthed(true);
  }, [router]);

  // The URL is the source of truth: read it once on mount (the server render
  // has no window) and again on Back/Forward.
  useEffect(() => {
    const read = () => setNav(parseNav(window.location.search));
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);

  if (authed === null) {
    return (
      <div className="painel-light grid h-screen place-items-center">
        <p className="text-sm text-[var(--painel-muted-2)]">Carregando…</p>
      </div>
    );
  }

  const go = (next: PainelNav, push: boolean) => {
    setNav(next);
    const url = `${window.location.pathname}${navSearch(next)}`;
    if (url === `${window.location.pathname}${window.location.search}`) return;
    window.history[push ? "pushState" : "replaceState"](null, "", url);
  };
  // A new screen or tab: its own history entry, no record open. The UF filter
  // survives a tab change inside Registros only.
  const navigate = (view: PainelViewKey, tab?: RegistrosTab) => {
    setFocusStart(0);
    go({ view, tab: tab ?? nav.tab, uf: view === nav.view ? nav.uf : "", id: null }, true);
  };
  const updateRegistros = (patch: RegistrosPatch) =>
    go({ ...nav, ...patch }, false);
  const openStart = () => {
    navigate("varreduras");
    setFocusStart(++starts.current);
  };

  return (
    <CurrencyProvider>
      <PainelShell
        active={nav.view}
        registrosTab={nav.tab}
        onSelect={navigate}
        topbar={<PainelTopbar title={title} subtitle={subtitle} onStart={openStart} />}
      >
        <PainelBody
          nav={nav}
          onNavigate={navigate}
          onRegistros={updateRegistros}
          focusStart={focusStart}
        />
      </PainelShell>
    </CurrencyProvider>
  );
}
