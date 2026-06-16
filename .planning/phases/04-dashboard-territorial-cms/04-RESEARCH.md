# Phase 4: Dashboard (Territorial CMS) — Research

**Researched:** 2026-06-16
**For:** `/gsd:plan-phase 4` (MVP / vertical-slice mode)
**Method:** Inline research by the orchestrator after two `gsd-phase-researcher` subagent spawns died at the transport layer (socket closed, 0 tokens returned, no file written — an infra failure, not a prompt failure). Grounded in direct reads of `brave/api/`, `brave/core/models.py`, `brave/config/settings.py`, `brave/tasks/pipeline.py`, plus current-version web lookup. The first failed spawn also tripped a security warning (it tried to install an undeclared `slopcheck` package); the repo was verified clean afterward (no install persisted, working tree unchanged).

---

## TL;DR for the planner

1. **The dashboard cannot be built UI-first — it is gated by a backend read-surface gap (CONTEXT D-01).** Of the 6 DASH requirements, only **DASH-01 mutations** and **DASH-03 queue/approve/reject** are fully backed by existing endpoints. The monitor (DASH-02 rates/throughput/alerts), cost (DASH-04), funnels (DASH-05), conversation transcript (DASH-05), and the DLQ **detail** panel (DASH-01) each need a **new thin read-only FastAPI endpoint** before their UI slice can be built or MSW-tested against a real contract.
2. **Auth is a BFF bridge (D-02).** Backend mutations use `X-Steward-Secret` (constant-time hmac in `require_steward`). Add a **Bearer FastAPI dependency** (mirror `require_steward`) and a **Next.js Route-Handler BFF** that validates a browser Bearer token then forwards to FastAPI with the server-held secret. The secret never reaches the browser.
3. **Conversation transcript (DASH-05) is the hardest data-source problem.** Transcripts live in **LangGraph `checkpoints` / `checkpoint_blobs`** tables (AsyncPostgresSaver), not a friendly message table. The read endpoint must either decode LangGraph checkpoint state or (recommended, simpler, decoupled) the planner adds a lightweight append-only `conversation_message` log written by the outreach/resume tasks. **Flag this as the one open design decision needing a planner call.**
4. **MVP vertical slices** map almost 1:1 to the 6 surfaces: each slice = `[new read endpoint if needed] → BFF route handler → UI view → MSW + Vitest test`. Slice order should front-load the Bearer/BFF foundation + the DLQ slice (highest value, mostly backed), then monitor/cost/funnels/conversations.

---

## 1. Backend surface inventory — what exists vs what's missing

Verified by reading every file in `brave/api/routers/` plus `main.py`/`deps.py`.

### Present and reusable as-is
| Endpoint | File | Returns / does | Dashboard use |
|---|---|---|---|
| `GET /api/v1/health` | `health.py` | DB + Redis liveness | header status pill |
| `GET /api/v1/metrics` | `metrics.py` | `{nascente_count, rio_count:{in_progress,mar,dlq,descarte}, mar_count}` — **counts only** | monitor volume tiles (partial) |
| `GET /api/v1/dlq?uf&entity_type&limit` | `dlq.py` | list of DLQ rows: `id, nascente_id, entity_type, uf, routing, dlq_reason, score, score_version, canonical_key` — **no `score_breakdown`, no payload, no signals, no whatsapp log** | DLQ queue list |
| `PATCH /api/v1/dlq/{id}/reprocess` | `dlq.py` | 202, steward-auth | DLQ reprocess action |
| `PATCH /api/v1/dlq/{id}/validate` | `dlq.py` | 202, sets `validacao_humana_value=100` → `reprocess_record` → push if `mar`; steward-auth | DLQ approve / edit→re-score |
| `POST /api/v1/dlq/validate-batch?uf&entity_type&limit` | `dlq.py` | 202 batch-by-state; steward-auth; `limit≤1000` | DLQ **batch-by-state** mode (DASH-01) |
| `PATCH /api/v1/dlq/{id}/descarte` | `dlq.py` | 200, steward-auth | DLQ reject |
| `GET /api/v1/atrativos/gate?uf&limit` | `atrativos_gate.py` | list of `attraction` rows where `sub_state='aguardando_consulta_whatsapp'`: `rio_id,uf,sub_state,routing,dlq_reason,score,score_version,canonical_key,normalized` | WhatsApp gate queue (DASH-03) |
| `PATCH /api/v1/atrativos/gate/{id}/approve` | `atrativos_gate.py` | flips to `whatsapp_in_progress`, enqueues outreach; steward-auth | gate approve (DASH-03) |
| `PATCH /api/v1/atrativos/gate/{id}/reject` | `atrativos_gate.py` | routes to dlq/descarte; steward-auth | gate reject (DASH-03) |
| inbound + quality-rating endpoints | `atrativos_gate.py` | n8n inbound relay / quality flag | not directly a dashboard view; quality-rating value feeds DASH-03 context |
| `GET /api/v1/audit?...` | `audit.py` | audit rows | monitor audit feed (DASH-02) |

### MISSING — new read-only endpoints this phase must add (D-01)
All are **read-only GET aggregations**, all logic in Python, no pipeline changes. Recommend a new `brave/api/routers/dashboard.py` (or extend the relevant router); register in `brave/api/main.py`.

| New endpoint (proposed) | Aggregates | Backs |
|---|---|---|
| `GET /api/v1/dlq/{rio_id}` (detail) | `RioRecord` full: `normalized`, **`score_breakdown`** (per-criterion §7.6), `dlq_reason`, `sub_state`; joined `NascenteRecord.payload`; signals (from `normalized`/payload); WhatsApp log (audit rows / conversation log for this `rio_id`) | **DASH-01** detail panel + per-criterion explainability |
| `GET /api/v1/monitor` (or extend `/metrics`) | counts **plus** approval/rejection/DLQ **rates** (derive from `AuditLog` action counts: `dlq_validated` / `dlq_rejected` / `dlq_reprocessed` over a window), throughput (RioRecord `processed_at` per interval), failure alerts (PoisonQuarantine count, RED quality flag) | **DASH-02** |
| `GET /api/v1/cost?group_by=lane\|model&since=` | `LLMGeneration` grouped by `lane` and/or `model_slug`/`resolved_provider`: `sum(usd_cost)`, `sum(prompt_tokens+completion_tokens)`, count | **DASH-04** |
| `GET /api/v1/funnels?entity_type&uf&source` | `NascenteRecord`→`RioRecord`(routing)→`MarRecord` counts grouped by `uf` and `source`/`entity_type` (funnel stages: ingested → in_progress → mar/dlq/descarte) | **DASH-05** funnels |
| `GET /api/v1/conversations` + `GET /api/v1/conversations/{rio_id}` | conversation list + transcript for an atrativo — **source TBD (see §4)** | **DASH-05** conversations |

> **Planner note:** keep these endpoints behind the **read-only Bearer dependency** (not steward) — they are reads the operator dashboard makes. Mutations continue to require steward (or accept Bearer too; see §3). Do not duplicate the existing DLQ/gate mutation endpoints — the UI calls them directly through the BFF.

---

## 2. Relevant data model (from `brave/core/models.py`)

- **`RioRecord`** (`rio_records`): `routing`(in_progress/mar/dlq/descarte), `sub_state`, `normalized`(JSON), **`score`**(Numeric 5,2), **`score_breakdown`**(JSON — per-criterion §7.6: origem/completude/corroboração/atualidade/validação-humana), `score_version`, `dlq_reason`, `processed_at`, `uf`, `entity_type`, `municipio_id`, `canonical_key`, `nascente_id`→`NascenteRecord`. **`score_breakdown` is the field DASH-01's per-criterion panel renders** — the existing `GET /api/v1/dlq` list omits it, hence the detail endpoint.
- **`NascenteRecord`** (`nascente_records`): `source`, `source_ref`, `entity_type`, `uf`, **`payload`**(JSON, raw), `content_hash`, `version`, supersession. → DLQ detail "Nascente payload"; funnels by `source`.
- **`MarRecord`** (`mar_records`): `entity_type`, `source_ref`, `canonical`(JSON), `provenance`(JSON), `reliability_score`, `score_version`, supersession. → funnel terminal stage.
- **`LLMGeneration`** (`llm_generations`): **`lane`**, **`model_slug`**, `resolved_provider`, `prompt_tokens`, `completion_tokens`, **`usd_cost`**(Numeric 10,6), `created_at`. → DASH-04 cost-by-lane/model is a straight `GROUP BY`.
- **`AuditLog`** (`audit_log`): `action`, `entity_type`, `record_id`, `before_state`, `after_state`, `actor`, `created_at`. → DASH-02 rates/audit feed (count actions over window); also the per-record WhatsApp/steward event log for DLQ detail.
- **`PoisonQuarantine`** (`poison_quarantine`): `task_name`, `error_message`, `quarantined_at`. → DASH-02 failure alerts.
- **`ConsentLog`** (`consent_log`): `phone_e164`, `rio_id`, `legal_basis`, `norteia_identified`, `opted_out`, `opted_out_at`, `last_contact_at`, `purpose`. → DASH-03 gate context (opt-out / ramp), DASH-05 conversation metadata. **Never expose PII (phone) to the browser unless minimized — LGPD minimization (PROJECT constraint).**

---

## 3. Auth — Bearer BFF bridge (D-02), grounded in current code

**Current backend auth** (`dlq.py` / `atrativos_gate.py`): `require_steward` reads `X-Steward-Secret`, compares with `hmac.compare_digest` against `StewardConfig.secret` (fail-closed: unset secret rejects all). `settings.py` confirms `StewardConfig` and `WebhookConfig` both carry a comment: *"Phase 4 (DASH-06) replaces this with the dashboard's Bearer-header auth."*

**Recommended implementation:**
1. **FastAPI Bearer dependency** in `brave/api/deps.py` (or a small `auth.py`): `require_bearer(authorization: str = Header(None, alias="Authorization"))` — strip `Bearer `, `hmac.compare_digest` against a new `DashboardConfig.bearer_token` (pydantic-settings, `BRAVE_DASHBOARD_BEARER_TOKEN`, fail-closed, never logged — copy `StewardConfig` verbatim, swap header). Apply to the new read endpoints; **also accept it on the existing mutation endpoints** so the dashboard's single token works end-to-end (keep `X-Steward-Secret` working for back-compat / direct callers → an `either-or` dependency).
2. **Next.js BFF** (`dashboard/app/api/.../route.ts` Route Handlers): the browser sends `Authorization: Bearer <operator-token>`; the Route Handler validates it (compare to a server env operator token), then `fetch`es FastAPI injecting the **server-held** `Authorization: Bearer <service-token>` (env, never shipped to client). One operator token this milestone (CONTEXT deferred: multi-user/RBAC later). Return 401 on missing/bad browser token before forwarding.
3. **Testability:** because all data flows browser → Route Handler → FastAPI, MSW intercepts at the **FastAPI URL** (the `fetch` inside the Route Handler) for route-handler tests, and at the **BFF URL** for component tests. Bearer auth-fail is a first-class MSW case (return 401, assert UI surfaces it).

---

## 4. Conversation transcript — the one real unknown (DASH-05)

`brave/tasks/pipeline.py` (outreach/resume tasks) uses **LangGraph `AsyncPostgresSaver`**: `saver.setup()` creates `checkpoints` + `checkpoint_blobs` tables; multi-turn state (message history, extraction, opt-out) is stored as serialized LangGraph checkpoint blobs keyed by thread id. There is **no plain `messages` table**.

Two options for the planner to choose:
- **(A) Read LangGraph checkpoints directly** — decode the latest checkpoint blob for the atrativo's thread and extract `messages`. Pro: no new write path. Con: couples a read endpoint to LangGraph's internal serialization (msgpack/blob), brittle across LangGraph upgrades, awkward to test offline.
- **(B, recommended) Append-only `conversation_message` log** — a thin table (`rio_id, phone_e164(minimized), direction, role, content, extracted, created_at`) written by the outreach + resume tasks alongside the existing LangGraph persistence. The transcript read endpoint is then a trivial `SELECT ... ORDER BY created_at`. Pro: decoupled, offline-testable, LGPD-minimizable, mirrors the project's "logic+state in our own tables" posture (like `ConsentLog`). Con: adds a small Alembic migration + two write calls in the tasks (a backend touch — but read-only-to-the-dashboard still holds).

**Recommendation: Option B.** It keeps the dashboard a pure REST consumer, stays offline-testable, and avoids coupling the UI to LangGraph internals. Flag as a planner decision; if the team wants zero backend writes, fall back to A for read-only transcript reconstruction.

---

## 5. Frontend stack — current versions & patterns (June 2026)

Confirmed current (CLAUDE.md pins + web check): **Next.js 16** (App Router) · **React 19** · **Bun 1.3** · **Node 22** · **Tailwind CSS v4** · **shadcn/ui** (vendored components, Tailwind-v4-compatible) · **Recharts 3** (monitor/funnels/cost charts) · **TanStack Table v8** (queue/data tables) · **TanStack Query v5** (server-state, mutations, polling) · **Vitest 4** · **MSW 2**.

Key patterns:
- **App Router server/client boundary:** Route Handlers (`app/api/**/route.ts`) are the BFF/server tier; interactive queues (DLQ, gate) are **Client Components** wrapped in a `QueryClientProvider`. Static shells (monitor layout, nav) can be Server Components. Provide a single `getQueryClient()` per request on the server, a singleton on the client (standard TanStack Query App-Router setup).
- **Mutations + optimistic re-score (DASH-01 edit→re-score):** `useMutation` with `onMutate` optimistic update of the cached DLQ row, `onSettled` → `invalidateQueries(['dlq'])`. Re-score is async (backend returns 202 + new `routing`); refetch the row/list to show the post-re-score routing.
- **Live monitor polling (DASH-02):** `useQuery({ refetchInterval })` on `/api/v1/monitor`; no WebSocket this milestone (CONTEXT deferred). Pick a sane interval (e.g. 5–15s) — planner's discretion.
- **Tables:** TanStack Table v8 + shadcn table primitives for the DLQ/gate queues (sorting, batch-row selection for batch-by-state). Batch-by-state = a UF filter + multi-select → `POST /validate-batch?uf=`.
- **Charts:** Recharts 3 — bar/area for monitor throughput & funnels, stacked bar/pie for cost-by-lane/model. shadcn ships a Recharts `chart` wrapper.
- **Testing (Vitest 4 + MSW 2 under Bun):** component tests render Client Components with a test `QueryClientProvider` and an MSW server mocking the BFF/FastAPI responses (success / empty / error / 401). Route-Handler tests invoke the handler and assert it forwards with the injected secret + maps auth-fail. **Bun gotcha:** run Vitest via `bunx vitest` / `bun run test`; jsdom/happy-dom environment for component tests; ensure MSW's `setupServer` (Node) not the browser worker. Keep everything offline — no real FastAPI in the default suite (mirrors the collector's offline-by-default mandate).

---

## 6. MVP vertical-slice decomposition (this phase is `Mode: mvp`)

Each slice is thin and end-to-end: **[new read endpoint if needed] → BFF route handler → UI view → MSW + Vitest test**. Suggested slice set & order (planner finalizes waves):

0. **Foundation slice** — scaffold `dashboard/` (Next 16 App Router, Bun, Tailwind v4, shadcn init, TanStack Query provider, Vitest+MSW harness) **+ Bearer FastAPI dependency + BFF auth Route Handler + login/token gate**. Proves one real authenticated round-trip (browser Bearer → BFF → FastAPI `/health` or `/metrics`). This is the Walking-Skeleton-equivalent for the dashboard. (DASH-06.)
1. **DLQ slice** (DASH-01) — `GET /dlq/{id}` detail endpoint + DLQ queue list/detail UI (per-criterion `score_breakdown` panel, batch-by-state, approve/reject/edit→re-score/reprocess via existing PATCH endpoints). Highest value; mostly backed already.
2. **Monitor slice** (DASH-02) — `GET /monitor` endpoint (rates/throughput/alerts from AuditLog/PoisonQuarantine/metrics) + monitor view (tiles + Recharts).
3. **WhatsApp gate slice** (DASH-03) — gate queue UI over existing `/atrativos/gate` + approve/reject + ramp/quality context (quality-rating flag, ConsentLog).
4. **Cost slice** (DASH-04) — `GET /cost` group-by endpoint + cost view (Recharts by lane/model).
5. **Funnels + Conversations slice** (DASH-05) — `GET /funnels` + `GET /conversations[/{id}]` (+ Option-B `conversation_message` log + migration if chosen) + funnels/conversations views.

Each slice independently shippable and offline-testable. DASH-06 (auth) is satisfied in slice 0 and exercised by every later slice.

---

## 7. Risks / watch-items for the planner

- **R1 — Backend-gap underestimation.** Five of six surfaces need a new endpoint; if the planner treats this as "UI only" the phase fails verification (no data source). Each slice MUST pair its UI with its endpoint. (D-01.)
- **R2 — Conversation transcript source (§4).** Decide Option A vs B explicitly; B adds a small Alembic migration + task writes. Don't leave the transcript endpoint hand-waved.
- **R3 — LGPD minimization.** Don't ship `phone_e164` / raw PII to the browser. Minimize in the conversation/gate endpoints (mask phone, expose only what the operator needs).
- **R4 — Auth coexistence.** New Bearer dependency must coexist with `X-Steward-Secret` on mutation endpoints (either-or), or the dashboard's single token can't drive approve/reject. Don't break the Phase 2/3 steward tests.
- **R5 — Offline test mandate.** No real FastAPI/Postgres in the default dashboard suite; MSW mocks the network. The new Python endpoints get their own offline pytest coverage on the backend side (reuse the Phase 1 FastAPI test harness in `tests/integration/test_fastapi_endpoints.py`).
- **R6 — Version churn.** Next 16 / Tailwind v4 / shadcn vendored components / MSW 2 move fast; pin exact versions at scaffold time and commit the lockfile (`bun.lock`).
- **R7 — `dashboard/` is greenfield.** No prior conventions; the foundation slice sets them. Mirror `norteia-frontend` conventions per CLAUDE.md if that repo is reachable; otherwise establish here.

## Validation Architecture

(Nyquist validation is disabled for this run — `nyquist_validation_enabled=false`. This section is a stub so downstream tooling that greps for it does not error; no VALIDATION.md is required.)

Offline-by-default: every UI view tested with Vitest 4 + MSW 2 (success/empty/error/401); every new FastAPI read endpoint tested with the existing offline pytest+TestClient harness. No external network in the default suite.

## RESEARCH COMPLETE

Sources: [Next.js 16 + shadcn/ui admin dashboards (2026)](https://adminlte.io/blog/nextjs-admin-dashboards-shadcn/), [shadcn admin dashboard templates 2026](https://adminlte.io/blog/shadcn-admin-dashboard-templates/) — confirmed current stack (Next 16, React 19, Tailwind v4, shadcn/ui, Recharts 3, TanStack Table v8). Backend facts grounded in direct reads of `brave/api/`, `brave/core/models.py`, `brave/config/settings.py`, `brave/tasks/pipeline.py`.
