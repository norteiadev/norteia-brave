"use client";

import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import {
  type BatchPromoteResult,
  type MarReadyItem,
  type PromoteResult,
  marReadyKeys,
  promoteAtrativo,
  promoteAtrativoBatch,
} from "@/lib/mar-ready-api";

/**
 * Mar-Ready action hooks (Phase 11, TA-06 / TA-07).
 *
 * TanStack `useMutation` over the /atrativos promote endpoints. Mirrors
 * dlq-actions.ts with the same optimistic-removal + snapshot-rollback pattern.
 *
 * T-11-04-02: onError restores snapshot — optimistic remove before 409 response
 * is fully rolled back.
 */

function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    if (err.status === 409) return "Atrativo não está pronto para promoção.";
    return err.message;
  }
  return "Falha ao consultar a API.";
}

const MAR_READY_KEY = marReadyKeys.all;

/** Snapshot of every cached ['mar-ready','list',...] entry, keyed for exact restore. */
type MarReadyListSnapshot = Array<{
  queryKey: readonly unknown[];
  data: MarReadyItem[] | undefined;
}>;

/**
 * Promote single attraction to Mar (PATCH /api/v1/atrativos/{id}/promote).
 *
 * OPTIMISTIC: the row is removed immediately, rolled back on error (409 or network).
 * T-11-04-02: onError restores snapshot from onMutate context.
 */
export function usePromoteMarReadyRecord(): UseMutationResult<
  PromoteResult,
  unknown,
  string,
  { snapshot: MarReadyListSnapshot }
> {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (rioId: string) => promoteAtrativo(rioId),
    onMutate: async (rioId: string) => {
      // Cancel every in-flight list fetch so they don't clobber the optimism.
      await qc.cancelQueries({ queryKey: marReadyKeys.all });

      // Snapshot every cached list query for an exact per-key restore on error.
      const entries = qc.getQueriesData<MarReadyItem[]>({
        queryKey: ["mar-ready", "list"],
      });
      const snapshot: MarReadyListSnapshot = entries.map(([queryKey, data]) => ({
        queryKey,
        data,
      }));

      // Optimistically remove the promoted row from every cached list.
      for (const { queryKey, data } of snapshot) {
        if (data) {
          qc.setQueryData<MarReadyItem[]>(
            queryKey,
            data.filter((r) => r.id !== rioId),
          );
        }
      }

      return { snapshot };
    },
    onError: (err, _rioId, ctx) => {
      // Restore every snapshotted list (rollback optimistic remove on 409).
      for (const { queryKey, data } of ctx?.snapshot ?? []) {
        qc.setQueryData(queryKey, data);
      }
      toast.error(explainError(err));
    },
    onSuccess: () => {
      toast.success("Atrativo promovido → Mar");
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: MAR_READY_KEY });
    },
  });
}

/**
 * Promote batch of attractions to Mar (POST /api/v1/atrativos/promote-batch).
 *
 * The caller is responsible for showing a confirm dialog before calling
 * `batch.mutate(...)` — matching the DLQ batch pattern.
 */
export function usePromoteMarReadyBatch(): UseMutationResult<
  BatchPromoteResult,
  unknown,
  { ufs: string[]; limit?: number }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ufs, limit }) => promoteAtrativoBatch(ufs, limit),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: (res) =>
      toast.success(`${res.promoted} atrativos de ${res.uf} promovidos → Mar`),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: MAR_READY_KEY });
    },
  });
}
