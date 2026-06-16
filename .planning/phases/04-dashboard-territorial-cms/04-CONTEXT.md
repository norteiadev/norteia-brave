# Phase 4: Dashboard (Territorial CMS) - Context

**Gathered:** 2026-06-16
**Status:** Ready for planning

> Captured in `--auto` mode: every gray area was auto-resolved with the recommended (research-backed default) option. Each decision below is a **default that downstream research/planning may refine** — none is a hard user lock except where it restates a PROJECT.md Key Decision, a ROADMAP constraint, or a Phase 1/3 locked decision carried forward. This phase is the **final milestone phase**: a Next.js territorial CMS over the FastAPI surface built in Phases 1 & 3. The riskiest unknown is the **backend read-surface gap** (D-01) — several required views have no backing endpoint yet.

<domain>
## Phase Boundary

Deliver the **Next.js territorial CMS (operations dashboard)** that lets operators run the entire Brave pipeline **through the FastAPI REST surface, never the DB directly**, all behind **Bearer-header auth**:

1. **DLQ review queue** (DASH-01) — Nascente payload + Rio data + §7.6 per-criterion score + signals + WhatsApp log; approve/reject/edit/reprocess; **batch-by-state** mode; edit triggers a re-score.
2. **Brave monitor** (DASH-02, §15.7) — volume per layer, approval/rejection/DLQ rates, failure alerts, throughput, audit.
3. **WhatsApp gate UI** (DASH-03) — works the `aguardando_consulta_whatsapp` queue (approve/reject) with ramp/quality context.
4. **Cost & LLM view** (DASH-04) — spend per lane/model from `llm_generations`.
5. **Conversations + funnels view** (DASH-05) — WhatsApp transcripts; destinos & atrativos funnels by UF/source.
6. **Bearer-header auth** (DASH-06) — access-controlled; components tested offline with **Vitest + MSW**.

**In scope:** the entire `dashboard/` Next.js app (App Router, Bun, Node 22) — pages/components for the six surfaces above; a server-side BFF layer (Next.js Route Handlers) that proxies to FastAPI and injects the backend secret; **thin read-only FastAPI aggregation/detail endpoints added to `brave/api/` only where a required view has no backing endpoint** (D-01); a Bearer-auth dependency on the FastAPI surface the dashboard consumes (D-02); offline Vitest + MSW component/integration tests for every view; no real network in the default suite.

**Out of scope (other phases / milestones):** any change to the frozen Phase 1 core, score engine, routing, Mar service, or the Pact contract (the dashboard reads/triggers them through existing seams, never rewrites them); real Places/Apify/WhatsApp/OpenRouter/Anthropic calls (collector-only, opt-in flag); new collection lanes or pipeline logic; n8n transport changes; auth user-management / multi-tenant RBAC (single operator Bearer token this milestone — see Deferred); a public-facing UI (this is an internal ops CMS).
</domain>

<decisions>
## Implementation Decisions

### Backend read-surface gap — extend FastAPI, keep dashboard a pure REST consumer
- **D-01:** **This phase MAY add thin, read-only aggregation/detail endpoints to `brave/api/routers/` wherever a required dashboard view has no backing endpoint** — the dashboard "never touches the DB directly," so any data it needs must exist as a REST endpoint with all logic in Python (CLAUDE.md: logic lives in code, not the UI). Scout confirms gaps: `GET /api/v1/metrics` returns only per-layer **counts** (DASH-02 also needs approval/rejection/DLQ **rates**, throughput, failure alerts, audit roll-up); there is **no** cost-by-lane/model endpoint over `llm_generations` (DASH-04), **no** funnels-by-UF/source endpoint (DASH-05), **no** conversation-transcript read endpoint (DASH-05 — only the inbound relay in `atrativos_gate.py`), and the DLQ detail needed for DASH-01 (Nascente payload + Rio + **per-criterion** score + signals + WhatsApp log in one view) must be verified/extended on `dlq.py`. Add these as **read-only GET aggregation endpoints** (new `brave/api/routers/dashboard.py` or extend existing routers); reuse existing services/models; no new pipeline logic. Mutations (approve/reject/edit/reprocess) reuse the **existing** `dlq.py` + `atrativos_gate.py` PATCH endpoints — do not duplicate them.

### Auth — Bearer at the dashboard edge, secret stays server-side (BFF)
- **D-02:** **Adopt a BFF (Backend-for-Frontend) auth pattern.** The browser authenticates to the **Next.js server** with a Bearer header (single shared operator token from env this milestone — DASH-06); Next.js **Route Handlers proxy** to FastAPI, injecting the backend secret server-side so it **never reaches the browser**. Today's backend mutation endpoints use `X-Steward-Secret` (`dlq.py` notes "Phase 4 (DASH-06) supersedes this with dashboard Bearer auth"): add a **Bearer-token FastAPI dependency** (constant-time compare, mirroring `require_steward`'s `hmac.compare_digest` discipline) that the dashboard's BFF presents, and keep the steward header working for any direct/back-compat callers. Net: browser → `Authorization: Bearer <operator-token>` → Next.js BFF → FastAPI (`Authorization: Bearer <service-token>` or injected secret). MSW mocks the FastAPI surface at the network layer so auth is testable offline.

### Next.js app architecture & rendering
- **D-03:** **Next.js 16 App Router**, Server Components for static shells + **Route Handlers as the BFF/proxy** (D-02), **Client Components** for the interactive queues (DLQ, WhatsApp gate) and live monitor/charts. Default to server-side data fetch through the BFF; client components hydrate the mutation-heavy queues. App lives in `dashboard/` (currently empty — `.gitkeep` only). Bun 1.3 as package manager + test runner; Node 22 runtime target.

### Data fetching & client state
- **D-04:** **TanStack Query** for client-side fetching, caching, and mutations (optimistic re-score on DLQ edit, queue invalidation after approve/reject). It pairs cleanly with MSW for offline tests and handles the polling the live Brave monitor needs. No heavyweight global store (Redux/Zustand) this milestone — server cache + local component state is sufficient for an ops CMS.

### UI system, layout & charts
- **D-05:** **Tailwind CSS + shadcn/ui (Radix primitives)** for accessible tables, dialogs, drawers, and forms built fast; **Recharts** (or equivalent) for the monitor/funnels/cost charts. This is an internal ops CMS (utility-first, function over polish), not a consumer surface. A `--ui` design pass (`/gsd:ui-phase 4`) is available before/after planning if a richer design contract is wanted — the ROADMAP marks this phase **UI hint: yes**.

### DLQ + WhatsApp-gate review UX
- **D-06:** **Master-detail layout with a shared review-panel component.** A **state-filtered queue list** (batch-by-state selection, BA/RJ/SP/SC/CE/PE prioritized to mirror the Phase 2 steward order) beside a **detail panel** showing Nascente payload, Rio normalized data, the **§7.6 per-criterion breakdown** (origem/completude/corroboração/atualidade/validação-humana as bars/rows), signals, and the WhatsApp log. The same review-panel + queue scaffold is reused for the **DLQ queue** and the **WhatsApp gate queue** (they share shape: list → detail → approve/reject), differing only in actions and the backing endpoint. Inline edit → triggers re-score via the existing DLQ mutation; the queue refetches.

### Test strategy
- **D-07:** **Vitest + MSW only** (per constraint) — every view's components and BFF route handlers tested **offline**, with MSW mocking the FastAPI responses (success, empty, error, auth-fail). Bearer auth is exercised at the MSW/network layer. No test hits a real FastAPI instance or any external service. Mirror the collector's "100% offline by default, real = opt-in flag" discipline on the dashboard side.

### Claude's Discretion
The exact new endpoint paths/response shapes (D-01), the BFF route-handler topology and token env-var names (D-02), the precise component tree and file layout under `dashboard/`, the chart library final pick, the shadcn component set, the polling interval for the live monitor, and the MSW handler/fixture structure are left to research/planning. Decisions above set direction, not signatures. **Recommended:** run `/gsd:plan-phase 4` with research enabled so the planner verifies Next.js 16 App Router + Bun + Vitest 4 + MSW 2 current patterns and confirms the exact `llm_generations` / Rio / signal column shapes the new read endpoints must aggregate.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Backend surface the dashboard consumes (reuse; extend read-only per D-01)
- `brave/api/main.py` — FastAPI app + router registration (where new `dashboard` router is wired).
- `brave/api/deps.py` — `get_db`, `get_redis`, `get_config`, `get_steward_config`, `get_webhook_config`; add the **Bearer dependency** here (D-02).
- `brave/api/routers/dlq.py` — DLQ list (GET) + steward validate/descarte/reprocess (PATCH); `require_steward` (hmac constant-time) — the auth pattern to mirror for Bearer, and the mutation endpoints the DLQ UI calls. Contains the explicit "Phase 4 (DASH-06) supersedes this with dashboard Bearer auth" note.
- `brave/api/routers/atrativos_gate.py` — WhatsApp gate queue (GET `/api/v1/atrativos/gate`) + approve/reject (PATCH) + inbound relay; the WhatsApp-gate UI's backing endpoints (D-03/D-06).
- `brave/api/routers/metrics.py` — `GET /api/v1/metrics` (per-layer **counts only**; DASH-02 needs rates/throughput/alerts added — D-01).
- `brave/api/routers/audit.py` — `GET /api/v1/audit` (monitor audit feed, DASH-02).
- `brave/api/routers/webhook.py` — error-report webhook (reference for header-auth discipline).
- `brave/core/models.py` — `NascenteRecord`, `RioRecord` (routing/sub_state/normalized/per-criterion score fields), `MarRecord`, `llm_generations` (cost), `ConsentLog`, audit — the shapes the new read endpoints aggregate (cost-by-lane/model, funnels-by-UF/source, conversation transcript, per-criterion DLQ detail).

### Phase build (reuse, do not modify)
- `.planning/phases/01-brave-core-score-gate-boundary-contract/01-CONTEXT.md` — D-21 FastAPI surface + DI pattern; score-engine per-criterion config (the breakdown DASH-01 renders); cost-guard / `llm_generations` (DASH-04 source).
- `.planning/phases/03-atrativos-lane-whatsapp-compliance/03-CONTEXT.md` — gate endpoint shape (D-06 there), conversation persistence (AsyncPostgresSaver), ramp/quality-rating Redis flag (DASH-03 context), and the explicit note that "FastAPI gate/queue/conversation endpoints are consumed by the Phase 4 dashboard."
- `.planning/phases/02-destinos-lane/02-CONTEXT.md` — DLQ steward batch-by-state order (BA/RJ/SP/SC/CE/PE) the DLQ UI mirrors (D-06).

### Plan & framework
- `docs/PLANO-BRAVE.md` — full plan; **§15.7 monitor/audit** (DASH-02), observability/quality rating (DASH-03/04), dashboard-as-territorial-CMS framing. Authoritative for this milestone (§-numbers cite the norteia-api `Norteia_MVP_Documentacao_Tecnica_v1.md`; treat PLANO-BRAVE.md values as canonical here).
- `docs/brave-visao-geral.pdf` — Brave overview (visual companion).

### Project planning
- `.planning/ROADMAP.md` §"Phase 4" — goal + 5 success criteria; **UI hint: yes**.
- `.planning/REQUIREMENTS.md` — DASH-01..06.
- `.planning/PROJECT.md` — Key Decisions: dashboard is the territorial CMS (DLQ/monitor moved here off norteia-api Filament §15.7); Next.js + Bun + Node 22 + Bearer auth + Vitest + MSW constraint.
- `.planning/STATE.md` — Roadmap note: "Dashboard is its own final phase — each panel depends on its backing FastAPI surface (DLQ/monitor from P1, gate/conversations from P3) existing" (directly motivates D-01's gap-filling).

### Stack constraints (CLAUDE.md)
- Dashboard: **Next.js 16 · Bun 1.3 · Node 22 · Bearer-header auth · Vitest 4 + MSW 2**. App Router mirrors `norteia-frontend`. Logic in code; the dashboard is a thin REST consumer.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`dashboard/` exists but is empty** (`.gitkeep` only) — greenfield Next.js app; no prior dashboard code or conventions to conflict with.
- **FastAPI surface already partly serves the dashboard**: `dlq.py` (DLQ list + mutations), `atrativos_gate.py` (WhatsApp gate queue + approve/reject + inbound), `metrics.py` (per-layer counts), `audit.py` (audit feed), `health.py`. These are the consumer endpoints; D-01 adds the missing read aggregations.
- **`require_steward` (constant-time hmac) in `dlq.py`/`atrativos_gate.py`** is the exact pattern for the new Bearer dependency (D-02) — copy the discipline, swap header to `Authorization: Bearer`.
- **`brave/api/deps.py` DI** (`get_db`/`get_redis`/config getters) — new read endpoints plug straight into it.

### Established Patterns
- **Dashboard never touches the DB directly** (ROADMAP/PROJECT) — every datum is a FastAPI endpoint; this is the constraint that forces D-01 (extend the read surface) rather than querying Postgres from Next.js.
- **Header-secret auth, constant-time compare, never logged** (Phase 1/2/3) — the Bearer dependency continues this; the BFF keeps the secret server-side (D-02).
- **Collector "100% offline by default, real = opt-in flag"** — mirrored on the dashboard as **Vitest + MSW** with no real FastAPI in the default suite (D-07).
- **Per-criterion §7.6 score lives on the Rio record** (origem/completude/corroboração/atualidade/validação-humana) — the DLQ detail panel renders this breakdown (D-06); the read endpoint surfaces it (D-01).

### Integration Points
- **Browser → Next.js BFF (Route Handlers, Bearer) → FastAPI (injected secret) → SQLAlchemy services** (D-02/D-03). No external network from the dashboard.
- **DLQ UI → existing `dlq.py` PATCH** (validate/descarte/reprocess; edit→re-score) + **new GET detail** (D-01).
- **WhatsApp gate UI → existing `atrativos_gate.py` GET/PATCH** (queue/approve/reject) + ramp/quality context.
- **Monitor / Cost / Funnels / Conversations views → new read-only GET endpoints (D-01)** aggregating `RioRecord`/`MarRecord`/`NascenteRecord`/`llm_generations`/audit/conversation state.

</code_context>

<specifics>
## Specific Ideas

- **"Never the DB directly" is the headline invariant** — the dashboard is a pure REST consumer; any view without a backing endpoint forces a thin Python read endpoint (D-01), never a direct DB query from Next.js. This is a ROADMAP/PROJECT Key Decision (DLQ/monitor moved off norteia-api Filament onto this CMS).
- **Batch-by-state ordering carries forward**: the DLQ queue prioritizes BA/RJ/SP/SC/CE/PE (Phase 2 steward order) in the UI (D-06).
- **Single operator Bearer token this milestone** — full user management / RBAC is out of scope (Deferred); DASH-06 is satisfied by a shared operator token at the BFF edge.
- **UI hint = yes** — a `/gsd:ui-phase 4` design contract is available if a richer visual spec is wanted before planning.

</specifics>

<deferred>
## Deferred Ideas

- **Multi-user auth / RBAC / SSO** — this milestone ships a single shared operator Bearer token; per-user accounts, roles, and audit-by-user are a future phase.
- **Real-time push (WebSocket/SSE) for the live monitor** — polling via TanStack Query this milestone; push transport is a later optimization.
- **Funnel deep-analytics / cohort exploration beyond by-UF/source** — DASH-05 ships the basic funnel; richer analytics is v2.
- **OTA / freshness-decay / auto-tuning dashboards** — depend on v2 backend features (FRESH-01, TUNE-01, OTA-01) not built this milestone.
- **A dedicated design system / brand polish pass** — this is an internal ops CMS; consumer-grade design is out of scope unless a `/gsd:ui-phase` contract elevates it.

None of these are in Phase 4 scope — recorded so they aren't lost.

</deferred>

---

*Phase: 4-Dashboard (Territorial CMS)*
*Context gathered: 2026-06-16*
