"use client";

import { useRef, useState } from "react";

import { PainelBoard } from "@/components/painel/PainelBoard";
import { PainelDrawer } from "@/components/painel/PainelDrawer";
import { PainelFilters } from "@/components/painel/PainelFilters";
import { PainelMetrics } from "@/components/painel/PainelMetrics";
import { usePainelMutations } from "@/lib/painel-actions";
import {
  filterCards,
  usePainelBoard,
  usePainelMetrics,
  type PainelCard,
  type PainelColumnKey,
  type TypeFilter,
} from "@/lib/painel-data";

/**
 * PainelView — the wired Painel (Kanban) container (17-05, UI-PAINEL-1).
 *
 * Replaces the 17-01 stub. Loads real board data (`usePainelBoard`) + truthful
 * metrics (`usePainelMetrics`), owns the type + UF-scope filter state, composes
 * the metric cards / filters / board, and turns drag-drops + the ↺ Reprocessar
 * button into the REAL mapped mutations via `usePainelMutations`. Drops that map
 * to no real action revert + toast (no invented endpoint) — the mapping lives in
 * lib/painel-actions.ts, the security boundary for this slice.
 *
 * Optimism: a mapped drop sets an override (cardId → column) so the card moves
 * immediately; onError clears overrides (rollback); onSettled invalidation
 * refetches and reconciles. Metrics reflect the WHOLE base (not UF-scoped this
 * slice); the UF scope filters the board only.
 */
export function PainelView() {
  const [type, setType] = useState<TypeFilter>("all");
  const [ufs, setUfs] = useState<string[]>([]);
  const [overrides, setOverrides] = useState<Record<string, PainelColumnKey>>(
    {},
  );
  const [selected, setSelected] = useState<PainelCard | null>(null);
  const dragged = useRef<PainelCard | null>(null);

  const { cards, isPending } = usePainelBoard();
  const metrics = usePainelMetrics();

  const actions = usePainelMutations({
    onOptimistic: (card, target) =>
      setOverrides((o) => ({ ...o, [card.id]: target })),
    onRevert: () => setOverrides({}),
  });

  // Apply optimistic column overrides, then the type + UF-scope filters.
  const effective = cards.map((c) =>
    overrides[c.id] ? { ...c, column: overrides[c.id] } : c,
  );
  const scoped = filterCards(effective, { type, ufs });

  return (
    <div data-testid="painel-view" className="flex h-full min-h-0 flex-col">
      <div className="flex flex-col gap-[14px] px-[22px] pb-1 pt-[18px]">
        <PainelMetrics destino={metrics.destino} atrativo={metrics.atrativo} />
        <PainelFilters
          type={type}
          onTypeChange={setType}
          ufs={ufs}
          onUfsChange={setUfs}
        />
      </div>

      <PainelBoard
        cards={scoped}
        nascenteCount={metrics.nascenteCount}
        isPending={isPending}
        onCardDragStart={(c) => {
          dragged.current = c;
        }}
        onDropToColumn={(target) => {
          if (dragged.current) {
            actions.drop(dragged.current, target);
            dragged.current = null;
          }
        }}
        onCardRetry={(c) => actions.retry(c)}
        onCardClick={setSelected}
      />

      <PainelDrawer card={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
