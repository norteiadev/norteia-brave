import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./mocks/server";

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
