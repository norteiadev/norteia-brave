/**
 * Operator-facing copy for a failed Painel record action (deferred commits,
 * promote-bulk, repush). The kanban drag allow-list that lived here is gone
 * with the kanban (#88): Registros only fires the dlq→mar / dlq→descarte
 * transitions, which the server's _ATRATIVO_ALLOWED_EDGES still guards.
 */

import { ApiError } from "@/lib/api-client";

/** Map an error to operator-facing PT-BR copy. */
export function explainError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Sessão expirada ou token inválido.";
    // The server's 409 detail is operator copy (e.g. the reliability gate held
    // the record in the Rio, with its motivo): show it when present.
    if (err.status === 409)
      return typeof err.detail === "string" && err.detail
        ? err.detail
        : "Ação não disponível neste estágio.";
    // Edit-lock backstop: the server 423s every record mutation while the
    // Motor is LIGADO.
    if (err.status === 423)
      return "Motor ligado — pause o motor para promover, descartar ou editar registros.";
    return err.message;
  }
  return "Falha ao atualizar o registro.";
}
