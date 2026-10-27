/**
 * Painel Brave — navigation config.
 *
 * The single-shell `/painel` route is an SPA-style view-switcher driven by local
 * state (NOT nested Next routes). The redesign (#83) replaces the kanban
 * ("painel") with Visão geral + Registros; Revisão / Mar / Falha are three nav
 * entries into the one Registros view, each opening its tab. The older views
 * stay reachable under Ferramentas / Observabilidade / Sistema. Conversas keeps
 * its view key but stays out of the nav (retired earlier).
 */

export type PainelViewKey =
  | "visao"
  | "registros"
  | "duplicados"
  | "mapeamento"
  | "varreduras"
  | "conversas"
  | "custo"
  | "revisao"
  | "monitor"
  | "logs"
  | "config";

/** Registros tabs, one per routing the steward works on. */
export type RegistrosTab = "revisao" | "mar" | "falha";

export type PainelNavGroup = "Operação" | "Ferramentas" | "Observabilidade" | "Sistema";

export interface PainelNavItem {
  /** Unique per item (testid `painel-nav-item-${id}`); Registros items carry the tab. */
  id: string;
  key: PainelViewKey;
  tab?: RegistrosTab;
  label: string;
  group: PainelNavGroup;
}

/** Nav items grouped as in the POC (order matters). */
export const NAV_GROUPS: { group: PainelNavGroup; items: PainelNavItem[] }[] = [
  {
    group: "Operação",
    items: [
      { id: "visao", key: "visao", label: "Visão geral", group: "Operação" },
      { id: "registros-revisao", key: "registros", tab: "revisao", label: "Revisão", group: "Operação" },
      { id: "registros-mar", key: "registros", tab: "mar", label: "Mar", group: "Operação" },
      { id: "registros-falha", key: "registros", tab: "falha", label: "Falha", group: "Operação" },
    ],
  },
  {
    group: "Ferramentas",
    items: [
      { id: "duplicados", key: "duplicados", label: "Duplicados", group: "Ferramentas" },
      { id: "varreduras", key: "varreduras", label: "Varreduras", group: "Ferramentas" },
      { id: "custo", key: "custo", label: "Custo & LLM", group: "Ferramentas" },
      { id: "mapeamento", key: "mapeamento", label: "Mapeamento", group: "Ferramentas" },
      { id: "revisao", key: "revisao", label: "DLQ & Gate", group: "Ferramentas" },
    ],
  },
  {
    group: "Observabilidade",
    items: [
      { id: "monitor", key: "monitor", label: "Monitor & Funis", group: "Observabilidade" },
      { id: "logs", key: "logs", label: "Logs", group: "Observabilidade" },
    ],
  },
  {
    group: "Sistema",
    items: [{ id: "config", key: "config", label: "Configuração", group: "Sistema" }],
  },
];
