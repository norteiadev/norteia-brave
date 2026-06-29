# Quick Task 260629-qny: Painel motor start drops source — Context

**Gathered:** 2026-06-29
**Status:** Ready for planning

<domain>
## Task Boundary

In the Painel shell, selecting **TripAdvisor** in the "Origem dos dados" modal and starting the
motor still makes the backend sweep `source=default` (Google Places). Selecting TripAdvisor only
injects the TA session; it never activates the source, and the Painel start control never sends
`source`. Fix so the selected origem source actually reaches `POST /api/v1/engine/start`.

Out of scope: changing collection logic, the cURL/session inject flow, or EngineControl (/processo)
which already passes source correctly.
</domain>

<decisions>
## Implementation Decisions (LOCKED)

### Approach (operator-confirmed)
- **Backend:** add a lightweight endpoint to set the active collection source WITHOUT starting a
  run — e.g. `POST /api/v1/engine/source {source}` → validate against `collection_engine._VALID_SOURCES`
  (`default|tripadvisor`), then `collection_engine.set_source(redis, source)`. Reuse the same
  `require_steward_or_bearer` auth dependency as the other engine routes. Return current source.
  Invalid source → 422 (mirror the existing source guard in `engine/start`).
- **Dashboard:**
  - `PainelOrigem.tsx` `onSave` ("Salvar origem") activates the chosen source via the new endpoint.
    For `tripadvisor`, call it AFTER a successful `injectTASession`. For other sources, call it too
    (so switching back to default/mtur is honored) — but note only `default|tripadvisor` are valid
    backend sources today; map the UI radios accordingly (mtur is not a valid engine source — keep
    current behavior for mtur, do not send an invalid source).
  - `PainelTopbar.tsx:137` start mutation passes `source` into `startEngine`. Source = the active
    source from `/status` (`data?.source`, already read at `PainelTopbar.tsx:151`), which now
    reflects what "Salvar origem" set. So start sends the real selected source, not `{depth}` only.
- **Keep `EngineControl.tsx` (/processo) untouched** — it already passes `source: selectedSource`.

### Tests (TDD)
- Dashboard (vitest + MSW): (a) after selecting TripAdvisor + saving origem, the engine-start
  request body includes `source: "tripadvisor"`; (b) "Salvar origem" issues the set-source call
  (TA: after inject). Runner: `cd dashboard && bun run test`.
- Backend (pytest): new set-source endpoint — valid source 200 + persists via set_source; invalid
  source 422; auth required. Runner: `.venv/bin/python -m pytest` (do NOT source .env; keep
  RUN_REAL_EXTERNALS unset).

### Safety / scope
- Don't break the existing `taBlocked` gate (`PainelTopbar.tsx:160-162`) or the depth menu flow.
- No change to `engine/start`'s own `source` default behavior (still defaults `default` when omitted)
  — the fix is that the Painel now always sends it.
</decisions>

<specifics>
## Precise bug trace (from code exploration)

- **Start control (Painel):** `dashboard/components/painel/PainelTopbar.tsx:289-309` motor switch →
  `onPickDepth` (:211-214) → `start.mutate(depth)`. Start mutation **`:136-137`**:
  `mutationFn: (depth) => startEngine({ depth })` — **no `source` sent**. ← root cause.
- **API client:** `dashboard/lib/engine-api.ts:143-156` `startEngine(body?)` — `source` is an optional
  param (:148); body = `JSON.stringify(body ?? {})` (:154). Whatever caller passes.
- **Origem save:** `dashboard/components/painel/PainelOrigem.tsx:197-210` `onSave` (button :396-404):
  non-TA → toast only (:198-201); TA → only `injectTASession(...)` (:204-209). **Sets no active
  source anywhere** (no store/context/localStorage/backend call). Local `source` state (:139) dies
  with the modal.
- **Painel source display:** `PainelTopbar.tsx:151` `const source = data?.source ?? "default"` — from
  `/status` echo; used for label (:268), `taBlocked` (:160-162), modal preselect (:346). Never fed
  into start body.
- **Backend default:** `brave/api/routers/engine.py:163` `source = body.get("source", "default")` →
  defaults `default` when dashboard omits it; then `collection_engine.set_source(redis, source)`
  (:189). Status `source` is only set BY a start that passed it → closed loop that never becomes
  `tripadvisor` from the Painel.
- **Correct reference:** `dashboard/components/engine/EngineControl.tsx:122-128` passes
  `source: selectedSource` (+ `ufs` for TA) — but it's only on `/processo`, separate selector.
- **Engine source setter exists:** `brave/core/engine.py:47` `_SOURCE_KEY`, `set_source(...)`,
  `_VALID_SOURCES`. Source guard precedent in `engine/start` `brave/api/routers/engine.py:163` and
  the 422 at the invalid-source branch.

## Live evidence (2026-06-29)
Operator selected TripAdvisor, injected session (status 200, "Sessão reconhecida"), started motor →
backend logged `engine_started source=default` and ran the Places lane (place_id=ChIJ..., 27 UFs).
No `sweep_tripadvisor` ran. Matches the trace exactly.
</specifics>

<canonical_refs>
## Canonical References
- Engine source contract: `brave/core/engine.py` (`_SOURCE_KEY`, `set_source`, `_VALID_SOURCES`),
  `brave/api/routers/engine.py` (`/engine/start` source validation + `set_source`).
- Test rules: dashboard `cd dashboard && bun run test` (vitest+MSW); backend `.venv/bin/python -m
  pytest`, RUN_REAL_EXTERNALS unset.
- Related TA quick tasks: 260629-e69 (taBlocked gate in PainelTopbar — don't regress), 260629-p2v
  (TA session refresh).
</canonical_refs>
