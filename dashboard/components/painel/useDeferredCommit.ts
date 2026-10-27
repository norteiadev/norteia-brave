"use client";

import { useSyncExternalStore } from "react";
import { toast } from "sonner";

import { explainError } from "@/lib/painel-actions";

/**
 * Client-side undo (spec #82, Phase 0): a state-changing action waits
 * `delayMs` behind an "Desfazer" toast before it reaches the API. Undo cancels
 * the timer, so nothing is sent. There is no server-side undo.
 *
 * The timer lives at module level, outside React: a pending commit survives the
 * view switch that unmounts its caller. So does its `key`: while a keyed commit
 * is pending (or its request in flight), usePendingCommits() reports it, so a
 * remounted screen can keep the record hidden/locked, and a second commit with
 * the same key is ignored (no double PATCH inside the undo window).
 */
export interface DeferredCommit {
  /** Toast copy, e.g. "3 descartados." */
  message: string;
  /** The API call, run exactly once when the window expires. */
  run: () => Promise<unknown>;
  delayMs?: number;
  /** One pending commit per key (e.g. "atrativo:<id>"). */
  key?: string;
  /** Called after run() resolves (e.g. invalidate queries). The key stays
   *  pending until a returned promise settles, so a refetch can land first. */
  onDone?: () => unknown;
  /** Called when the operator clicks "Desfazer" (restore optimistic UI). */
  onUndo?: () => void;
  /** Called when run() rejects, before the explained error toast. */
  onError?: () => void;
}

let pending: ReadonlySet<string> = new Set();
const listeners = new Set<() => void>();

function setPending(key: string, on: boolean) {
  const next = new Set(pending);
  if (on) next.add(key);
  else next.delete(key);
  pending = next;
  listeners.forEach((l) => l());
}

function commit({ message, run, delayMs = 6000, key, onDone, onUndo, onError }: DeferredCommit): void {
  if (key && pending.has(key)) return;
  if (key) setPending(key, true);
  const release = () => key && setPending(key, false);

  const timer = setTimeout(() => {
    // Sonner pauses its own timer on hover: drop the toast so "Desfazer" can
    // never be clicked after the request went out.
    toast.dismiss(id);
    run()
      .then(
        () => onDone?.(),
        (err) => {
          onError?.();
          // 423 (motor ligado) and friends become an explained toast.
          toast.error(explainError(err));
        },
      )
      .finally(release);
  }, delayMs);

  const id = toast(message, {
    duration: delayMs,
    action: {
      label: "Desfazer",
      onClick: () => {
        clearTimeout(timer);
        release();
        onUndo?.();
        toast("Ação desfeita.");
      },
    },
  });
}

export function useDeferredCommit(): (c: DeferredCommit) => void {
  return commit;
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** Keys of the commits still inside their undo window or in flight. */
export function usePendingCommits(): ReadonlySet<string> {
  return useSyncExternalStore(subscribe, () => pending, () => pending);
}
