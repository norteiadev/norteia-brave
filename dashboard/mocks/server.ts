import { setupServer } from "msw/node";

import { handlers } from "./handlers";

/**
 * MSW request-mocking server for the offline test suite (D-07, RESEARCH §5).
 *
 * Uses `setupServer` (the Node integration) — NOT the browser worker — per the
 * Bun/Vitest gotcha: under `bunx vitest` the tests run in Node, so the worker
 * (`setupWorker`) would never intercept. This server is started/stopped in
 * `vitest.setup.ts`.
 *
 * The default handler set is intentionally empty: each later slice owns its own
 * `mocks/handlers/<slice>.ts` module and applies it per-suite via
 * `server.use(...)`, so no two slices contend over one shared handlers file.
 */
export const server = setupServer(...handlers);
