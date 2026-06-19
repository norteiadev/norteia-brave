# Phase 8: Ops CMS — Destinos/Atrativos CRUD + Process Observability - Context

**Gathered:** 2026-06-18
**Status:** Ready for planning

<domain>
## Phase Boundary

Give the operator a **visual, browsable CMS** over the territorial data the Brave pipeline produces, plus **24/7 process observability**. Today the Next.js dashboard only exposes the two human-review queues (DLQ + WhatsApp gate) + read-only aggregates (monitor/cost/funnels/conversations) — there is **no way to list/view/edit destinos or atrativos across all stages**, and **no way to see workers running, failures, or each record's journey to Mar**.

**This phase delivers (MÉDIO scope — confirmed with user):**
1. **Cores only** — port the Norteia brand tokens (navy `#082B5B`, terracota `#B14A36`, success green, warning amber) into the dashboard's Tailwind v4 theme; light reskin of existing screens. NO new shell/sidebar, NO font swap, NO i18n library.
2. **Status badges** — one reusable `<StageBadge>` system that encodes each record's pipeline stage with color semantics (routing, atrativos `sub_state`, score band, source/origem, "pending human validation"). The visual feedback of "what's happening" to every record.
3. **CRUD Destinos + Atrativos** — list (filters + badges), detail (score breakdown + journey + children), edit canonical + actions (promote/reject/reprocess for destinos; edit/approve/descartar/advance for atrativos).
4. **Process observability page** — see all **workers** (Celery up/down, active/queued tasks, queue depth), **failures** (quarantine), **human-pending** counts (DLQ + WhatsApp gate), and a **journey/funnel** view tracking every record's progress through each Brave step until it reaches **Mar**.

**Out of scope:** new sidebar/shell, fonts, i18n lib, WhatsApp send changes, score-engine changes, norteia-api push.
</domain>

<decisions>
## Implementation Decisions

### Cores (design = colors only)
- **D-01:** Port the Norteia color tokens into the dashboard theme (`dashboard/app/globals.css` Tailwind v4 `@theme`/CSS vars), light+dark: primary **navy `hsl(211 83% 19%)`** (`#082B5B`), accent **terracota `hsl(11 53% 46%)`** (`#B14A36`), **success `hsl(142 76% 36%)`**, **warning `hsl(38 92% 50%)`**, destructive red, off-white background `hsl(40 33% 98%)`. Map to the existing token names the dashboard already uses (primary/accent/etc.) so existing screens reskin by token swap. Do NOT change the sidebar/shell/fonts (the dashboard has no sidebar — keep its current nav shell), NO Open Sans/Montserrat swap.

### StageBadge system (the visual process feedback)
- **D-02:** One reusable `<StageBadge>` component (+ small helper mapping value→{label, color, intent}) covering every pipeline stage, reused across all listings, detail headers, and the observability page:
  - **routing**: `mar` → success/green, `dlq` → warning/amber, `descarte` → muted/gray, `in_progress` → navy/info.
  - **atrativos `sub_state`**: `discovered`, `contacts_found`, `signals_gathered`, `aguardando_consulta_whatsapp`, `whatsapp_in_progress`, terminal — each a distinct step color (navy gradient by progress).
  - **score band**: ≥85 green (Mar-eligible), 40–84.9 amber (DLQ), ≤40 gray/red (descarte).
  - **source/origem**: `mtur`, `notebooklm`, `desmembramento`(origem=40), `places_discovery` — neutral chips.
  - **validation-pending**: a flag chip when a record awaits human validation (in DLQ or WhatsApp gate).
  - Color values come from the D-01 tokens (success/warning/navy/muted), never hardcoded hex.

### CRUD Destinos
- **D-03:** Backend (Bearer-guarded reads; steward/Bearer mutations — reuse Phase-4 `require_bearer`/`require_steward`):
  - `GET /api/v1/destinos` — list destino records across **all** routings (Mar + DLQ + descarte), filters: `uf`, `source`, `routing`, score band, free-text `q`; paginated; returns badge fields (routing, score, source/origem, validation-pending) + key canonical fields. Joins MarRecord (promoted) + RioRecord (not yet Mar).
  - `GET /api/v1/destinos/{id}` — detail: canonical, `score_breakdown`, provenance, **journey** (AuditLog trail), and child atrativos summary (count + stage breakdown).
  - `PATCH /api/v1/destinos/{id}` — edit canonical fields + actions: `promote` (reuse `validate_and_promote_rio`), `reject`/`descarte`, `reprocess` (re-score). Audit-logged.
  - Frontend: `/destinos` list (TanStack react-table + filters + `<StageBadge>`), `/destinos/[id]` detail+edit (reuse `ScoreBreakdownPanel`/`ReviewPanel` where it fits) + journey stepper (D-06).

### CRUD Atrativos
- **D-04:** Backend:
  - `GET /api/v1/atrativos` — list across all FSM stages, filters: `uf`, `sub_state`, `parent_mar_id`, `routing`; badges. (The link is in `rio.normalized['parent_mar_id']` — Phase 7.)
  - `GET /api/v1/atrativos/{id}` — detail: FSM journey, contacts, signals, score, parent destino link.
  - `PATCH /api/v1/atrativos/{id}` — edit canonical + actions: approve/descartar/`advance_sub_state` (reuse `brave/lanes/atrativos/state_machine.advance_sub_state`). Mask PII (`phone_e164`) using the existing `mask_phone` helper from `atrativos_gate.py`.
  - Frontend: `/atrativos` list + `/atrativos/[id]` detail+edit + journey stepper.

### Process observability page (acompanhar 24/7)
- **D-05:** New page `/processo` (or extend `/monitor`) backed by two new endpoints (read-only, Bearer-guarded), live-polled (`refetchInterval`, mirror existing `useMonitor`):
  - `GET /api/v1/workers` — `celery_app.control.inspect()`: which workers are **up/down** (ping), **active**/**reserved**/**scheduled** task counts per worker, registered tasks; **queue depth** via Redis `LLEN` per queue (`brave.sweep`, default); beat schedule summary (from `beat_schedule.py`). MUST degrade gracefully (clear "broker unreachable / no workers" state) when Celery/Redis is down — never 500.
  - `GET /api/v1/failures` — `PoisonQuarantine` list (task_name, error, payload-summary, created_at) + counts by task; retry/quarantine totals.
  - Page composition: **worker board** (up/down tiles + active tasks + queue depth), **failures panel** (recent quarantine), **human-pending tiles** (DLQ count + WhatsApp gate count — reuse existing endpoints), and a **per-lane stage funnel** (destinos: Nascente→Rio→score→DLQ/Mar; atrativos: FSM `sub_state` distribution) so the operator sees progress toward Mar at a glance.

### Journey stepper
- **D-06:** Reusable `<JourneyStepper>` rendering a record's path to Mar from `AuditLog` + current `routing`/`sub_state`: destinos `Nascente → Rio (score) → DLQ → [steward] → Mar`; atrativos `discovered → contacts_found → signals_gathered → score → [gate] → [outreach] → Mar/DLQ`. Highlights the current step; shows history (timestamp + actor per `AuditLog` row). Used in both detail pages and (compact) in `/processo`.

### Scope fence + testing
- **D-07:** NO sidebar/shell rebuild, NO font swap, NO i18n lib, NO WhatsApp send / score-engine / norteia-api changes. Tests: dashboard **Vitest + MSW** (mock the new endpoints; assert badges, lists, edit, journey, worker board states incl. broker-down). Backend **pytest offline** for the new endpoints (Bearer guard, response shapes, mask PII, graceful worker-inspect with `celery_app.control.inspect` mocked — NO real broker in CI). 100% offline mandate holds.

### Claude's Discretion
- Whether `/processo` is a new route or an enhanced `/monitor`; exact filter param names; pagination style; whether destinos list is one unified endpoint or Mar+DLQ merged client-side; StageBadge color shades within the token palette; react-table column set; polling interval.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Backend — endpoints to mirror + reuse
- `brave/api/routers/dashboard.py` — existing `GET /dlq`, `GET /dlq/{rio_id}`, `GET /monitor` shapes (mirror for destinos/atrativos list/detail; monitor for funnel aggregates).
- `brave/api/routers/dlq.py` — DLQ list + `POST validate` (steward edit→re-score) pattern to reuse for PATCH actions.
- `brave/api/routers/atrativos_gate.py` — gate list + `mask_phone` (REUSE for atrativo PII) + ramp-context.
- `brave/core/dlq/service.py` — `validate_and_promote_rio` (destino promote action).
- `brave/lanes/atrativos/state_machine.py` — `advance_sub_state` + the `sub_state` value set (badge labels + atrativo advance action).
- `brave/core/models.py` — `RioRecord` (routing, sub_state, dlq_reason, score, score_breakdown, normalized, entity_type, uf, municipio_id), `MarRecord` (source_ref, canonical, reliability_score, provenance, parent_mar_id), `AuditLog` (action/actor/before/after/created_at — the journey source), `PoisonQuarantine` (failures source).
- `brave/tasks/celery_app.py` (the `Celery("norteia_brave")` app → `.control.inspect()`) + `brave/tasks/beat_schedule.py` (queues + 27-UF beat) — worker/queue/beat data for `GET /workers`.
- Phase-4 Bearer auth: `require_bearer` / `require_steward` dependencies (guard the new endpoints).

### Frontend — dashboard to extend + reskin
- `dashboard/app/globals.css` (+ tailwind v4 theme) — where the D-01 color tokens land.
- `dashboard/app/page.tsx` (nav shell) — add nav links to `/destinos`, `/atrativos`, `/processo`.
- `dashboard/app/dlq/page.tsx` + its `QueueList`/`ReviewPanel`/`ScoreBreakdownPanel` + the `useMonitor`/`useDlq`/`useCost` hooks — reuse patterns for the new list/detail/edit + polling.
- The BFF Route Handler (Phase-4 catch-all proxy that injects the service Bearer) — the new endpoints route through it.
- `dashboard` stack: Next 16, Tailwind v4, `@tanstack/react-table` + `react-query`, `recharts`, `lucide-react`, `next-themes` (already present — no new deps expected).

### Design tokens (colors only)
- Norteia palette (from design-system analysis): navy `#082B5B` = `hsl(211 83% 19%)`, terracota `#B14A36` = `hsl(11 53% 46%)`, success `hsl(142 76% 36%)`, warning `hsl(38 92% 50%)`, bg `hsl(40 33% 98%)`. Reference repo: `/Users/leandro/Projects/norteia/norteia-frontend/src/app/globals.css` (tokens) — colors only, do NOT copy shell/fonts.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `ScoreBreakdownPanel` / `ReviewPanel` / `QueueList` (Phase 4) — reuse for destino/atrativo detail + edit.
- `useMonitor` polling hook (refetchInterval) — mirror for `/processo` live worker board.
- `mask_phone` (atrativos_gate.py) — reuse for atrativo PII.
- `validate_and_promote_rio`, `advance_sub_state` — the mutation actions; do NOT re-implement.
- `AuditLog` rows already exist for every state change → the journey stepper is a read over existing data.
- `@tanstack/react-table` already a dependency — use for the listings.

### Established Patterns
- Bearer-at-edge: browser Bearer validated by BFF Route Handler, service secret injected server-side; new endpoints follow the same guard.
- Read endpoints Bearer-guarded; mutations steward/Bearer either-or (R4).
- PII never returned raw (mask_phone); llm/prompt content never logged.

### Integration Points
- New endpoints register on the existing dashboard/atrativos routers (or a new `cms`/`workers` router).
- New pages mount in the existing dashboard app router + nav shell; theme tokens are global.
- `celery_app.control.inspect()` needs the broker; the endpoint must handle broker-absent (CI + local-without-worker) gracefully.
</code_context>

<specifics>
## Specific Ideas

- The operator's core need: open the dashboard and SEE, at a glance — which workers are running, what failed, what's waiting on a human, and where every destino/atrativo is on its path to Mar. The `/processo` page + `<StageBadge>` + `<JourneyStepper>` are the heart of this; the CRUD listings are how they drill in and act.
- Badges are the recurring primitive — same `<StageBadge>` in list rows, detail headers, funnel, and worker board.
- Live feel: poll workers/failures/pending like the existing monitor (no websockets this phase).
</specifics>

<deferred>
## Deferred Ideas
- Full Norteia design-system replication (sidebar shell, Open Sans/Montserrat, component library, i18n) — the GRANDE scope; deferred (user chose MÉDIO).
- WebSocket live updates (vs polling).
- Flower integration for deep Celery introspection.
- Bulk edit / batch actions on the new listings.
- norteia-api push status surfaced per record.
</deferred>

---

*Phase: 8-Ops CMS — Destinos/Atrativos CRUD + Process Observability*
*Context gathered: 2026-06-18*
