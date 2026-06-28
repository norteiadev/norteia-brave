/**
 * Painel Brave — navigation config (phase 17 shell; all six views wired in 17.1).
 *
 * The single-shell `/painel` route is an SPA-style view-switcher driven by local
 * state (NOT nested Next routes), mirroring the design contract's `setView`.
 * All six views (painel/duplicados/mapeamento/varreduras/conversas/custo) are
 * implemented and mounted as of phase 17.1 — no "Em breve" placeholders remain.
 *
 * Labels + groups are LOCKED by 17-CONTEXT.md (pt-BR copy from the mockup).
 */

export type PainelViewKey =
  | "painel"
  | "duplicados"
  | "mapeamento"
  | "varreduras"
  | "conversas"
  | "custo";

export type PainelNavGroup = "Processamento" | "Operação";

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
      { key: "painel", label: "Painel (Kanban)", group: "Processamento" },
      { key: "duplicados", label: "Duplicados", group: "Processamento" },
      { key: "mapeamento", label: "Mapeamento", group: "Processamento" },
      { key: "varreduras", label: "Varreduras", group: "Processamento" },
    ],
  },
  {
    group: "Operação",
    items: [
      { key: "conversas", label: "Conversas WhatsApp", group: "Operação" },
      { key: "custo", label: "Custo & LLM", group: "Operação" },
    ],
  },
];

/** Flat lookup of every nav item by view key. */
export const NAV_ITEMS: Record<PainelViewKey, PainelNavItem> = Object.fromEntries(
  NAV_GROUPS.flatMap((g) => g.items).map((it) => [it.key, it]),
) as Record<PainelViewKey, PainelNavItem>;
