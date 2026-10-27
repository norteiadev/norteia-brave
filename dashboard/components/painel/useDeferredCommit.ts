"use client";

import { toast } from "sonner";

import { explainError } from "@/lib/painel-actions";

/**
 * Client-side undo (spec #82, Phase 0): a state-changing action waits
 * `delayMs` behind an "Desfazer" toast before it reaches the API. Undo cancels
 * the timer, so nothing is sent. There is no server-side undo.
 *
 * The timer lives at module level, outside React: a pending commit survives the
 * view switch that unmounts its caller.
 */
export interface DeferredCommit {
  /** Toast copy, e.g. "3 descartados." */
  message: string;
  /** The API call, run exactly once when the window expires. */
  run: () => Promise<unknown>;
  delayMs?: number;
  /** Called after run() resolves (e.g. invalidate queries). */
  onDone?: () => void;
}

function commit({ message, run, delayMs = 6000, onDone }: DeferredCommit): void {
  const timer = setTimeout(() => {
    // Sonner pauses its own timer on hover: drop the toast so "Desfazer" can
    // never be clicked after the request went out.
    toast.dismiss(id);
    run().then(
      () => onDone?.(),
      // 423 (motor ligado) and friends become an explained toast.
      (err) => toast.error(explainError(err)),
    );
  }, delayMs);

  const id = toast(message, {
    duration: delayMs,
    action: {
      label: "Desfazer",
      onClick: () => {
        clearTimeout(timer);
        toast("Ação desfeita.");
      },
    },
  });
}

export function useDeferredCommit(): (c: DeferredCommit) => void {
  return commit;
}
