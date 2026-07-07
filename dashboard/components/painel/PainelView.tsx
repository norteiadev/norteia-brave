"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { PainelBoard } from "@/components/painel/PainelBoard";
import { PainelDrawer } from "@/components/painel/PainelDrawer";
import { PainelFilters } from "@/components/painel/PainelFilters";
import { PainelMetrics } from "@/components/painel/PainelMetrics";
import { ApiError } from "@/lib/api-client";
import {
  ENGINE_REFETCH_INTERVAL_MS,
  engineKeys,
  fetchEngineStatus,
} from "@/lib/engine-api";
import {
  WHATSAPP_INELIGIBLE_REASONS,
  ineligibleFrom,
  moveDlqToWhatsApp,
} from "@/lib/dlq-api";
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
 * PainelView — the wired Painel (Kanban) container (17-05, UI-PAINEL-1; phase H).
 *
 * Loads real board data (`usePainelBoard`) + truthful metrics (`usePainelMetrics`),
 * owns the type + UF-scope filter state, composes the metric cards / filters /
 * board, and turns drag-drops + the ↺ Reprocessar button into the REAL mapped
 * mutations via `usePainelMutations`.
 *
 * Phase H adds two operator flows on top:
 *   - Edit-lock: cards are draggable/selectable ONLY when the engine mode is
 *     PAUSADO/DESLIGADO (`status.editing_unlocked`). While LIGADO the board is
 *     read-only; the server 423s any card mutation and the optimistic move is
 *     reverted (painel-actions explainError 423 arm).
 *   - DLQ→WhatsApp: DLQ-column atrativos can be multi-selected and moved to the
 *     WhatsApp column via POST /api/v1/dlq/whatsapp-batch. The response's
 *     outreach/discovery split is surfaced as branch feedback; an atomic 422
 *     lists the ineligible records; phone stays masked (never on a card).
 *
 * Optimism: a mapped drop sets an override (cardId → column) so the card moves
 * immediately; onError clears overrides (rollback); onSettled invalidation
 * refetches and reconciles.
 */
export function PainelView() {
  const qc = useQueryClient();
  const [type, setType] = useState<TypeFilter>("all");
  const [ufs, setUfs] = useState<string[]>([]);
  const [overrides, setOverrides] = useState<Record<string, PainelColumnKey>>(
    {},
  );
  const [selected, setSelected] = useState<PainelCard | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
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

  const { cards, isPending, nascenteCount } = usePainelBoard(boardIntervalMs);
  const metrics = usePainelMetrics();

  const actions = usePainelMutations({
    onOptimistic: (card, target) =>
      setOverrides((o) => ({ ...o, [card.id]: target })),
    onRevert: () => setOverrides({}),
  });

  // DLQ→WhatsApp batch — atomic move of the selected DLQ atrativos.
  const batch = useMutation({
    mutationFn: (ids: string[]) => moveDlqToWhatsApp(ids),
    onSuccess: (res) => {
      // Branch feedback: outreach = conversa iniciada, discovery = LLM number-discovery.
      toast.success(
        `${res.moved} movido(s) para WhatsApp — ${res.outreach} conversa(s) iniciada(s), ${res.discovery} em descoberta de número (LLM).`,
      );
      setSelectedIds(new Set());
      void qc.invalidateQueries({ queryKey: ["atrativos"] });
      void qc.invalidateQueries({ queryKey: engineKeys.status });
    },
    onError: (err) => {
      const ineligible = ineligibleFrom(err);
      if (ineligible) {
        const reasons = ineligible
          .map((i) => WHATSAPP_INELIGIBLE_REASONS[i.reason] ?? i.reason)
          .join(", ");
        toast.error(
          `${ineligible.length} registro(s) inelegível(is) (${reasons}). Nada foi movido.`,
        );
        return;
      }
      if (err instanceof ApiError && err.status === 423) {
        toast.error("Motor ligado — pause o motor para mover para WhatsApp.");
        return;
      }
      toast.error(
        err instanceof ApiError ? err.message : "Falha ao mover para WhatsApp.",
      );
    },
  });

  const toggleSelect = (card: PainelCard) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(card.id)) next.delete(card.id);
      else next.add(card.id);
      return next;
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
        {selectedIds.size > 0 ? (
          <div
            data-testid="whatsapp-batch-bar"
            className="flex items-center justify-between gap-3 rounded-lg border border-[var(--painel-border-outer)] bg-[var(--painel-chip)] px-3.5 py-2.5"
          >
            <span className="text-[12.5px] font-semibold text-[var(--painel-text)]">
              {selectedIds.size} atrativo(s) selecionado(s) para WhatsApp
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                data-testid="whatsapp-batch-clear"
                onClick={() => setSelectedIds(new Set())}
                className="rounded-md px-2.5 py-1.5 text-[12px] font-semibold text-[var(--painel-muted)] hover:bg-[var(--card)]"
              >
                Limpar
              </button>
              <button
                type="button"
                data-testid="whatsapp-batch-btn"
                disabled={batch.isPending || !editingUnlocked}
                onClick={() => batch.mutate([...selectedIds])}
                className="rounded-md bg-[var(--painel-navy)] px-3.5 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50"
              >
                Mover para WhatsApp
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <PainelBoard
        cards={scoped}
        nascenteCount={nascenteCount}
        isPending={isPending}
        editingUnlocked={editingUnlocked}
        selectedIds={selectedIds}
        onToggleSelect={toggleSelect}
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
