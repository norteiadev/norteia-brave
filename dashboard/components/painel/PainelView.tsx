"use client";

import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { PainelBoard } from "@/components/painel/PainelBoard";
import { PainelDrawer } from "@/components/painel/PainelDrawer";
import { PainelFilters } from "@/components/painel/PainelFilters";
import { PainelMetrics } from "@/components/painel/PainelMetrics";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ApiError } from "@/lib/api-client";
import {
  promoteBulkAtrativos,
  type PromoteBulkDryRunResult,
  type PromoteBulkRunResult,
} from "@/lib/atrativos-api";
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
    // Only the cheap engine state/progress read polls fast (3s) while a sweep
    // runs. The board + metrics (12 queries, 4 with limit:500) stay on the shared
    // 10s cadence so the panel does not compete with the sweep for Postgres.
    refetchInterval: (query) =>
      query.state.data?.state === "running" ? 3000 : ENGINE_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });
  const editingUnlocked = engine?.editing_unlocked ?? true;

  const { cards, isPending, nascenteCount } = usePainelBoard(
    ENGINE_REFETCH_INTERVAL_MS,
    uf,
  );
  const metrics = usePainelMetrics(uf);

  const actions = usePainelMutations({
    onOptimistic: (card, target) =>
      setOverrides((o) => ({ ...o, [card.id]: target })),
    onRevert: () => setOverrides({}),
  });

  // Promover em lote: dry-run → AlertDialog confirm → real run, scoped to the UF.
  const qc = useQueryClient();
  const [bulkInFlight, setBulkInFlight] = useState(false);
  const [bulkDry, setBulkDry] = useState<PromoteBulkDryRunResult | null>(null);
  const bulkError = (err: unknown) =>
    toast.error(
      err instanceof ApiError && err.status === 423
        ? "Motor ligado — pause o motor para editar os cards."
        : err instanceof Error
          ? err.message
          : "Falha na promoção em lote.",
    );
  const onPromoverLote = async () => {
    if (bulkInFlight) return;
    setBulkInFlight(true);
    try {
      const dry = (await promoteBulkAtrativos({
        uf,
        dry_run: true,
      })) as PromoteBulkDryRunResult;
      if (dry.would_promote === 0) {
        toast.error(
          "Nenhum atrativo elegível para promoção em lote com os filtros atuais.",
        );
        return;
      }
      setBulkDry(dry);
    } catch (err) {
      bulkError(err);
    } finally {
      setBulkInFlight(false);
    }
  };
  const onConfirmarLote = async () => {
    setBulkDry(null);
    setBulkInFlight(true);
    try {
      const run = (await promoteBulkAtrativos({
        uf,
        dry_run: false,
      })) as PromoteBulkRunResult;
      toast.success(
        `${run.promoted} promovidos, ${run.held.length} retidos, ${run.failed.length} falharam. Restam ${run.remaining}.`,
      );
      void qc.invalidateQueries({ queryKey: ["destinos"] });
      void qc.invalidateQueries({ queryKey: ["atrativos"] });
      void qc.invalidateQueries({ queryKey: ["engine", "status"] });
    } catch (err) {
      bulkError(err);
    } finally {
      setBulkInFlight(false);
    }
  };

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
        <PainelFilters
          uf={uf}
          onUfChange={setUf}
          onPromoverLote={() => void onPromoverLote()}
          promoverLoteDisabled={bulkInFlight}
        />
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

      <AlertDialog
        open={bulkDry !== null}
        onOpenChange={(open) => {
          if (!open) setBulkDry(null);
        }}
      >
        {/* Portal renders outside the /painel subtree — re-scope the light tokens. */}
        <AlertDialogContent className="painel-light" data-testid="promover-lote-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>
              Promover {bulkDry?.would_promote} atrativos para o Mar?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {bulkDry?.candidates} elegíveis{uf ? ` em ${uf}` : ""}. Excluídos:{" "}
              {bulkDry?.excluded.below_score} por score,{" "}
              {bulkDry?.excluded.no_description} sem descrição,{" "}
              {bulkDry?.excluded.recency} sem review recente. Cada promoção fica
              registrada como validação humana e é publicada na norteia-api.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="promover-lote-cancel">
              Cancelar
            </AlertDialogCancel>
            <AlertDialogAction
              data-testid="promover-lote-confirm"
              onClick={() => void onConfirmarLote()}
            >
              Promover
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
