---
phase: 04-dashboard-territorial-cms
plan: 02
subsystem: dashboard-frontend
tags: [nextjs-16, app-router, bun, tailwind-v4, shadcn, tanstack-query, bff, bearer-auth, vitest, msw]

# Dependency graph
requires:
  - phase: 04-dashboard-territorial-cms
    plan: 01
    provides: require_bearer FastAPI dependency + BRAVE_DASHBOARD_BEARER_TOKEN — the backend Bearer gate this BFF presents to
provides:
  - Greenfield dashboard/ Next.js 16 App Router app (Bun 1.3, Node 22, React 19, Tailwind v4)
  - shadcn new-york/neutral/CSS-vars preset (components.json + globals.css theme), dark-default, Geist Sans+Mono
  - app/providers.tsx — TanStack Query QueryClientProvider (D-04 server-state foundation)
  - app/api/[...path]/route.ts — BFF proxy Route Handler (browser Bearer → FastAPI service secret, D-02/D-03)
  - lib/auth.ts — constant-time browser-token validation at the BFF edge
  - lib/api-client.ts — typed BFF fetch wrappers + TanStack query keys
  - app/login/page.tsx — "Entrar" token gate + 401 redirect copy
  - Offline Vitest 4 + MSW 2 harness (setupServer, onUnhandledRequest=error) — D-07
affects: [all later dashboard slices (DLQ/monitor/gate/cost/funnels/conversations), every UI view + BFF call]

# Tech tracking
tech-stack:
  added:
    - "next 16.0.1 · react 19.2.0 (App Router)"
    - "@tanstack/react-query 5.90.2 (D-04 server-state)"
    - "tailwindcss 4.1.14 + @tailwindcss/postcss (Tailwind v4)"
    - "shadcn primitives deps: @radix-ui/react-slot, class-variance-authority, clsx, tailwind-merge, lucide-react, next-themes, tw-animate-css"
    - "vitest 4.0.4 + msw 2.11.5 + @testing-library/{react,jest-dom,user-event} + jsdom (offline test harness, D-07)"
  patterns:
    - "BFF proxy: catch-all Route Handler validates browser Bearer (401 before forward), injects server-held service secret, forwards only to fixed BRAVE_API_URL base"
    - "Server-only auth module (lib/auth.ts, node:crypto timingSafeEqual) never imported into a Client Component"
    - "api-client calls relative /api only (never FastAPI directly); operator token in browser storage"
    - "MSW setupServer (Node, not browser worker) per the Bun/Vitest gotcha; per-slice handler modules via server.use()"
    - "dark-default ops console via next-themes; Geist Sans (UI) + Geist Mono (data) per UI-SPEC"

key-files:
  created:
    - dashboard/package.json
    - dashboard/bun.lock
    - dashboard/next.config.ts
    - dashboard/postcss.config.mjs
    - dashboard/components.json
    - dashboard/.env.example
    - dashboard/.gitignore
    - dashboard/app/globals.css
    - dashboard/app/layout.tsx
    - dashboard/app/page.tsx
    - dashboard/app/providers.tsx
    - dashboard/lib/utils.ts
    - dashboard/lib/auth.ts
    - dashboard/lib/api-client.ts
    - dashboard/app/api/[...path]/route.ts
    - dashboard/app/login/page.tsx
    - dashboard/mocks/server.ts
    - dashboard/mocks/handlers/index.ts
    - dashboard/vitest.config.ts
    - dashboard/vitest.setup.ts
    - dashboard/app/api/__tests__/bff.test.ts
    - dashboard/app/login/__tests__/login.test.tsx
  modified:
    - dashboard/tsconfig.json
  removed:
    - dashboard/.gitkeep

key-decisions:
  - "Scaffolded via direct pinned config files + bun install (deterministic) rather than interactive `bun create next-app`; single sanctioned network event, bun.lock committed (RESEARCH §6 R6)"
  - "shadcn init NOT run interactively — wrote the exact new-york/neutral/CSS-vars components.json + globals.css theme it produces; per-block `shadcn add` deferred to the slices that need them (no third-party registries)"
  - "BFF mounts at /api/* and maps to FastAPI /<rest>; api-client `bff()` helper hides the double prefix (/api/api/v1/...)"
  - "passWithNoTests so the harness boots green before any slice adds tests (Task 1 acceptance criterion)"

requirements-completed: [DASH-06]

# Metrics
duration: 8min
completed: 2026-06-16
---

# Phase 4 Plan 02: Dashboard Scaffold + BFF Auth Foundation Summary

**Greenfield Next.js 16 App Router dashboard (Bun/Tailwind v4/shadcn new-york preset/TanStack Query) plus the D-02 BFF auth layer — a catch-all Route Handler that validates the browser Bearer, injects the server-held service secret to FastAPI, and never leaks it back — all proven offline with a Vitest 4 + MSW 2 harness (8 tests).**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-06-16T19:22Z
- **Completed:** 2026-06-16T19:30Z
- **Tasks:** 2 (both `type=auto`)
- **Files created:** 22 · modified: 1 · removed: 1 (`.gitkeep`)

## Accomplishments

### Task 1 — Scaffold + harness (commit `c68504a`)
- `dashboard/` Next.js 16 App Router app on Bun 1.3 / Node 22 / React 19 / Tailwind v4, all versions pinned and `bun.lock` committed (the single sanctioned network event, RESEARCH §6 R6).
- shadcn **new-york / neutral / CSS-variables** preset written to `components.json` + `app/globals.css` (full neutral CSS-var theme incl. UI-SPEC `--primary` blue accent and `--status-mar/dlq/descarte` semantic encodings). **Dark-default** via `next-themes`; **Geist Sans + Geist Mono** wired through `next/font` per UI-SPEC typography.
- `app/providers.tsx`: TanStack Query `QueryClientProvider` (client singleton held in `useState`) wrapping `next-themes` — wired into `app/layout.tsx`.
- Offline Vitest 4 + MSW 2 harness: `vitest.config.ts` (jsdom, `passWithNoTests`), `vitest.setup.ts` (`server.listen({ onUnhandledRequest: "error" })` — any real network fails the suite), `mocks/server.ts` using **`setupServer` (Node, not the browser worker)** per the Bun/Vitest gotcha, and an empty per-slice handlers barrel.
- `bunx vitest run` (green, 0 tests) + `bunx tsc --noEmit` (clean).

### Task 2 — BFF auth + login gate + offline tests (commit `2fdafbf`)
- `app/api/[...path]/route.ts`: catch-all BFF proxy (GET/POST/PATCH). Validates the browser `Authorization: Bearer` via `lib/auth.ts` and returns **401 before any forward**; on success `fetch`es the fixed `BRAVE_API_URL` base at the same path, injecting the server-held `BRAVE_DASHBOARD_BEARER_TOKEN` as `Authorization: Bearer`, relays FastAPI status + JSON, and **never echoes the secret** into the browser-facing response/headers.
- `lib/auth.ts`: server-only constant-time browser-token compare (`node:crypto.timingSafeEqual`), fail-closed on an unset `DASHBOARD_OPERATOR_TOKEN`.
- `lib/api-client.ts`: typed `apiFetch` + `ApiError` + TanStack query keys; operator-token browser storage; **only calls relative `/api`** (never FastAPI directly).
- `app/login/page.tsx`: **"Entrar"** token gate (blue `--primary` CTA), persists the operator token, and renders the UI-SPEC **"Sessão expirada ou token inválido"** copy on `?reason=expired`.
- Offline tests (MSW): `bff.test.ts` (4) — 401 before forward on missing/bad token, valid forward injects the **service** secret to the configured base, secret never leaks; `login.test.tsx` (4) — "Entrar" CTA, 401 copy on expired, no copy on normal load, submit persists token + redirects. **8/8 green.**

## Verification

- `cd dashboard && bunx vitest run` → **2 files, 8 tests passed** (fully offline, MSW).
- `cd dashboard && bunx tsc --noEmit` → **clean**.
- `cd dashboard && bunx next build` → **all 4 routes compiled** (`/`, `/login`, `/api/[...path]` dynamic, `/_not-found`).

## Deviations from Plan

### Auto-fixed / minor adjustments

**1. [Rule 3 — Blocking] Added `@testing-library/user-event` dev dependency**
- **Found during:** Task 2 (login test).
- **Issue:** The login submit test needs realistic user interaction; the plan's dep list didn't include `user-event`.
- **Fix:** Added `@testing-library/user-event@14.6.1` to `devDependencies` and re-ran `bun install` (within the sanctioned scaffold-install boundary). It is a first-party Testing Library helper, not a substitution for a failed package.
- **Files:** `dashboard/package.json`, `dashboard/bun.lock`.

**2. [Process] shadcn `init` not run interactively**
- The `npx shadcn@latest init` interactive flow can't run unattended; instead the exact artifacts it produces for the locked preset were written directly (`components.json` + the new-york/neutral CSS-var `globals.css`). The per-block `shadcn add` calls are deferred to the slices that consume each block (no blocks were needed for the foundation slice). No third-party registries. Net effect identical to the planned preset; the single-network-event boundary (`bun install`) is preserved.

**3. [Managed] `tsconfig.json` reconfigured by `next build`**
- `next build` set `jsx: react-jsx` and added `.next/dev/types/**/*.ts` to `include` (standard Next.js managed change). Committed as-is.

## Threat Model Compliance

- **T-04-05 (Spoofing / browser-token check):** `lib/auth.ts` constant-time `timingSafeEqual` vs `DASHBOARD_OPERATOR_TOKEN`, fail-closed; BFF returns 401 before any forward — asserted by `bff.test.ts` (missing + wrong token, `forwarded === false`).
- **T-04-06 (Info Disclosure / service-secret leak):** `BRAVE_DASHBOARD_BEARER_TOKEN` read only server-side in the Route Handler; the handler re-serializes the body and forwards only a safe content-type — no request `Authorization` echoed back. Asserted: response body + every response header free of the secret.
- **T-04-07 (Tampering / SSRF):** the catch-all path is appended to the fixed `BRAVE_API_URL` origin (segments `encodeURIComponent`-escaped, `redirect: "manual"`); the proxy can only reach the configured FastAPI base — asserted `seenUrl.startsWith(FASTAPI_BASE)`.
- **T-04-08 (Repudiation / unauthenticated mutation):** POST/PATCH go through the same browser-token gate; an invalid token 401s before forward (same code path as GET).
- **T-04-SC (package installs):** the one-time `bun install` is the single sanctioned network event; exact versions pinned, `bun.lock` committed; only first-party Testing Library + the locked stack added; no third-party shadcn registries.

## Known Stubs

- `app/page.tsx` is a minimal authenticated landing (links to `/login`); the full nav shell + six surfaces land in later slices (04-04+). This is an intentional foundation-slice stub, not a data stub — no empty data flows to UI.

## User Setup Required

Set in dashboard deploy/dev env (see `dashboard/.env.example`):
- `BRAVE_API_URL` — FastAPI base the BFF proxies to (server-side only).
- `BRAVE_DASHBOARD_BEARER_TOKEN` — server-held service token the BFF injects (must match the backend's value from plan 04-01).
- `DASHBOARD_OPERATOR_TOKEN` — the operator token the browser presents to the BFF.

## Next Phase Readiness

- The greenfield conventions every later slice builds on now exist: App Router layout, dark-default theme, TanStack Query provider, the BFF proxy seam, the typed `api-client`, and the offline MSW+Vitest harness with per-slice handler modules.
- Ready for the DLQ slice (04-04+): UI over the existing `GET /api/v1/dlq` list + the `GET /api/v1/dlq/{rio_id}` detail endpoint (04-03), all through the BFF.

## Self-Check: PASSED

All 22 created source files exist; both task commits (`c68504a`, `2fdafbf`) present in git history; full offline suite (8 tests) + tsc + next build all green.

---
*Phase: 04-dashboard-territorial-cms*
*Completed: 2026-06-16*
