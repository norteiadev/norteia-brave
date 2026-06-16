import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

// Offline-by-default test harness (D-07). MSW intercepts all network at the
// Node layer (setupServer) — no real FastAPI, no browser worker. Run via
// `bun run test` / `bunx vitest run`.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "."),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules", ".next"],
    // Harness must boot green even before any slice adds its tests.
    passWithNoTests: true,
  },
});
