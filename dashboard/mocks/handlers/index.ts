import type { RequestHandler } from "msw";

/**
 * Default (empty) MSW handler barrel.
 *
 * Each vertical slice owns its own `mocks/handlers/<slice>.ts` module exporting
 * an array of handlers, and applies it per test suite via `server.use(...)`.
 * Keeping this default empty means no two slices share a handlers file and the
 * harness boots clean with zero global mocks (every request must be explicitly
 * mocked by the suite that needs it).
 */
export const handlers: RequestHandler[] = [];
