import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./mocks/server";

// jsdom under Node >= 26 can leave window.localStorage/sessionStorage uninitialized
// (this repo targets Node 22). Components that persist the operator token
// (lib/api-client.ts setOperatorToken) then crash on `window.localStorage.setItem`.
// Polyfill a minimal in-memory Storage when absent so the suite runs on any Node.
function memoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => {
      store.delete(k);
    },
    setItem: (k: string, v: string) => {
      store.set(k, String(v));
    },
  } as Storage;
}
if (typeof window !== "undefined") {
  if (!window.localStorage) {
    Object.defineProperty(window, "localStorage", {
      value: memoryStorage(),
      configurable: true,
    });
  }
  if (!window.sessionStorage) {
    Object.defineProperty(window, "sessionStorage", {
      value: memoryStorage(),
      configurable: true,
    });
  }
}

// Offline-by-default (D-07): start the MSW Node server before any test, reset
// per-suite handlers (added via server.use) after each test, and tear it down at
// the end. `onUnhandledRequest: "error"` enforces that NO request escapes the
// mock layer — a real FastAPI/network call fails the suite loudly.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
