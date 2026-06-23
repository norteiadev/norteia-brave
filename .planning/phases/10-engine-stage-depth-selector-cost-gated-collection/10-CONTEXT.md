# Phase 10: Engine Stage-Depth Selector (cost-gated collection) - Context

**Gathered:** 2026-06-23
**Status:** Ready for planning
**Source:** Operator conversation (locked decisions captured directly; discuss-phase bypassed)

<domain>
## Phase Boundary

Brave is the collector (Nascente → Rio → Mar/DLQ). `norteia-api` is a **separate** platform that only ever receives Mar-stage records via the existing idempotent push. The operator's problem: external cost (Google Places 1000/mo free tier + LLM budget) makes a full 24/7 sweep expensive, so collection must run "aos poucos" with an explicit cost ceiling per run.

This phase adds an **operator-selectable pipeline depth** to the existing engine start/stop control on `/processo`, turning the three medallion layers into real **cost checkpoints**, plus a per-entity "nascente" stage badge. It does NOT re-architect the lanes, move external calls, or add new data sources — those are deferred (see Deferred).

**In scope:** the depth selector (UI + required-to-start), depth persistence + API wiring, orchestrator/lane gating that honors the depth, the "nascente" StageBadge variant, offline tests.

**Out of scope (hard fence):** gov source for atrativos; moving Places to the Nascente→Rio edge; structured hours/price; free LLM model for Desmembramento; dedicated contacts table; any norteia-api change.
</domain>

<decisions>
## Implementation Decisions (LOCKED)

### Stage map as cost checkpoints
- **Nascente** = ingest + §7.6 score only. No Places, no LLM. Free.
- **Nascente → Rio** = Places + LLM validation (the paid step).
- **Rio → Mar** = WhatsApp + human validation (low score → gate; high score → Mar → push).

### Three depths and exact behavior
- `Apenas nascente`: dispatch only the **free Mtur seed** producer (`MturSeedIngest`). Write Nascente via `store_raw` + compute §7.6 score. Do NOT call `process_nascente_record` (Rio). Do NOT run `DesmembramentoAgent` (LLM). Do NOT run atrativos discovery (Places). Zero external cost.
- `Nascente → Rio`: run producers + validation up to Rio routing (`mar`-eligible / `dlq` / `descarte`). Do NOT promote to Mar. Do NOT dispatch the WhatsApp gate/outreach.
- `Nascente → Rio → Mar`: full pipeline, including the idempotent norteia-api Mar push (unchanged).

### Selector is required to start
- "Ligar motor" stays **disabled** until the operator picks a depth. No implicit default that silently spends.

### Atrativos caveat (LOCKED)
- Atrativos have **no free source today** (Discovery *is* Google Places). Therefore in `Apenas nascente` mode atrativos do not run — only destinos (Mtur). At `Nascente → Rio` and `Nascente → Rio → Mar`, atrativos run as today. A gov/official source that gives atrativos a free Nascente is a future phase (Phase B).

### Data model (LOCKED)
- Keep the **table-per-layer** model (NascenteRecord → RioRecord.nascente_id → MarRecord.rio_id). Do NOT migrate to a single `incoming_attractions(status)` table. Stage is implicit by table membership; the "nascente" badge covers the visual need.

### Contact channel (LOCKED)
- WhatsApp stays the **single** contact channel. Multichannel (SMS/IG/FB/email) is cut.

### Gating chokepoints (where the depth is enforced)
- Rio gate: the `store_raw → process_nascente_record` chaining inside lanes (e.g. `brave/lanes/destinos/mtur.py:149-162`) and the FSM kickoff for atrativos.
- Mar gate: `promote_to_mar` / the push dispatch.
- Producer selection: the orchestrator `engine_sweep_run` (`brave/tasks/pipeline.py:1550`) chooses which producers fan out per depth (Mtur-only for nascente; +Desmembramento/+atrativos for rio/mar).
- Prefer threading an explicit depth through `engine_sweep_run` → `sweep_uf` / `discover_atrativo_task` rather than scattering reads of Redis deep in the lanes. Read Redis once in the orchestrator; pass the depth down as a task arg.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Engine state + control
- `brave/core/engine.py` — Redis-backed engine state (`brave:engine:*`), `get_state/start_run/request_stop/get_status`. Add depth key + getter/setter here; extend `get_status`.
- `brave/api/routers/engine.py` — `GET /api/v1/engine/status`, `POST /api/v1/engine/start` (accepts body `{ufs, lane}` today — add `depth`), `POST /api/v1/engine/stop`. `require_steward_or_bearer` on mutations.
- `brave/tasks/pipeline.py:1550` — `engine_sweep_run(ufs, lane)` orchestrator; per-UF it fans out `sweep_uf.delay(uf)` (destinos) and `discover_atrativo_task.delay(uf)` (atrativos). Add `depth` param; gate fan-out + thread depth down.
- `brave/tasks/pipeline.py:755` — `sweep_uf` runs `MturSeedIngest` + `DesmembramentoAgent`. Must run Mtur-only under `Apenas nascente`.

### Lanes / pipeline seams
- `brave/lanes/destinos/mtur.py:105-162` — `MturSeedIngest.produce`: `store_raw` (Nascente) then `process_nascente_record` (Rio) chained at 149-162. The Rio gate lives here.
- `brave/core/nascente/service.py` — `store_raw` signature.
- `brave/core/rio/routing.py:84` — `process_nascente_record` (Rio entry); `route_by_score`.
- `brave/core/mar/service.py` — `promote_to_mar` (Mar gate).

### Dashboard
- `dashboard/components/engine/EngineControl.tsx` — start/stop panel on /processo. Add the depth selector here; disable start until chosen; send depth on start.
- `dashboard/lib/engine-api.ts` — `EngineStatus`/`startEngine` types + fetchers. Add `depth` to the type + start body.
- `dashboard/components/cms/StageBadge.tsx` — routing/sub_state/score/source badges. Add the "nascente" variant.
- `dashboard/app/processo/page.tsx` — hosts EngineControl.
- MSW handlers for engine (`dashboard/mocks/handlers/` or `dashboard/mocks/` — find the engine handler) — add `depth` field.

### Tests
- Backend pytest: fakeredis pattern used in existing engine tests (`test_engine_*`). Offline, no broker.
- Dashboard Vitest + MSW for EngineControl + StageBadge.
</canonical_refs>

<specifics>
## Specific Ideas

- Depth enum suggestion (backend + TS shared semantics): `nascente` | `nascente_rio` | `nascente_rio_mar`. Redis key e.g. `brave:engine:depth`. Default when absent: treat as unset → UI requires explicit choice; backend `start` should reject/400 if depth missing (so the required-selection contract is enforced server-side too, not only client-side).
- `get_status` should return `depth` so the dashboard can reflect the active run's depth.
- Keep the existing `lane` body param working; `depth` is orthogonal (lane = which entity families; depth = how far the pipeline runs). For `Apenas nascente`, the orchestrator forces Mtur-only regardless of lane (atrativos can't run free).
- The "nascente" badge: a record is "nascente-only" when a NascenteRecord exists with no corresponding RioRecord. The dashboard list/detail endpoints already surface stage via table membership; the badge is the visual.
</specifics>

<deferred>
## Deferred Ideas (future phases — DO NOT plan here)

- **Phase B:** gov/official source for atrativos (embratur/cadastur) → free atrativo Nascente; move Google Places to the Nascente→Rio edge; re-map the atrativos FSM so discovery/contact = Rio and WhatsApp = Rio→Mar.
- **Phase C:** structured validated data — opening hours (weekday/weekend/holiday) and price-per-person as fields, not free text.
- Free LLM model (e.g. an OpenRouter `:free` slug) for Desmembramento so it can run at Nascente — needs a data-collection/LGPD carve-out decision (free tier trains on prompts vs D-04 `data_collection="deny"`).
- Dedicated `incoming_attraction_contacts` table.
- Multichannel contact (SMS / Instagram Direct / Facebook Messenger / email).
</deferred>

---

*Phase: 10-engine-stage-depth-selector-cost-gated-collection*
*Context captured: 2026-06-23 from operator conversation (locked decisions)*
