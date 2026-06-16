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

/** Snapshot of every cached ['dlq','list',...] entry, keyed for exact restore. */
type DlqListSnapshot = Array<{
  queryKey: readonly unknown[];
  data: DlqListItem[] | undefined;
}>;

/**
 * Validate ("Validar e publicar"): sets validação-humana=100 → re-score → push
 * if Mar. OPTIMISTIC: the row is removed from the visible queue immediately
 * (it is leaving the DLQ), rolled back on error.
 *
 * WR-05: the optimistic removal + rollback span ALL cached ['dlq','list',...]
 * entries (every UF / entityType the operator has visited), not just the current
 * (uf, entityType) key. The validated row leaves the DLQ entirely, so it must
 * disappear from every mounted list observer immediately — and a failed validate
 * must restore every snapshotted list (including the case where a list was not
 * yet cached). The broad onSettled invalidate still reconciles with the server.
 */
export function useValidateDlqRecord(
  _uf?: string,
  _entityType?: string,
): UseMutationResult<MutationResult, unknown, string, { snapshot: DlqListSnapshot }> {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (rioId: string) => validateDlqRecord(rioId),
    onMutate: async (rioId: string) => {
      // Cancel every in-flight list fetch so they don't clobber the optimism.
      await qc.cancelQueries({ queryKey: dlqKeys.all });

      // Snapshot every cached list query for an exact per-key restore on error.
      const entries = qc.getQueriesData<DlqListItem[]>({
        queryKey: ["dlq", "list"],
      });
      const snapshot: DlqListSnapshot = entries.map(([queryKey, data]) => ({
        queryKey,
        data,
      }));

      // Optimistically remove the validated row from every cached list.
      for (const { queryKey, data } of snapshot) {
        if (data) {
          qc.setQueryData<DlqListItem[]>(
            queryKey,
            data.filter((r) => r.id !== rioId),
          );
        }
      }

      return { snapshot };
    },
    onError: (err, _rioId, ctx) => {
      // Restore every snapshotted list. `undefined` data is restored faithfully
      // (a list that was never cached stays uncached) so a failed validate rolls
      // back cleanly with no phantom empty entries.
      for (const { queryKey, data } of ctx?.snapshot ?? []) {
        qc.setQueryData(queryKey, data);
      }
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
