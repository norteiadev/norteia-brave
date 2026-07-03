"use client";

/**
 * Painel drag-drop / retry → real-mutation mapping (17.1-06, UI-PAINEL-2).
 *
 * The closed allow-list that turns a Kanban drop (or a falha card's ↺ retry)
 * into the ONE real backend mutation it maps to — or into nothing. This module
 * is the CLIENT security boundary (T-17.1-06-01): a board drop may only ever
 * fire a real, mapped stage transition. Anything not in the table below returns
 * `null` and the hook reverts + toasts — NO endpoint is invented or called.
 *
 * Full-pipeline drag: every allowed edge routes through the ONE generic, audited
 * per-entity transition endpoint (engine-api `transition`). The allow-list below
 * is the EXACT twin of the server _ALLOWED_EDGES (brave/api/routers/cms.py) and
 * _ATRATIVO_ALLOWED_EDGES (atrativos.py) — the documented paired contract:
 *
 *   destino : (rio→mar) (rio→descarte) (rio→dlq) (dlq→rio) (dlq→mar) (dlq→descarte)
 *   atrativo: (rio→dlq) (dlq→rio) (rio→mar) (rio→descarte)  [+ whatsapp gate_approve*]
 *
 * Deliberately ABSENT (always null → revert + toast, never a call):
 *   - mar → *           (T-17.1-06-02: a live Mar record can never be depublished;
 *                        the server also 409s every ("mar", *) edge)
 *   - into-nascente     (no transition lands a record back in Nascente)
 *   - same-column drops (no-op)
 *   - falha → *         (quarantine records reprocess via ↺ retry, not a drag)
 *   - any other pair absent from the server allow-list
 *
 * (*) The atrativo (whatsapp→whatsapp) gate_approve edge is a same-column drop,
 *     so mapDrop returns null for it by design — the WhatsApp gate is driven by a
 *     dedicated gate UI, not a board drag. Keeping the same-column null guard is
 *     the documented exception; it is NOT a missing edge.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import { reprocessDestino } from "@/lib/destinos-api";
import { transition } from "@/lib/engine-api";
import type {
  PainelCard,
  PainelColumnKey,
  PainelEntityType,
} from "@/lib/painel-data";

/** A resolved, dispatchable real mutation (never an invented one). */
export type DropAction =
  | {
      kind: "transition";
      entity: PainelEntityType;
      id: string;
      to: PainelColumnKey;
      expected: PainelColumnKey;
    }
  | { kind: "reprocess"; entity: PainelEntityType; id: string };

/** Copy shown when a drop/retry maps to no real action (per 17-CONTEXT). */
const UNAVAILABLE = "Ação não disponível neste estágio";

/**
 * Per-entity client allow-list — the EXACT twin of the server allow-lists,
 * keyed by `${expected}>${to}`. A pair present here is the ONLY way mapDrop
 * emits a transition; everything else is null.
 */
const DESTINO_EDGES: ReadonlySet<string> = new Set([
  "rio>mar",
  "rio>descarte",
  "rio>dlq",
  "dlq>rio",
  "dlq>mar",
  "dlq>descarte",
]);
const ATRATIVO_EDGES: ReadonlySet<string> = new Set([
  "rio>dlq",
  "dlq>rio",
  "rio>mar",
  "rio>descarte",
]);

/**
 * Map a drop of `card` onto `target` to its real transition action, or null when
 * the (expected, to) pair is absent from the server allow-list for that entity.
 * A drop onto the card's own column is always a null no-op.
 */
export function mapDrop(
  card: PainelCard,
  target: PainelColumnKey,
): DropAction | null {
  if (card.column === target) return null;

  const edges = card.type === "destino" ? DESTINO_EDGES : ATRATIVO_EDGES;
  if (!edges.has(`${card.column}>${target}`)) return null;

  return {
    kind: "transition",
    entity: card.type,
    id: card.id,
    to: target,
    expected: card.column,
  };
}

/** Map a falha card's ↺ Reprocessar to reprocess (destino only) or null. */
export function mapRetry(card: PainelCard): DropAction | null {
  return card.type === "destino"
    ? { kind: "reprocess", entity: "destino", id: card.id }
    : null;
}

/**
 * Dispatch a resolved DropAction. Transitions go through the ONE generic, audited
 * per-entity transition endpoint; reprocess is destino-only (a reprocess+atrativo
 * action must never be constructed — mapRetry never produces one, runAction throws
 * if it ever is).
 */
export function runAction(a: DropAction): Promise<unknown> {
  switch (a.kind) {
    case "transition":
      return transition(a.entity, a.id, { to: a.to, expected: a.expected });
    case "reprocess":
      if (a.entity === "atrativo") {
        throw new Error("reprocess is not supported for atrativos");
      }
      return reprocessDestino(a.id);
  }
}

/** Map an error to operator-facing PT-BR copy. */
function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    if (err.status === 409) return "Ação não disponível neste estágio.";
    // Edit-lock backstop (phase H): the server 423s every card mutation while the
    // Motor is LIGADO. The optimistic move is reverted by onError → onRevert.
    if (err.status === 423)
      return "Motor ligado — pause o motor para editar os cards.";
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
