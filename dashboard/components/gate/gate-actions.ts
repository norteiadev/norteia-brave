"use client";

import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import { toast } from "sonner";

import { ApiError } from "@/lib/api-client";
import {
  approveGate,
  gateKeys,
  rejectGate,
  type GateMutationResult,
} from "@/lib/gate-api";

/**
 * Gate action hooks (D-04) — TanStack `useMutation` over the EXISTING
 * atrativos_gate.py endpoints (approve / reject). No new mutations are
 * introduced; the dashboard only calls what Phase 3's gate router already
 * exposes, through the BFF.
 *
 * Shared invariants:
 *  - `onSettled` always `invalidateQueries(['gate'])` → the queue AND the ramp
 *    context refetch (they share the ['gate'] key prefix).
 *  - State-transition-explicit toasts (UI-SPEC): never a bare "Sucesso".
 *  - 401 surfaces the session-expired toast.
 *
 * The send-path compliance gate (human gate + ramp + opt-out) still runs
 * server-side inside outreach_task (T-04-19/T-04-20) — approve only authorizes
 * the dispatch; the UI cannot bypass the ramp.
 */

function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    return err.message;
  }
  return "Falha ao consultar a API.";
}

const GATE_KEY = gateKeys.all;

/**
 * Approve ("Aprovar contato"): flips sub_state → whatsapp_in_progress and
 * enqueues outreach. The queue refetches via the shared invalidate (the row
 * leaves the aguardando queue).
 */
export function useApproveGate(): UseMutationResult<
  GateMutationResult,
  unknown,
  string
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rioId: string) => approveGate(rioId),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => toast.success("Contato aprovado — saída enfileirada"),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: GATE_KEY });
    },
  });
}

/**
 * Reject ("Rejeitar atrativo?"): routes the atrativo to dlq/descarte. Behind a
 * shadcn AlertDialog confirm in the UI (destructive). Refetches the queue.
 */
export function useRejectGate(): UseMutationResult<
  GateMutationResult,
  unknown,
  string
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rioId: string) => rejectGate(rioId),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: () => toast.success("Atrativo rejeitado → DLQ"),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: GATE_KEY });
    },
  });
}
