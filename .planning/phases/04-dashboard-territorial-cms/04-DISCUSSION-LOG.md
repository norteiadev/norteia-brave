# Phase 4: Dashboard (Territorial CMS) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-16
**Phase:** 4-Dashboard (Territorial CMS)
**Mode:** `--auto` (all gray areas auto-resolved with the recommended option; no interactive prompts)
**Areas discussed:** Backend read-surface gap, Auth bridge (Bearer), App architecture/rendering, Data fetching & state, UI system & charts, DLQ/gate review UX, Test strategy

---

## Backend read-surface gap

| Option | Description | Selected |
|--------|-------------|----------|
| Extend FastAPI with thin read-only endpoints (recommended) | Add read GETs where a view has no backing endpoint; dashboard stays a pure REST consumer | ✓ |
| UI-only — consume existing endpoints, skip missing views | Ship only DASH views already backed by an endpoint | |
| Let Next.js query Postgres directly for gaps | Violates "never touches the DB directly" invariant | |

**Auto-selected:** Extend FastAPI (read-only) — D-01.
**Notes:** Scout confirmed gaps: metrics returns counts only (no rates/throughput/alerts), no cost-by-lane/model, no funnels-by-UF/source, no conversation-transcript read endpoint, DLQ per-criterion detail to verify. "Dashboard never touches the DB directly" forces Python read endpoints.

---

## Auth bridge (Bearer)

| Option | Description | Selected |
|--------|-------------|----------|
| BFF — Bearer at Next.js edge, secret server-side (recommended) | Browser→Next.js (Bearer)→FastAPI (injected secret); add Bearer dep mirroring `require_steward` | ✓ |
| Bearer straight to FastAPI from browser | Backend secret exposed to the browser | |
| Keep X-Steward-Secret, no Bearer | Fails DASH-06 | |

**Auto-selected:** BFF pattern — D-02.
**Notes:** Backend uses `X-Steward-Secret`; `dlq.py` already flags "Phase 4 (DASH-06) supersedes this with dashboard Bearer auth." Constant-time compare discipline carried forward.

---

## App architecture / rendering

| Option | Description | Selected |
|--------|-------------|----------|
| App Router + Route Handlers as BFF (recommended) | Server shells, Route-Handler proxy, client components for queues/charts | ✓ |
| Client-only SPA hitting FastAPI directly | No server layer to hold the secret | |
| Pages Router | Legacy; mismatched with Next.js 16 | |

**Auto-selected:** App Router + BFF — D-03.

---

## Data fetching & client state

| Option | Description | Selected |
|--------|-------------|----------|
| TanStack Query (recommended) | Caching, mutations, optimistic re-score, polling; pairs with MSW | ✓ |
| Native fetch / SWR | Lighter, but more manual mutation/polling plumbing | |
| Redux/Zustand global store | Overkill for an ops CMS | |

**Auto-selected:** TanStack Query — D-04.

---

## UI system & charts

| Option | Description | Selected |
|--------|-------------|----------|
| Tailwind + shadcn/ui + Recharts (recommended) | Accessible tables/dialogs fast; charts for monitor/funnels/cost | ✓ |
| Custom CSS / component library from scratch | Slower; no benefit for internal CMS | |
| Full design-system pass first | Available via `/gsd:ui-phase 4` if richer spec wanted | |

**Auto-selected:** Tailwind + shadcn/ui + Recharts — D-05. (UI hint = yes; `/gsd:ui-phase 4` optional.)

---

## DLQ / WhatsApp-gate review UX

| Option | Description | Selected |
|--------|-------------|----------|
| Master-detail + shared review panel (recommended) | State-filtered queue + detail panel (payload/Rio/§7.6 per-criterion/signals/WhatsApp log); reused for DLQ and gate | ✓ |
| Separate bespoke screens per queue | Duplicated effort; both queues share shape | |
| Modal-only review | Cramped for the per-criterion + log detail | |

**Auto-selected:** Master-detail, shared panel — D-06. Batch-by-state order BA/RJ/SP/SC/CE/PE (Phase 2 carry-forward).

---

## Test strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Vitest + MSW, 100% offline (recommended / constraint) | Mock FastAPI at network layer; success/empty/error/auth-fail | ✓ |
| Real FastAPI integration in default suite | Violates offline-by-default constraint | |

**Auto-selected:** Vitest + MSW offline — D-07.

---

## Claude's Discretion

- Exact new endpoint paths/response shapes (D-01), BFF route-handler topology + token env-var names (D-02), component tree/file layout under `dashboard/`, final chart-lib pick, shadcn component set, monitor polling interval, MSW handler/fixture structure.

## Deferred Ideas

- Multi-user auth / RBAC / SSO (single operator Bearer token this milestone).
- Real-time push (WebSocket/SSE) for the live monitor (polling for now).
- Funnel deep-analytics beyond by-UF/source.
- OTA / freshness-decay / auto-tuning dashboards (depend on v2 backend).
- Dedicated design-system / brand-polish pass (internal ops CMS).
