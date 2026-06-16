"use client";

import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import {
  type BatchResult,
  type DlqListItem,
  type MutationResult,
  descarteDlqRecord,
  dlqKeys,
  reprocessDlqRecord,
  validateDlqBatch,
  validateDlqRecord,
} from "@/lib/dlq-api";

/**
 * DLQ action hooks (D-04) — TanStack `useMutation` over the EXISTING dlq.py
 * mutation endpoints (validate / descarte / reprocess / validate-batch). No new
 * mutations are introduced; the dashboard only calls what the pipeline already
 * exposes, through the BFF.
 *
 * Shared invariants:
 *  - `onSettled` always `invalidateQueries(['dlq'])` → the list AND the detail
 *    refetch (they share the ['dlq'] key prefix). Editing → re-score → refetch
 *    falls out of this for free (validate IS the re-score path, D-07).
 *  - State-transition-explicit toasts (UI-SPEC): never a bare "Sucesso".
 *  - 401 surfaces the session-expired toast.
 */

function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    return err.message;
  }
  return "Falha ao consultar a API.";
}

const DLQ_KEY = dlqKeys.all;

/**
 * Validate ("Validar e publicar"): sets validação-humana=100 → re-score → push
 * if Mar. OPTIMISTIC: the row is removed from the visible queue immediately
 * (it is leaving the DLQ), rolled back on error.
 */
export function useValidateDlqRecord(
  uf?: string,
  entityType?: string,
): UseMutationResult<MutationResult, unknown, string, { previous?: DlqListItem[] }> {
  const qc = useQueryClient();
  const listKey = dlqKeys.list(uf, entityType);

  return useMutation({
    mutationFn: (rioId: string) => validateDlqRecord(rioId),
    onMutate: async (rioId: string) => {
      await qc.cancelQueries({ queryKey: listKey });
      const previous = qc.getQueryData<DlqListItem[]>(listKey);
      if (previous) {
        qc.setQueryData<DlqListItem[]>(
          listKey,
          previous.filter((r) => r.id !== rioId),
        );
      }
      return { previous };
    },
    onError: (err, _rioId, ctx) => {
      if (ctx?.previous) qc.setQueryData(listKey, ctx.previous);
      toast.error(explainError(err));
    },
    onSuccess: () => {
      toast.success("Registro validado → Mar");
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: DLQ_KEY });
    },
  });
}

/** Reject ("Rejeitar" → descarte). Behind an AlertDialog in the UI. */
export function useDescarteDlqRecord(): UseMutationResult<
  MutationResult,
  unknown,
  string
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rioId: string) => descarteDlqRecord(rioId),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => toast.success("Registro rejeitado → descarte"),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: DLQ_KEY });
    },
  });
}

/**
 * Reprocess / edit→re-score ("Reprocessar" / "Salvar e reprocessar"): re-runs
 * §7.6 routing. The queue refetches via the shared invalidate.
 */
export function useReprocessDlqRecord(): UseMutationResult<
  MutationResult,
  unknown,
  string
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rioId: string) => reprocessDlqRecord(rioId),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => toast.success("Registro reenviado para reprocessamento"),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: DLQ_KEY });
    },
  });
}

/**
 * Batch validate ("Validar lote por estado"): high-impact POST, gated by the
 * "Validar {n} registros de {UF} em lote?" confirm in the UI. Refetches the queue.
 */
export function useValidateDlqBatch(): UseMutationResult<
  BatchResult,
  unknown,
  { uf: string; entityType?: string; limit?: number }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ uf, entityType, limit }) =>
      validateDlqBatch(uf, entityType, limit),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: (res) =>
      toast.success(`${res.validated} registros de ${res.uf} validados → Mar`),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: DLQ_KEY });
    },
  });
}
