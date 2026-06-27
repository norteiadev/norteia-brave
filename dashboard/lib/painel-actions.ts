"use client";

/**
 * Painel drag-drop / retry → real-mutation mapping (17-05, UI-PAINEL-1).
 *
 * The closed allow-list that turns a Kanban drop (or a falha card's ↺ retry)
 * into the ONE real backend mutation it maps to — or into nothing. This module
 * is the security boundary for the riskiest slice (T-17-05-01): a drop may only
 * ever fire a real, mapped transition. Anything not in the table below returns
 * `null` and the hook reverts + toasts — NO endpoint is invented or called.
 *
 * Allowed real actions (the ONLY ones):
 *   target column          | destino card    | atrativo card
 *   mar (Sincronizado)     | promoteDestino  | promoteAtrativo (mar-ready, audited)
 *   descarte (Descarte)    | descarteDestino | descartarAtrativo
 *   dlq (Revisão/reprocess)| reprocessDestino| (none → null)
 *   nascente / in_progress | (none → null)   | (none → null)
 *   same column            | (none → null)   | (none → null)
 * Retry button (falha):    destino → reprocessDestino ; atrativo → null.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import { descartarAtrativo } from "@/lib/atrativos-api";
import {
  descarteDestino,
  promoteDestino,
  reprocessDestino,
} from "@/lib/destinos-api";
import { promoteAtrativo } from "@/lib/mar-ready-api";
import type {
  PainelCard,
  PainelColumnKey,
  PainelEntityType,
} from "@/lib/painel-data";

/** A resolved, dispatchable real mutation (never an invented one). */
export type DropAction = {
  kind: "promote" | "descarte" | "reprocess";
  entity: PainelEntityType;
  id: string;
};

/** Copy shown when a drop/retry maps to no real action (per 17-CONTEXT). */
const UNAVAILABLE = "Ação não disponível neste estágio";

/**
 * Map a drop of `card` onto `target` to its real action, or null when no real
 * transition exists for that (entity, target) pair. A drop onto the card's own
 * column is always a null no-op.
 */
export function mapDrop(
  card: PainelCard,
  target: PainelColumnKey,
): DropAction | null {
  if (card.column === target) return null;

  switch (target) {
    case "mar":
      // Sincronizado: promote (destino) or audited mar-ready promote (atrativo).
      return { kind: "promote", entity: card.type, id: card.id };
    case "descarte":
      return { kind: "descarte", entity: card.type, id: card.id };
    case "dlq":
      // Revisão/reprocess exists for destinos only; atrativos have no reprocess.
      return card.type === "destino"
        ? { kind: "reprocess", entity: "destino", id: card.id }
        : null;
    case "nascente":
    case "in_progress":
    default:
      // No real transition INTO nascente / em-processamento this slice.
      return null;
  }
}

/** Map a falha card's ↺ Reprocessar to reprocess (destino only) or null. */
export function mapRetry(card: PainelCard): DropAction | null {
  return card.type === "destino"
    ? { kind: "reprocess", entity: "destino", id: card.id }
    : null;
}

/**
 * Dispatch a resolved DropAction to its existing API client fn. Reprocess is
 * destino-only; a reprocess+atrativo action must never be constructed (mapDrop/
 * mapRetry never produce one) — runAction throws if it ever is.
 */
export function runAction(a: DropAction): Promise<unknown> {
  switch (a.kind) {
    case "promote":
      return a.entity === "destino"
        ? promoteDestino(a.id)
        : promoteAtrativo(a.id);
    case "descarte":
      return a.entity === "destino"
        ? descarteDestino(a.id)
        : descartarAtrativo(a.id);
    case "reprocess":
      if (a.entity === "atrativo") {
        throw new Error("reprocess is not supported for atrativos");
      }
      return reprocessDestino(a.id);
  }
}

/** Map an error to operator-facing PT-BR copy (ported from MarReadyActions). */
function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    if (err.status === 409) return "Ação não disponível neste estágio.";
    return err.message;
  }
  return "Falha ao atualizar o registro.";
}

export interface PainelMutationOptions {
  /** Called before the request fires so the container can move the card. */
  onOptimistic?: (card: PainelCard, target: PainelColumnKey) => void;
  /** Called on error so the container can roll the optimistic move back. */
  onRevert?: () => void;
}

/**
 * The single mutation over runAction. `drop`/`retry` resolve the action first:
 * a null mapping NEVER calls the mutation — it toasts "unavailable" and returns.
 * A mapped action applies optimistically, then on settle invalidates the
 * destinos + atrativos + engine status keys; on error it reverts + toasts.
 */
export function usePainelMutations(options: PainelMutationOptions = {}): {
  drop: (card: PainelCard, target: PainelColumnKey) => void;
  retry: (card: PainelCard) => void;
} {
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: (a: DropAction) => runAction(a),
    onError: (err) => {
      options.onRevert?.();
      toast.error(explainError(err));
    },
    onSuccess: () => {
      toast.success("Registro atualizado");
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["destinos"] });
      void qc.invalidateQueries({ queryKey: ["atrativos"] });
      void qc.invalidateQueries({ queryKey: ["engine", "status"] });
    },
  });

  return {
    drop: (card, target) => {
      const action = mapDrop(card, target);
      if (!action) {
        toast.error(UNAVAILABLE);
        return;
      }
      options.onOptimistic?.(card, target);
      mutation.mutate(action);
    },
    retry: (card) => {
      const action = mapRetry(card);
      if (!action) {
        toast.error(UNAVAILABLE);
        return;
      }
      mutation.mutate(action);
    },
  };
}
