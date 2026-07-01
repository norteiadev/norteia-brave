---
phase: quick-260701-kiy
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - brave/api/routers/engine.py
  - tests/unit/api/test_nascente_projection.py
  - dashboard/lib/nascente-api.ts
  - dashboard/lib/painel-data.ts
  - dashboard/lib/__tests__/painel-data.test.ts
  - dashboard/components/painel/__tests__/RecordCard.test.tsx
  - dashboard/components/painel/PainelBoard.tsx
  - dashboard/components/painel/__tests__/PainelBoard.test.tsx
autonomous: true
requirements: [KIY-01]
must_haves:
  truths:
    - "GET /api/v1/nascente returns municipio (nome) + municipio_id per item, null-safe when absent"
    - "A Nascente board card shows the município under/near the name when present"
    - "A Nascente card with no município falls back to UF-only (as today), no crash"
    - "A Painel column with >100 cards renders 100 on load and grows +50 per scroll-to-bottom"
  artifacts:
    - path: "brave/api/routers/engine.py"
      provides: "_project_nascente_item helper + municipio/municipio_id in the LGPD allow-list projection"
      contains: "municipio"
    - path: "dashboard/lib/nascente-api.ts"
      provides: "municipio field on NascenteListItem"
      contains: "municipio"
    - path: "dashboard/components/painel/PainelBoard.tsx"
      provides: "Per-column client-side render windowing (visibleCount + IntersectionObserver)"
      contains: "IntersectionObserver"
  key_links:
    - from: "dashboard/lib/painel-data.ts"
      to: "PainelCard.municipality"
      via: "nascenteCards map reads n.municipio"
      pattern: "municipality:\\s*n\\.municipio"
    - from: "brave/api/routers/engine.py"
      to: "rec.payload.canonical.municipio"
      via: "_project_nascente_item projection"
      pattern: "canonical.*municipio|municipio"
---

<objective>
Surface the município on Nascente board cards (display-only) and add client-side
render windowing to the Painel Kanban columns so heavy columns stay responsive.

The município is already resolved and stored at ingest — `payload.canonical.municipio`
(nome) and `payload.municipio_id` (IBGE code) exist on every Nascente record across
mtur destino / tripadvisor atrativo / desmembramento. The API deliberately omits it
today behind an LGPD field allow-list. This plan adds it to the allow-list (as a
PUBLIC-GEO, non-PII field in the same class as name/uf), carries it to the frontend
card model, and renders it. The card component already renders `card.municipality`,
so the surface work is data-plumbing only.

Separately, each Kanban column currently renders every card in memory. Task 4 caps
initial render at 100 per column and lazy-reveals +50 on scroll-to-bottom via an
IntersectionObserver sentinel — pure client-side windowing (the single-fetch →
client-distribute board model makes per-column server pagination impractical).

Purpose: operators identify records by município at a glance; large columns render fast.
Output: município on Nascente cards end-to-end + per-column lazy render windowing.

Scope guardrails (do NOT touch): no ingest/resolution change (município already
resolved at Nascente), no Rio/Mar/pipeline logic, no new endpoints, no server pagination.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md

<interfaces>
<!-- Current shapes the executor works against — no codebase exploration needed. -->

brave/api/routers/engine.py — list_nascente projection (~line 113-123), current allow-list:
```python
items = [
    {
        "id": str(rec.id),
        "entity_type": rec.entity_type,
        "uf": rec.uf,
        "source": rec.source,
        "name": (rec.payload or {}).get("name") or rec.source_ref,
        "ingested_at": rec.ingested_at.isoformat() if rec.ingested_at else None,
    }
    for rec in rows
]
```
Data present in every payload: `payload.canonical.municipio` (nome, e.g. "Vila Velha")
and `payload.municipio_id` (IBGE code). Docstring LGPD note is at ~line 98-100.

dashboard/lib/nascente-api.ts — NascenteListItem:
```typescript
export interface NascenteListItem {
  id: string;
  entity_type: string; // "destination" | "attraction"
  uf: string | null;
  source: string | null;
  name: string | null;
  ingested_at: string | null;
}
```

dashboard/lib/painel-data.ts — nascenteCards map (~line 155-167) currently sets
`municipality: null`. Destino cards already derive municipality via
`municipalityFromCanonicalKey` (~line 130). PainelCard already has a
`municipality: string | null` field.

dashboard/components/painel/RecordCard.tsx (~line 74-78) ALREADY renders
`card.municipality` next to the UF chip, hidden when null. No change needed there.

dashboard/components/painel/PainelBoard.tsx — column body maps `column.cards.map(...)`
inline at ~line 99 inside `columns.map(...)`. Scroll container is the
`data-testid="painel-col-{key}"` div (line 89-98, `overflow-y-auto`).
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add municipio + municipio_id to the Nascente API projection</name>
  <files>brave/api/routers/engine.py</files>
  <behavior>
    - _project_nascente_item(rec) returns the existing allow-list PLUS
      municipio = rec.payload.canonical.municipio and municipio_id = rec.payload.municipio_id
    - TA atrativo payload with canonical.municipio="Vila Velha" → municipio="Vila Velha"
    - Mtur destino payload with canonical.municipio present → municipio surfaced
    - payload missing canonical → municipio=None, no KeyError
    - payload missing municipio_id → municipio_id=None
    - payload=None (or {}) → both None, no crash
  </behavior>
  <action>
    Extract the inline item dict from list_nascente (~line 113-123) into a module-level
    pure helper `_project_nascente_item(rec) -> dict` and call it in the list comprehension
    (`items = [_project_nascente_item(rec) for rec in rows]`). This makes the projection
    unit-testable offline without a DB (Task 3 targets this helper).
    Add two fields to the projected dict: `municipio` set to
    `(rec.payload or {}).get("canonical", {}).get("municipio")` and `municipio_id` set to
    `(rec.payload or {}).get("municipio_id")`. Both default to None. Guard the nested
    `.get("canonical", {})` so a None/absent canonical never raises. Keep every existing
    field unchanged. Do NOT return the raw payload wholesale.
    Update the LGPD docstring note (~line 98-100) to record municipio + municipio_id as
    APPROVED PUBLIC-GEO fields — NOT PII, same class as name/uf (público, geo-territorial).
  </action>
  <verify>
    <automated>BRAVE_USE_FAKEREDIS=1 env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest tests/unit/api/test_nascente_projection.py -q -p no:cacheprovider -W ignore::DeprecationWarning</automated>
  </verify>
  <done>_project_nascente_item exists, list_nascente uses it, projection includes municipio + municipio_id (None-safe), docstring updated. Task 3 test green.</done>
</task>

<task type="auto">
  <name>Task 2: Carry municipio into the frontend card model</name>
  <files>dashboard/lib/nascente-api.ts, dashboard/lib/painel-data.ts</files>
  <action>
    In nascente-api.ts: add `municipio: string | null;` and `municipio_id: string | null;`
    to the NascenteListItem interface (after `name`). No fetch change needed — apiFetch
    returns the raw JSON and TanStack passes it through; the new fields flow automatically.
    In painel-data.ts: in the `nascente.map(...)` inside toPainelCards (~line 155-167),
    change `municipality: null` to `municipality: n.municipio`. This mirrors how destino
    cards already carry municipality (via municipalityFromCanonicalKey ~line 130).
    Do NOT touch RecordCard.tsx — it already renders card.municipality near the UF chip
    (~line 74-78) and hides it gracefully when null. PT-BR copy/styling already in place.
  </action>
  <verify>
    <automated>cd dashboard && bun run test -- painel-data --run</automated>
  </verify>
  <done>NascenteListItem has municipio (+ municipio_id); toPainelCards sets nascente card municipality from n.municipio; typecheck + painel-data suite green.</done>
</task>

<task type="auto">
  <name>Task 3: Tests — API projection (offline) + frontend município render</name>
  <files>tests/unit/api/test_nascente_projection.py, dashboard/lib/__tests__/painel-data.test.ts, dashboard/components/painel/__tests__/RecordCard.test.tsx</files>
  <action>
    Backend (offline, no DB, no respx — pure helper): create
    tests/unit/api/test_nascente_projection.py importing `_project_nascente_item` from
    brave.api.routers.engine. Build lightweight fake records with types.SimpleNamespace
    (fields: id, entity_type, uf, source, source_ref, ingested_at=None, payload=dict).
    Cases: (a) TA atrativo payload {"canonical": {"municipio": "Vila Velha"}, "municipio_id": "3205200", "name": "..."} → asserts municipio=="Vila Velha", municipio_id=="3205200";
    (b) Mtur destino payload with canonical.municipio present → municipio surfaced;
    (c) payload without canonical → municipio is None, no KeyError; (d) payload without
    municipio_id → municipio_id is None; (e) payload={} → both None. Assert the existing
    keys (id/entity_type/uf/source/name) still project correctly.
    Dashboard (vitest+MSW): in painel-data.test.ts add a case that seeds a nascente item
    with municipio via the existing nascenteList mock factory and asserts the resulting
    PainelCard.municipality equals that município (and null when the item's municipio is
    null). In RecordCard.test.tsx add: a card with municipality set renders the município
    text; a card with municipality=null does not render it (UF-only). Match existing test
    patterns in these files. Keep all existing suites green.
  </action>
  <verify>
    <automated>BRAVE_USE_FAKEREDIS=1 env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest tests/unit -q -p no:cacheprovider -W ignore::DeprecationWarning && cd dashboard && bun run test</automated>
  </verify>
  <done>New backend projection tests pass offline; dashboard município render + card-model tests pass; full backend tests/unit and full dashboard suite stay green.</done>
</task>

<task type="auto">
  <name>Task 4: Kanban per-column render windowing (100 initial, +50 on scroll)</name>
  <files>dashboard/components/painel/PainelBoard.tsx, dashboard/components/painel/__tests__/PainelBoard.test.tsx</files>
  <action>
    Extract the per-column body currently inlined in PainelBoard's `columns.map(...)`
    (~line 65-116) into a `PainelColumn` sub-component in the same file so each column owns
    its own hook state. In PainelColumn: `const [visibleCount, setVisibleCount] = useState(100)`.
    Render only `column.cards.slice(0, visibleCount)`. Reset visibleCount to 100 whenever the
    column's data/filter identity changes — use `useEffect(() => setVisibleCount(100), [column.key, column.cards.length])`
    (length change is the observable proxy for a data/filter change; keep it simple, no deep compare).
    When `column.cards.length > visibleCount`, render a sentinel div at the bottom of the
    scroll container (the `overflow-y-auto` body div) and attach an IntersectionObserver
    (root = the scroll container ref) whose callback bumps `setVisibleCount(v => Math.min(v + 50, column.cards.length))`
    on intersect. Clean up the observer on unmount/re-attach. Preserve the existing count
    pill (shows total column.cards.length, NOT visibleCount), drop handlers, data-testids,
    RecordCard props, and the isPending "Carregando…" branch. Give the sentinel
    `data-testid="painel-col-sentinel-{column.key}"`. This is display windowing only —
    all cards remain in memory; no fetch/pagination change.
  </action>
  <verify>
    <automated>cd dashboard && bun run test -- PainelBoard --run</automated>
  </verify>
  <done>A column with >100 cards renders 100 RecordCards initially; simulating the sentinel intersect grows the rendered count by 50; count pill still shows the true total; existing PainelBoard behaviors unchanged.</done>
</task>

</tasks>

<verification>
- Backend: `BRAVE_USE_FAKEREDIS=1 env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest tests/unit -q -p no:cacheprovider -W ignore::DeprecationWarning` green (incl. new projection test).
- Dashboard: `cd dashboard && bun run test` green (incl. painel-data município, RecordCard município, PainelBoard windowing).
- GET /api/v1/nascente item shape now includes `municipio` + `municipio_id`; raw payload never returned wholesale.
- No changes under brave/lanes, brave/core pipeline, Rio/Mar, or new routes.
</verification>

<success_criteria>
- Nascente API projects municipio + municipio_id, null-safe, LGPD allow-list docstring updated.
- Nascente board cards display the município (UF-only fallback preserved).
- Each Painel column renders 100 cards initially and reveals +50 per scroll-to-bottom.
- Both test suites stay green; no pipeline/ingest/endpoint changes.
</success_criteria>

<output>
Create `.planning/quick/260701-kiy-surface-municipio-on-nascente-cards-api-/260701-kiy-SUMMARY.md` when done.
</output>
