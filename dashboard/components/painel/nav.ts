/**
 * Painel Brave — navigation config (phase 17 shell; all six views wired in 17.1).
 *
 * The single-shell `/painel` route is an SPA-style view-switcher driven by local
 * state (NOT nested Next routes), mirroring the design contract's `setView`.
 *
 * Phase H (route consolidation) folded the standalone dark routes into the
 * painel: four new views join the original six — Revisão (DLQ + WhatsApp gate),
 * Monitor & Funis, Logs and Configuração — under two new groups (Observabilidade,
 * Sistema). The original six (painel/duplicados/mapeamento/varreduras/conversas/
 * custo) and their locked 17-CONTEXT.md labels/groups are unchanged.
 */

export type PainelViewKey =
  | "painel"
  | "duplicados"
  | "mapeamento"
  | "varreduras"
  | "conversas"
  | "custo"
  | "revisao"
  | "monitor"
  | "logs"
  | "config";

export type PainelNavGroup =
  | "Processamento"
  | "Operação"
  | "Observabilidade"
  | "Sistema";

export interface PainelNavItem {
  key: PainelViewKey;
  label: string;
  group: PainelNavGroup;
}

/** Nav items grouped exactly as in the design contract (order matters). */
export const NAV_GROUPS: { group: PainelNavGroup; items: PainelNavItem[] }[] = [
  {
    group: "Processamento",
    items: [
      { key: "painel", label: "Painel de Processamento", group: "Processamento" },
      { key: "duplicados", label: "Duplicados", group: "Processamento" },
      { key: "mapeamento", label: "Mapeamento", group: "Processamento" },
      { key: "varreduras", label: "Varreduras", group: "Processamento" },
    ],
  },
  {
    group: "Operação",
    items: [
      { key: "revisao", label: "Rio / Revisão", group: "Operação" },
      { key: "custo", label: "Custo & LLM", group: "Operação" },
    ],
  },
  {
    group: "Observabilidade",
    items: [
      { key: "monitor", label: "Monitor & Funis", group: "Observabilidade" },
      { key: "logs", label: "Logs", group: "Observabilidade" },
    ],
  },
  {
    group: "Sistema",
    items: [{ key: "config", label: "Configuração", group: "Sistema" }],
  },
];

/** Flat lookup of every nav item by view key. */
export const NAV_ITEMS: Record<PainelViewKey, PainelNavItem> = Object.fromEntries(
  NAV_GROUPS.flatMap((g) => g.items).map((it) => [it.key, it]),
) as Record<PainelViewKey, PainelNavItem>;
