"use client";

import { useCallback, useState } from "react";

/**
 * useState backed by localStorage for small per-operator preferences (theme,
 * currency, collapsed menu). `allowed[0]` is the default; a stored value outside
 * `allowed` is ignored. Storage can throw (private mode, SSR), so every access
 * is guarded and the UI still works without it.
 */
export function useStoredState<T extends string>(
  key: string,
  allowed: readonly T[],
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = window.localStorage.getItem(key);
      if (allowed.includes(stored as T)) return stored as T;
    } catch {
      // no storage: fall back to the default
    }
    return allowed[0];
  });

  const set = useCallback(
    (next: T) => {
      setValue(next);
      try {
        window.localStorage.setItem(key, next);
      } catch {
        // preference just won't persist
      }
    },
    [key],
  );

  return [value, set];
}
