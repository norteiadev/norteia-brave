"use client";

import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { PainelBoard } from "@/components/painel/PainelBoard";
import { PainelDrawer } from "@/components/painel/PainelDrawer";
import { PainelFilters } from "@/components/painel/PainelFilters";
import { PainelMetrics } from "@/components/painel/PainelMetrics";
import {
  ENGINE_REFETCH_INTERVAL_MS,
  engineKeys,
  fetchEngineStatus,
} from "@/lib/engine-api";
import { usePainelMutations } from "@/lib/painel-actions";
import {
  filterCards,
  usePainelBoard,
  usePainelMetrics,
  type PainelCard,
  type PainelColumnKey,
} from "@/lib/painel-data";

/**
 * PainelView — the wired Painel (Kanban) container (17-05, UI-PAINEL-1; phase H).
 *
 * Loads real board data (`usePainelBoard`) + truthful metrics (`usePainelMetrics`),
 * owns the UF-scope filter + name-search state, composes the metric card /
 * filters / board, and turns drag-drops + the ↺ Reprocessar button into the REAL
 * mapped mutations via `usePainelMutations`.
 *
 * Phase H adds an operator flow on top:
 *   - Edit-lock: cards are draggable/selectable ONLY when the engine mode is
 *     PAUSADO/DESLIGADO (`status.editing_unlocked`). While LIGADO the board is
 *     read-only; the server 423s any card mutation and the optimistic move is
 *     reverted (painel-actions explainError 423 arm).
 *
 * Optimism: a mapped drop sets an override (cardId → column) so the card moves
 * immediately; onError clears overrides (rollback); onSettled invalidation
 * refetches and reconciles.
 */
export function PainelView() {
  const [uf, setUf] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [overrides, setOverrides] = useState<Record<string, PainelColumnKey>>(
    {},
  );
  const [selected, setSelected] = useState<PainelCard | null>(null);
  const dragged = useRef<PainelCard | null>(null);

  // Edit-lock: read the live engine status. Default UNLOCKED while the status is
  // unknown/loading so the board is interactive immediately (the server 423 is
  // the authoritative backstop); it only locks once a LIGADO status resolves.
  const { data: engine } = useQuery({
    queryKey: engineKeys.status,
    queryFn: fetchEngineStatus,
    refetchInterval: ENGINE_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });
  const editingUnlocked = engine?.editing_unlocked ?? true;

  // Bug 3: while a sweep runs the board polls fast (3s) so cards land in near
  // real time; idle it falls back to the shared 10s cadence.
  const boardIntervalMs =
    engine?.state === "running" ? 3000 : ENGINE_REFETCH_INTERVAL_MS;

  const { cards, isPending, nascenteCount } = usePainelBoard(boardIntervalMs, uf);
  // Metrics poll at the same fast cadence as the board so the % bar visibly
  // moves while the engine sweeps.
  const metrics = usePainelMetrics(uf, boardIntervalMs);

  const actions = usePainelMutations({
    onOptimistic: (card, target) =>
      setOverrides((o) => ({ ...o, [card.id]: target })),
    onRevert: () => setOverrides({}),
  });

  // Apply optimistic column overrides, then the UF-scope filter (type filtering
  // is gone — destinos are excluded in the data layer), then the name search.
  const effective = cards.map((c) =>
    overrides[c.id] ? { ...c, column: overrides[c.id] } : c,
  );
  const ufScoped = filterCards(effective, { type: "all", uf });
  const q = search.trim().toLowerCase();
  const scoped = q
    ? ufScoped.filter((c) => c.name?.toLowerCase().includes(q))
    : ufScoped;

  return (
    <div data-testid="painel-view" className="flex h-full min-h-0 flex-col">
      <div className="flex flex-col gap-[14px] px-[22px] pb-1 pt-[18px]">
        <PainelMetrics atrativo={metrics.atrativo} />
        <PainelFilters uf={uf} onUfChange={setUf} />
        <input
          data-testid="painel-search"
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Buscar atrativo por nome…"
          className="h-8 w-full rounded-lg border border-[var(--painel-border-outer)] bg-[var(--card)] px-3 text-[12.5px] text-[var(--painel-text)] placeholder:text-[var(--painel-muted-2)]"
        />
      </div>

      <PainelBoard
        cards={scoped}
        nascenteCount={nascenteCount}
        isPending={isPending}
        editingUnlocked={editingUnlocked}
        onCardDragStart={(c) => {
          dragged.current = c;
        }}
        onDropToColumn={(target) => {
          // Edit-lock: only fire a real transition while editing is unlocked.
          if (dragged.current && editingUnlocked) {
            actions.drop(dragged.current, target);
          }
          dragged.current = null;
        }}
        onCardRetry={(c) => actions.retry(c)}
        onCardClick={setSelected}
      />

      <PainelDrawer card={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
