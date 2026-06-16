---
phase: 04-dashboard-territorial-cms
plan: 09
subsystem: dashboard-funnels-conversations
tags: [dash-05, funnels, conversations, transcript, masked-phone, lgpd, r3, recharts, tanstack-query, msw, vitest, d-03, d-04, d-05, d-07]

# Dependency graph
requires:
  - phase: 04-dashboard-territorial-cms
    plan: 02
    provides: "BFF (apiFetch + ApiError + bff() double-prefix), TanStack Query providers, MSW+Vitest harness, shadcn new-york chart wrapper, operator-Bearer login gate (401 redirect)"
  - phase: 04-dashboard-territorial-cms
    plan: 08
    provides: "GET /api/v1/funnels (ingested/routing/published) + GET /api/v1/conversations[/{rio_id}] (masked transcript over the append-only conversation_message log, R2 Option B); 404 on unknown rio_id"
  - phase: 04-dashboard-territorial-cms
    plan: 07
    provides: "cost slice chart/summary pattern mirrored here — shared chart body, 4 view-states, per-slice MSW handler module"
provides:
  - "Funnels view (/funnels): FunnelChart Recharts stage bars (ingerido -> em progresso -> mar/dlq/descarte) by lane (entity_type) and UF, fetched via useQuery through the BFF"
  - "Conversations view (/conversations): ConversationList master + TranscriptPanel detail (inbound/outbound bubbles, extraction snapshot) — master-detail"
  - "funnels-api + conversations-api: typed BFF fetchers, query keys, toStageBars/isFunnelEmpty helpers"
  - "Per-slice MSW handler modules: mocks/handlers/funnels.ts + mocks/handlers/conversations.ts (success/empty/error/401/404)"
affects:
  - "DASH-05 is now shippable end-to-end (backend 04-08 + frontend 04-09)"
  - "the app nav shell (later) links to /funnels and /conversations"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "FunnelChart collapses the endpoint's three blocks (ingested[], routing[], published) into ordered stage totals via toStageBars() — Nascente sum is the top, Rio routing outcomes are the in_progress/mar/dlq/descarte split; absent stages render 0 so the funnel shape stays stable"
    - "Master-detail conversations: ConversationList drives TranscriptPanel via a selectedId in the page; TranscriptPanel's useQuery is `enabled: rioId != null` so no fetch fires until a row is picked"
    - "LGPD R3 at the type level: neither conversations-api type nor any fetcher has a raw-phone field — only `phone_masked` exists, so a raw E.164 is structurally unrepresentable in the browser layer"
    - "Per-slice MSW handler modules (funnels.ts, conversations.ts) applied per-suite via server.use(...) — same double-prefix (/api/api/v1/...) convention as cost/dlq/monitor"
    - "Four/five view-states per surface (Skeleton loading, empty copy, 401 session-expired, retry on other errors, 404 not-found for the transcript) mirroring the cost slice"

key-files:
  created:
    - dashboard/lib/funnels-api.ts
    - dashboard/lib/conversations-api.ts
    - dashboard/components/funnels/FunnelChart.tsx
    - dashboard/components/conversations/ConversationList.tsx
    - dashboard/components/conversations/TranscriptPanel.tsx
    - dashboard/app/funnels/page.tsx
    - dashboard/app/conversations/page.tsx
    - dashboard/mocks/handlers/funnels.ts
    - dashboard/mocks/handlers/conversations.ts
    - dashboard/components/funnels/__tests__/FunnelChart.test.tsx
    - dashboard/components/conversations/__tests__/TranscriptPanel.test.tsx
  modified: []

decisions:
  - "D-05: FunnelChart is a bar chart of per-stage counts (mirroring the monitor/cost Recharts bars) rather than a literal nested funnel widget — bars read the magnitude split clearly and reuse the existing shadcn chart wrapper with no new chart dep (honoring T-04-SC)"
  - "R3 LGPD enforced structurally: conversations-api exposes only `phone_masked`; the TranscriptPanel test asserts the un-minimized raw E.164 (`+5511987654342`) appears in NEITHER container.textContent NOR innerHTML"
  - "Added mocks/handlers/funnels.ts (not enumerated in the plan's files_modified) — the per-slice MSW convention requires a funnels handler for FunnelChart's offline view-state tests; treated as a Rule 3 blocking test-infra fix"

requirements-completed: [DASH-05]

# Metrics
duration: ~12min
completed: 2026-06-16
tasks: 1
files_changed: 11
commits: 1
---

# Phase 4 Plan 09: Funnels + Conversations Frontend (DASH-05) Summary

The DASH-05 frontend half: a `/funnels` view rendering destinos/atrativos pipeline-stage bars (ingerido → em progresso → mar/dlq/descarte) by lane and UF over `GET /api/v1/funnels`, and a `/conversations` master-detail view rendering the masked WhatsApp transcript per atrativo (inbound/outbound bubbles + extraction snapshot) over `GET /api/v1/conversations[/{rio_id}]` — both fetched through the BFF via TanStack Query, all offline-tested with MSW, and proven to leak no raw phone PII to the browser.

## What Was Built

**Task 1 — Funnels + Conversations views (commit `6a629b9`)**

- **`lib/funnels-api.ts`** — `FunnelData`/`FunnelIngestedRow`/`FunnelRoutingRow` types matching 04-08's response, `funnelKeys`, `fetchFunnels(filters)` (entity_type/uf/source → query params through the BFF), plus `toStageBars()` (collapses the three blocks into ordered stage totals) and `isFunnelEmpty()`.
- **`lib/conversations-api.ts`** — `ConversationListItem`/`ConversationDetail`/`ConversationMessage` types (masked-phone-only, no raw-E.164 field exists), `conversationKeys`, `fetchConversations()` + `fetchConversationDetail(rioId)` through the BFF.
- **`components/funnels/FunnelChart.tsx`** — Recharts stage bars via the shadcn `chart` wrapper (primary-blue series, value labels), lane (Todos/Destinos/Atrativos) + UF (BR + BA/RJ/SP/SC/CE/PE) filters driving the `useQuery` key; Skeleton / empty "Sem dados no período" / 401 / retry view-states.
- **`components/conversations/ConversationList.tsx`** — master list, one row per rio_id (masked phone + message count + last-message preview), selection drives the panel; empty "Sem conversas ainda", 401, retry states.
- **`components/conversations/TranscriptPanel.tsx`** — detail transcript: inbound (left, muted) / outbound (right, primary) bubbles, Geist Sans 14px body, extraction snapshot under its turn, header shows the masked phone labeled "telefone (minimizado)"; no-selection / Skeleton / 404 / 401 / retry states. `enabled: rioId != null` so no fetch until a row is picked.
- **`app/funnels/page.tsx`** + **`app/conversations/page.tsx`** — the two App Router views (conversations wires the master-detail selectedId).
- **`mocks/handlers/funnels.ts`** + **`mocks/handlers/conversations.ts`** — per-slice MSW modules with success/empty/error/401 (and detail 404) factories; all fixtures carry only masked phones.
- **Tests** — `FunnelChart.test.tsx` (5: stage series + filters present, loading, empty, error+retry, 401) and `TranscriptPanel.test.tsx` (11, incl. ConversationList: bubbles + masked label, **no raw E.164 in container.textContent or innerHTML**, no-selection, loading, 404, error, 401, extraction snapshot, list masked/empty/401).

## Verification

- `cd dashboard && bunx vitest run components/funnels components/conversations` → **2 files, 16 tests passed**.
- `cd dashboard && bunx vitest run` (full suite) → **13 files, 79 tests passed** (no regression to the prior 63).
- `cd dashboard && bunx tsc --noEmit` → **clean (exit 0)**.
- `cd dashboard && bunx next build` → all routes compiled, including **`/funnels` and `/conversations`** (both static).
- LGPD assertion: the raw E.164 `+5511987654342` appears in neither `container.textContent` nor `container.innerHTML` of the transcript; only the masked `+55 11 9••••-••42` renders.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking test infra] Added `mocks/handlers/funnels.ts`**
- **Found during:** Task 1 (FunnelChart offline view-state tests had no handler module).
- **Issue:** the plan's `files_modified` lists `mocks/handlers/conversations.ts` but not a funnels handler; FunnelChart's success/empty/error/401 tests require a per-slice MSW module for the `/api/v1/funnels` path (the repo's convention is one handler module per slice, applied via `server.use`).
- **Fix:** created `mocks/handlers/funnels.ts` mirroring `cost.ts` (success/empty/error/401 factories + barrel). No new dependency; test-only module never imported by app code.
- **Files:** `dashboard/mocks/handlers/funnels.ts`.
- **Commit:** `6a629b9`.

All else executed as written.

## Threat Model Compliance

- **T-04-28 (Information Disclosure / transcript PII):** mitigated. The TranscriptPanel + ConversationList render only the masked `phone_masked`; the conversations-api types have no raw-phone field, so a raw E.164 is structurally unrepresentable. Tests assert the un-minimized number is absent from both the rendered text and the markup.
- **T-04-29 (Spoofing / fetches):** mitigated. Every fetch goes through `apiFetch` → the BFF (relative `/api/...`, operator Bearer), never FastAPI directly; 401 surfaces the "Sessão expirada" copy (the login gate's redirect signal from plan 02).
- **T-04-SC (Tampering / deps):** accepted and held — **no new packages**; FunnelChart reuses the existing recharts + shadcn chart wrapper.

## Known Stubs

None — both views are fully wired to the real 04-08 endpoints (`/api/v1/funnels`, `/api/v1/conversations[/{rio_id}]`) through the BFF. The MSW handlers are test-only and never imported by app code. `/funnels` and `/conversations` are complete and reachable but not yet linked from a global nav shell (that shell lands in a later slice).

## Self-Check: PASSED

All 11 created files exist on disk; the single task commit `6a629b9` is in git history; the funnels+conversations suite (16), full frontend suite (79), tsc, and next build (incl. /funnels and /conversations) all green.

---
*Phase: 04-dashboard-territorial-cms*
*Completed: 2026-06-16*
