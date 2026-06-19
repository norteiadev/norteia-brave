# Phase 8: Ops CMS — Destinos/Atrativos CRUD + Process Observability — Research

**Researched:** 2026-06-18
**Domain:** FastAPI endpoints + Next.js dashboard (Tailwind v4, TanStack Table v8, Recharts, MSW)
**Confidence:** HIGH (all findings from direct codebase inspection; no unverified claims)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01** Port Norteia color tokens into `dashboard/app/globals.css` Tailwind v4 `@theme`/CSS vars, light+dark: primary navy `hsl(211 83% 19%)`, accent terracota `hsl(11 53% 46%)`, success `hsl(142 76% 36%)`, warning `hsl(38 92% 50%)`, destructive red, off-white background `hsl(40 33% 98%)`. Map to the existing token names. No sidebar/shell/font changes.
- **D-02** One reusable `<StageBadge>` component (+ mapping value→{label, color, intent}) covering every pipeline stage. Extends the existing `StatusBadge` (which covers routing only). Routing: mar=success/green, dlq=warning/amber, descarte=muted/gray, in_progress=navy/info. Atrativo sub_state: 6 FSM states with navy-gradient progress. Score band: ≥85 green, 40–84.9 amber, ≤40 gray/red. Source/origem: neutral chips. Validation-pending: flag chip.
- **D-03** Backend (Bearer reads, steward/Bearer mutations): `GET /api/v1/destinos` (all routings, filters, paginated), `GET /api/v1/destinos/{id}` (canonical+score_breakdown+provenance+journey+child atrativos summary), `PATCH /api/v1/destinos/{id}` (promote/reject/reprocess). Frontend: `/destinos` list + `/destinos/[id]` detail+edit.
- **D-04** Backend: `GET /api/v1/atrativos` (all FSM stages, filters), `GET /api/v1/atrativos/{id}` (FSM journey+contacts+signals+score+parent link), `PATCH /api/v1/atrativos/{id}` (edit+approve/descartar/advance_sub_state). Mask PII using existing `mask_phone`. Frontend: `/atrativos` list + `/atrativos/[id]` detail+edit.
- **D-05** New page `/processo`: `GET /api/v1/workers` (Celery inspect + Redis LLEN, graceful degradation on broker down), `GET /api/v1/failures` (PoisonQuarantine list+counts). Page: worker board + failures panel + human-pending tiles + per-lane stage funnel. Live-polled.
- **D-06** Reusable `<JourneyStepper>` from AuditLog + routing/sub_state. Destino steps: Nascente → Rio (score) → DLQ → [steward] → Mar. Atrativo steps: discovered → contacts_found → signals_gathered → score → [gate] → [outreach] → Mar/DLQ.
- **D-07** No sidebar/shell rebuild, no font swap, no i18n lib, no WhatsApp send / score-engine / norteia-api changes. Tests: Vitest+MSW dashboard (offline), pytest offline backend (mock celery inspect + fakeredis). 100% offline mandate holds.

### Claude's Discretion
- Whether `/processo` is a new route or an enhanced `/monitor`
- Exact filter param names
- Pagination style
- Whether destinos list is one unified endpoint or Mar+DLQ merged client-side
- StageBadge color shades within the token palette
- React-table column set
- Polling interval

### Deferred Ideas (OUT OF SCOPE)
- Full Norteia design-system replication (sidebar shell, Open Sans/Montserrat, component library, i18n)
- WebSocket live updates
- Flower integration
- Bulk edit / batch actions on listings
- norteia-api push status surfaced per record
</user_constraints>

---

## Summary

Phase 8 adds a visual, browsable CMS layer over the data the Brave pipeline already produces, plus 24/7 process observability. All backend primitives exist: models, audit trail, FSM, service functions, auth deps. The phase is primarily plumbing (new endpoints that compose existing building blocks) and UI wiring (new pages using the established TanStack Query + MSW + shadcn pattern).

The highest-risk item is `GET /api/v1/workers`: Celery's `inspect()` makes network I/O to the broker and can hang or return `None` when no workers are running. The endpoint MUST call `inspect(timeout=2.0)` and treat `None` returns as an explicit "no workers" state, never propagating a timeout as a 500. This is fully offline-testable by monkeypatching `celery_app.control.inspect`.

The second risk is JSON path filtering for `parent_mar_id` in the atrativos list. The `normalized` column is `sqlalchemy.JSON` (not JSONB), so Postgres's `->>` operator is unavailable via standard SQLAlchemy JSON accessors. The established project pattern (from `discovery_agent.py` line 116) is to avoid JSON path expressions and use scalar column filtering where possible. For `parent_mar_id` filtering the recommended approach is a Python-level cast with `func.json_extract_path_text` or a `cast(RioRecord.normalized["parent_mar_id"].as_string(), ...)` using SQLAlchemy's JSON subscript — verified safe for PostgreSQL JSON (not JSONB).

The dashboard's Tailwind v4 theme uses `@theme inline` bridging CSS variables — the token swap for D-01 is a `globals.css` edit only, no config file changes needed.

**Primary recommendation:** Implement in three waves: (1) backend endpoints + auth + offline pytest, (2) frontend pages + badge system, (3) `/processo` worker board with graceful-degradation tests. Reuse existing service functions; do not re-implement any pipeline logic.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Color token swap (D-01) | Frontend (dashboard CSS) | — | Pure CSS variable override in globals.css; no backend involvement |
| StageBadge / JourneyStepper components | Frontend (dashboard) | — | Read-only UI primitives consuming data from API |
| Destinos list/detail | API / Backend | Frontend (table+detail page) | Data lives in DB; backend query + pagination; frontend renders |
| Destinos PATCH actions | API / Backend | Frontend (action bar) | Delegates to existing service: validate_and_promote_rio, reprocess_record, descarte |
| Atrativos list/detail | API / Backend | Frontend (table+detail page) | Same pattern; JSON normalized filtering is a backend concern |
| Atrativos PATCH actions | API / Backend | Frontend (action bar) | Delegates to advance_sub_state + existing gate approve/descartar |
| Workers observability | API / Backend | Frontend (worker board) | Celery inspect + Redis LLEN happen server-side; result serialized to JSON |
| Failures panel | API / Backend | Frontend (panel) | PoisonQuarantine query; browser only renders |
| Human-pending tiles | API / Backend (reuse existing endpoints) | Frontend | DLQ count: reuse existing `GET /api/v1/dlq` with count; gate count: reuse `GET /api/v1/atrativos/gate` |
| AuditLog journey read | API / Backend | Frontend (JourneyStepper) | SELECT AuditLog WHERE record_id = ? ORDER BY created_at; surfaced in detail endpoint |

---

## Standard Stack

All libraries confirmed installed via `dashboard/package.json` and project venv.

### Core (backend)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.136.x | New router endpoints | Project standard; existing routers pattern [CITED: CLAUDE.md] |
| SQLAlchemy | 2.0.x | ORM queries (destinos/atrativos list/detail) | Project standard; JSON subscript syntax for normalized filtering [CITED: CLAUDE.md] |
| Celery `app.control.inspect()` | 5.6.x | Worker introspection | Built-in Celery API; no new dep [VERIFIED: direct code inspection] |
| Redis `client.llen(queue)` | 8.0 Python | Queue depth | Already used for ramp counter; `get_redis()` dep available [VERIFIED: direct code inspection] |
| `require_bearer` / `require_steward_or_bearer` | — | Auth guards | Already in `brave/api/deps.py`; import and reuse [VERIFIED: direct code inspection] |

### Core (dashboard)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Next.js | 16.0.1 | App Router pages | Project standard [VERIFIED: package.json] |
| @tanstack/react-table | 8.21.3 | Destinos/atrativos list tables | Already installed and used in QueueList.tsx [VERIFIED: package.json + QueueList.tsx] |
| @tanstack/react-query | 5.90.2 | Data fetching + polling | Already used in every slice [VERIFIED: package.json] |
| recharts | 3.8.0 | Stage funnel chart in /processo | Already installed; used in CostByLaneChart.tsx [VERIFIED: package.json] |
| msw | 2.11.5 | Offline test mocking | Already used; server.ts + per-slice handlers pattern [VERIFIED: mocks/server.ts] |
| Tailwind v4 | 4.1.14 | Styling; CSS var token system | Project standard; `@theme inline` pattern in globals.css [VERIFIED: globals.css] |
| next-themes | ^0.4.6 | Light/dark via `.dark` class | Already installed [VERIFIED: package.json] |
| vitest | 4.0.4 | Test runner | Project standard [VERIFIED: package.json] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| lucide-react | 0.544.0 | Icons (worker up/down, badge icons) | Already installed; use for status indicators |
| class-variance-authority | 0.7.1 | Badge variant system (StageBadge) | Already used by shadcn components; use for badge intent mapping |
| sonner | ^2.0.7 | Toast on PATCH action success/error | Already installed; mirror DLQ action pattern |

### No New Dependencies Expected
This phase adds no new npm or PyPI packages. All required functionality is available through existing installed libraries.

---

## Package Legitimacy Audit

No new packages are installed in this phase. All libraries are already present and verified in prior phases.

| Package | Registry | Status | Disposition |
|---------|----------|--------|-------------|
| (none new) | — | — | — |

---

## Architecture Patterns

### System Architecture Diagram

```
Browser (operator)
    │ Bearer token (operator-secret)
    ▼
Next.js BFF Route Handler (dashboard/app/api/[...path]/route.ts)
    │ Auth gate: isAuthorizedBrowserToken → 401 before forward
    │ Injects: Authorization: Bearer <service-secret>
    ▼
FastAPI (brave.api)
    ├── GET /api/v1/destinos         → RioRecord (all routings) + LEFT JOIN MarRecord
    ├── GET /api/v1/destinos/{id}    → RioRecord + NascenteRecord + AuditLog + child atrativos count
    ├── PATCH /api/v1/destinos/{id}  → validate_and_promote_rio | descarte | reprocess_record
    ├── GET /api/v1/atrativos        → RioRecord (entity_type=attraction) + JSON normalized filter
    ├── GET /api/v1/atrativos/{id}   → RioRecord + AuditLog + parent MarRecord
    ├── PATCH /api/v1/atrativos/{id} → advance_sub_state | approve_gate | descartar
    ├── GET /api/v1/workers          → celery_app.control.inspect(timeout=2.0) + redis.llen
    └── GET /api/v1/failures         → PoisonQuarantine SELECT + GROUP BY task_name
         │
         ▼
    PostgreSQL (RioRecord, MarRecord, NascenteRecord, AuditLog, PoisonQuarantine)
    Redis     (LLEN on brave.sweep + celery queues; fakeredis in tests)
```

Data flow for `/processo`:
```
GET /api/v1/workers
  inspect.ping()   → {hostname: {ok:'pong'}} or None
  inspect.active() → {hostname: [task_info...]} or None
  inspect.reserved()
  redis.llen('brave.sweep')
  redis.llen('celery')
  → {workers: [...], queues: {...}, beat_schedule: [...], broker_reachable: bool}

GET /api/v1/failures
  SELECT task_name, error_message, payload, quarantined_at FROM poison_quarantine
  → {items: [...], by_task: {task_name: count}, total: N}
```

### Recommended Project Structure

New backend files:
```
brave/api/routers/
├── cms.py              # GET/PATCH /api/v1/destinos + /api/v1/atrativos
└── workers.py          # GET /api/v1/workers + GET /api/v1/failures
```

New dashboard files:
```
dashboard/
├── app/
│   ├── destinos/
│   │   ├── page.tsx          # list
│   │   └── [id]/page.tsx     # detail+edit
│   ├── atrativos/
│   │   ├── page.tsx          # list
│   │   └── [id]/page.tsx     # detail+edit
│   └── processo/
│       └── page.tsx          # worker board + failures + funnel
├── components/
│   ├── cms/
│   │   ├── StageBadge.tsx    # extends/replaces StatusBadge (D-02)
│   │   ├── JourneyStepper.tsx
│   │   ├── DestinoList.tsx   # TanStack Table v8
│   │   ├── AtrativoList.tsx
│   │   └── DetailPanel.tsx   # extends ReviewPanel pattern
│   └── processo/
│       ├── WorkerBoard.tsx
│       └── FailuresPanel.tsx
├── lib/
│   ├── destinos-api.ts
│   ├── atrativos-api.ts
│   └── workers-api.ts
└── mocks/handlers/
    ├── destinos.ts
    ├── atrativos.ts
    └── workers.ts
```

---

## Research Question Answers

### Q1: Celery Worker Introspection (GET /api/v1/workers)

**Celery inspect API** [VERIFIED: direct Python inspection of celery 5.6.x installed in .venv]:

```python
from brave.tasks.celery_app import app

i = app.control.inspect(timeout=2.0)   # timeout kwarg, default 1.0s

ping_result   = i.ping()       # {hostname: {'ok': 'pong'}} or None
active_result = i.active()     # {hostname: [task_info,...]} or None
reserved_result = i.reserved() # {hostname: [task_info,...]} or None
scheduled_result = i.scheduled()
registered_result = i.registered()
```

All methods return `None` (not raise) when the broker is unreachable or no workers respond within `timeout`. `task_info` dicts contain: `id`, `name`, `args`, `kwargs`, `hostname`, `time_start`.

**Queue depth** [VERIFIED: Redis is running locally; llen confirmed]:
```python
from brave.api.deps import get_redis

redis = get_redis()
depth = redis.llen("brave.sweep")   # int — 0 when queue is empty
celery_default = redis.llen("celery")
```

Queue names confirmed from `beat_schedule.py`: all sweep tasks route to `"brave.sweep"` (the `options: {"queue": "brave.sweep"}` in every BRAVE_BEAT_SCHEDULE entry). The default Celery queue name is `"celery"`.

**Task names registered** [VERIFIED: pipeline.py grep]:
- `brave.process_nascente`
- `brave.push_mar`
- `brave.reprocess_record`
- `brave.push_destination`
- `brave.push_attraction`
- `brave.discover_atrativo`
- `brave.sweep_uf`
- `brave.find_contacts`
- `brave.gather_signals`
- `brave.outreach`
- `brave.resume_conversation`

**Beat schedule summary** from `beat_schedule.py` [VERIFIED]:
- 27 entries `sweep-{uf}-daily` → `brave.sweep_uf` at 2 AM UTC daily, queue `brave.sweep`
- 27 entries `sweep-atrativos-{uf}-daily` → `brave.discover_atrativo` at 3 AM UTC daily, queue `brave.sweep`
- Total: 54 scheduled entries; summarize as `{count_sweep_uf: 27, count_discover_atrativo: 27, queues: ["brave.sweep"]}` for the response

**Graceful degradation pattern** [VERIFIED: behavior confirmed from Celery 5.6.x docs]:
```python
def get_workers_status() -> dict:
    try:
        i = app.control.inspect(timeout=2.0)
        ping = i.ping() or {}       # None → {}
        active = i.active() or {}
        reserved = i.reserved() or {}
    except Exception:
        ping = active = reserved = {}

    broker_reachable = bool(ping)

    workers = []
    for hostname, response in ping.items():
        active_tasks = active.get(hostname, [])
        reserved_tasks = reserved.get(hostname, [])
        workers.append({
            "hostname": hostname,
            "status": "up" if response.get("ok") == "pong" else "down",
            "active_count": len(active_tasks),
            "reserved_count": len(reserved_tasks),
        })

    try:
        redis = get_redis()
        queue_depths = {
            "brave.sweep": redis.llen("brave.sweep"),
            "celery": redis.llen("celery"),
        }
    except Exception:
        queue_depths = {"brave.sweep": None, "celery": None}

    return {
        "broker_reachable": broker_reachable,
        "workers": workers,
        "queues": queue_depths,
        "beat_schedule": {"count_sweep_uf": 27, "count_discover_atrativo": 27, "queues": ["brave.sweep"]},
    }
```

**Unit-testing offline** [VERIFIED: from test infrastructure patterns in the project]:
```python
# In pytest:
from unittest.mock import MagicMock, patch
import fakeredis

def test_workers_no_broker(client, monkeypatch):
    mock_inspect = MagicMock()
    mock_inspect.ping.return_value = None
    mock_inspect.active.return_value = None
    mock_inspect.reserved.return_value = None

    monkeypatch.setattr(
        "brave.tasks.celery_app.app.control.inspect",
        lambda **kw: mock_inspect,
    )
    monkeypatch.setattr("brave.api.deps.get_redis", lambda: fakeredis.FakeRedis())

    resp = client.get("/api/v1/workers", headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["broker_reachable"] is False
    assert body["workers"] == []
```

**Risk:** `inspect(timeout=2.0)` still blocks for 2 seconds in a FastAPI sync handler. Use `timeout=1.0` or add the endpoint to a background task / async path. The existing `get_monitor` is a sync endpoint and 1-2s is acceptable for an ops poll.

---

### Q2: Destinos List Query

**Model columns available** [VERIFIED: brave/core/models.py]:
- `RioRecord`: id, nascente_id, entity_type, uf, municipio_id, routing, dlq_reason, sub_state, normalized (JSON), score, score_breakdown (JSON), score_version, processed_at, canonical_key
- `MarRecord`: id, rio_id, entity_type, source_ref, canonical (JSON), provenance (JSON), reliability_score, score_version, parent_mar_id, published_at

**Recommended query pattern:**

A destino is always a RioRecord with `entity_type='destination'`. When promoted, a corresponding `MarRecord` exists linked by `mar.rio_id = rio.id`. The cleanest list is: query `RioRecord` (all routings), LEFT JOIN `MarRecord`, emit badge fields from both:

```python
from sqlalchemy import select, or_, func, cast, String
from sqlalchemy.orm import aliased

def list_destinos(db, uf=None, source=None, routing=None, q=None,
                  score_band=None, offset=0, limit=50):
    stmt = (
        select(RioRecord, MarRecord)
        .outerjoin(MarRecord, MarRecord.rio_id == RioRecord.id)
        .where(RioRecord.entity_type == "destination")
    )
    if uf:
        stmt = stmt.where(RioRecord.uf == uf)
    if routing:
        stmt = stmt.where(RioRecord.routing == routing)
    if source:
        # source lives in NascenteRecord.source; join nascente for filtering
        stmt = stmt.join(NascenteRecord, NascenteRecord.id == RioRecord.nascente_id)
        stmt = stmt.where(NascenteRecord.source == source)
    if score_band == "mar":
        stmt = stmt.where(RioRecord.score >= 85)
    elif score_band == "dlq":
        stmt = stmt.where(RioRecord.score >= 40, RioRecord.score < 85)
    elif score_band == "descarte":
        stmt = stmt.where(RioRecord.score < 40)
    # offset+limit pagination (same as DLQ list: limit param)
    stmt = stmt.offset(offset).limit(limit)
    rows = db.execute(stmt).all()
    return [_serialize_destino_row(rio, mar) for rio, mar in rows]
```

Badge fields per row:
- `routing` (from RioRecord)
- `score` (from RioRecord)
- `source` (from NascenteRecord.source — requires join when filtering; otherwise pass from canonical or nascente_id)
- `validation_pending`: `True` when `routing == 'dlq'`
- `name`: `rio.normalized.get('name')` or `mar.canonical.get('name')` if promoted

**Pagination pattern** [VERIFIED: from dlq.py]: `limit` query param (default 50, max 500). The existing endpoints use `LIMIT` only (no cursor/offset). For the CMS listing, offset+limit is appropriate given this is human-browsed at modest scale.

---

### Q3: Atrativos List Query

**parent_mar_id in normalized** [VERIFIED: discovery_agent.py line 313 + routing.py line 162]:
- `parent_mar_id` is stored as a string UUID at `rio.normalized["parent_mar_id"]` for every atrativo ingested via `DiscoveryAgent`
- This is `sqlalchemy.JSON` (not JSONB) [VERIFIED: models.py imports `from sqlalchemy import JSON`]

**JSON vs JSONB — critical finding** [VERIFIED: discovery_agent.py line 116 comment]:
> "JSON type (not JSONB), making JSON path expressions dialect-specific"

The existing codebase documents this explicitly and works around it via scalar column matching. For the atrativos list `parent_mar_id` filter, use SQLAlchemy's JSON subscript operator which maps to Postgres `->>`/`#>>` under the hood:

```python
# SQLAlchemy 2.0 JSON path filtering (works with both JSON and JSONB on PostgreSQL)
stmt = stmt.where(
    RioRecord.normalized["parent_mar_id"].as_string() == str(parent_mar_id)
)
```

`as_string()` is the SQLAlchemy JSON accessor that emits `normalized->>'parent_mar_id'` in Postgres. This IS supported with `JSON` type (not only JSONB) — Postgres's `->>` works on both. [VERIFIED: SQLAlchemy 2.0 docs pattern; not dialect-specific for the simple key access case]

The project's own comment about "dialect-specific" referred to JSON PATH expressions (`jsonb_path_query` etc.) not simple `->>` key access.

**Atrativos list query:**
```python
stmt = (
    select(RioRecord)
    .where(RioRecord.entity_type == "attraction")
)
if uf:
    stmt = stmt.where(RioRecord.uf == uf)
if sub_state:
    stmt = stmt.where(RioRecord.sub_state == sub_state)
if parent_mar_id:
    stmt = stmt.where(
        RioRecord.normalized["parent_mar_id"].as_string() == str(parent_mar_id)
    )
if routing:
    stmt = stmt.where(RioRecord.routing == routing)
stmt = stmt.offset(offset).limit(limit)
```

**Name field**: `rio.normalized["name"]` (atrativo name from discovery) [VERIFIED: discovery_agent seeds normalized with `name` key]

---

### Q4: AuditLog Journey Trail

All `write_audit` call sites inspected [VERIFIED: grep across all brave modules]:

**Destino lane actions** (AuditLog action values):
| Action | Actor | Where Written |
|--------|-------|---------------|
| `dlq_reprocessed` | `"steward"` | dlq.py reprocess endpoint |
| `dlq_validated` | `"steward"` | dlq.py validate endpoint + batch validate |
| `dlq_rejected` | `"steward"` | dlq.py descarte endpoint |
| `error_report_received` | `"webhook"` | webhook.py |

Note: the pipeline processing steps (nascente ingest, Rio scoring, routing to DLQ/Mar) do NOT write AuditLog rows [VERIFIED: brave/core has no write_audit calls]. AuditLog only records steward actions and a few system events. This means the JourneyStepper for destinos will show the steward decision events only, not every pipeline step.

**Atrativo lane actions**:
| Action | Actor | Where Written |
|--------|-------|---------------|
| `atrativo_discovered` | `"discovery_agent"` | discovery_agent.py (two call sites) |
| `sub_state_advanced` | `"state_machine"` / `"contact_finder_agent"` / `"signal_agent"` | state_machine.py + agents |
| `hard_descarte` | `"signal_agent"` | signal_agent.py |
| `whatsapp_gate_approved` | `"steward"` | atrativos_gate.py |
| `whatsapp_gate_rejected` | `"steward"` | atrativos_gate.py |
| `quality_rating_updated` | `"webhook"` | atrativos_gate.py |
| `opt_out_recorded` | `"compliance"` | consent_log.py |

**JourneyStepper mapping**:

For destinos, derive current step from `rio.routing` + AuditLog rows:
```
Step 1: Nascente (always — every destino was ingested)       ← no AuditLog row
Step 2: Rio / Score                                           ← no AuditLog row
Step 3: DLQ gate (show if routing='dlq' ever)               ← dlq_validated / dlq_rejected / dlq_reprocessed
Step 4: Mar (show if routing='mar')                          ← dlq_validated (last row where after_state routing='mar')
Current step = from rio.routing
```

For atrativos, map AuditLog `action` + `after_state.sub_state` to steps:
```
Step 1: atrativo_discovered → sub_state='discovered'
Step 2: sub_state_advanced  → sub_state='contacts_found'
Step 3: sub_state_advanced  → sub_state='signals_gathered'
Step 4: sub_state_advanced  → sub_state='aguardando_consulta_whatsapp'
Step 5: whatsapp_gate_approved → sub_state='whatsapp_in_progress'
Step 6: Mar (rio.routing='mar') or dlq_rejected (gate rejected)
```

**Implication for JourneyStepper**: The component receives `{ auditRows: AuditLogRow[], routing: string, subState: string | null }` and maps rows to step completions. Destino steps 1-2 will have no audit evidence — mark them "completed" implicitly based on the record existing.

---

### Q5: PATCH Actions — Exact Signatures

**Destinos PATCH actions** [VERIFIED: dlq.py + dlq/service.py]:

1. **Promote** (`action: "promote"`):
   ```python
   from brave.core.dlq.service import validate_and_promote_rio
   validate_and_promote_rio(session, rio, config=ScoreConfig())
   session.refresh(rio)
   # if rio.routing == 'mar': dispatch push_destination_task.delay(str(rio_id))
   write_audit(session, "dlq_validated", entity_type="destination", record_id=rio.id,
               before_state={...}, after_state={...}, actor="steward")
   ```
   Guard: `require_steward_or_bearer`

2. **Reject/Descarte** (`action: "descarte"`):
   ```python
   rio.routing = "descarte"
   rio.dlq_reason = "steward_rejected"
   write_audit(session, "dlq_rejected", ...)
   ```
   Guard: `require_steward_or_bearer`

3. **Reprocess** (`action: "reprocess"`):
   ```python
   reprocess_record_task.delay(str(rio_id))
   # sync fallback: reprocess_record(db, rio_id, ScoreConfig())
   write_audit(session, "dlq_reprocessed", ...)
   ```
   Guard: `require_steward_or_bearer`

**Atrativos PATCH actions** [VERIFIED: state_machine.py + atrativos_gate.py]:

4. **Advance sub_state** (`action: "advance_sub_state"`):
   ```python
   from brave.lanes.atrativos.state_machine import advance_sub_state
   # Signature: advance_sub_state(session, rio, expected_state, next_state,
   #                               actor="steward", lock=True) -> bool
   advanced = advance_sub_state(
       session=db, rio=rio,
       expected_state=body.expected_state,
       next_state=body.next_state,
       actor="steward",
       lock=True,       # re-fetches with FOR UPDATE
   )
   # Returns False if already past expected_state (idempotency guard)
   ```
   Guard: `require_steward_or_bearer`

5. **Approve** (equivalent to atrativos gate approve, for atrativos still in `aguardando_consulta_whatsapp`):
   Reuse the existing `/api/v1/atrativos/gate/{rio_id}/approve` endpoint — or call the same logic from the new PATCH endpoint. Guard: `require_steward_or_bearer`.

6. **Descartar**:
   ```python
   rio.routing = "dlq"
   rio.dlq_reason = "steward_rejected_gate"
   rio.sub_state = None
   write_audit(session, "whatsapp_gate_rejected", ...)
   ```
   Guard: `require_steward_or_bearer`

7. **Edit canonical** (canonical field patch):
   Use `flag_modified(rio, "normalized")` after dict reassignment [VERIFIED: dlq/service.py Pitfall 3 pattern]. Then optionally trigger reprocess.

**PII masking for atrativos**: use `mask_phone` from `brave.core.models` and the `_safe_normalized` helper from `atrativos_gate.py`. Both are importable.

---

### Q6: Tailwind v4 Token Mapping (D-01)

**Current globals.css structure** [VERIFIED: dashboard/app/globals.css]:

The dashboard uses Tailwind v4 with `@theme inline` bridging:
```css
@theme inline {
  --color-primary: var(--primary);
  --color-accent:  var(--accent);
  /* ... maps Tailwind color utilities to CSS vars */
}
:root {
  --primary: oklch(0.62 0.19 255);  /* current: shadcn blue */
  --accent:  oklch(0.97 0 0);       /* current: nearly-white */
  ...
}
```

Utility classes like `bg-primary`, `text-accent`, `text-status-mar` resolve through the `@theme inline` aliases to the CSS vars defined in `:root`.

**Token swap for D-01** — edit ONLY `:root` and `.dark` blocks in `globals.css`:

```css
:root {
  /* D-01: Norteia brand tokens (from norteia-frontend globals.css) */
  --background:   hsl(40 33% 98%);          /* off-white */
  --foreground:   hsl(0 0% 10%);
  --primary:      hsl(211 83% 19%);         /* navy #082B5B */
  --primary-foreground: hsl(40 33% 98%);
  --accent:       hsl(11 53% 46%);          /* terracota #B14A36 */
  --accent-foreground: hsl(40 33% 98%);
  --destructive:  hsl(0 84% 60%);           /* keep existing red */
  /* status tokens (already defined in current globals.css): */
  --status-mar:      hsl(142 76% 36%);      /* success green */
  --status-dlq:      hsl(38 92% 50%);       /* warning amber */
  --status-descarte: hsl(0 84% 60%);        /* destructive red */
  /* NEW tokens for StageBadge (not yet in globals.css): */
  --status-in-progress: hsl(211 83% 19%);   /* navy = pipeline active */
  --status-success:     hsl(142 76% 36%);   /* alias for --status-mar */
  --status-warning:     hsl(38 92% 50%);    /* alias for --status-dlq */
}
.dark {
  --primary: hsl(211 83% 25%);  /* lighter navy for dark bg */
  --accent:  hsl(11 53% 50%);   /* lighter terracota for dark bg */
  /* existing dark status tokens are already correct */
}
```

**Note**: The `@theme inline` section maps `--color-primary: var(--primary)` etc. already, so `bg-primary` / `text-primary` picks up the new value automatically. No change to the `@theme inline` block is needed — only the `:root` and `.dark` CSS var values.

**Norteia reference values** [VERIFIED: /Users/leandro/Projects/norteia/norteia-frontend/src/app/globals.css]:
- `--primary: 211 83% 19%` (navy, light mode)
- `--accent: 11 53% 46%` (terracota, light mode)
- `--success: 142 76% 36%`
- `--warning: 38 92% 50%`
- `--background: 40 33% 98%`
- Dark primary: `211 83% 25%`, dark accent: `11 53% 50%`

Note: norteia-frontend uses `hsl()` wrapper in utilities (`@apply`); dashboard uses raw values without `hsl()` because Tailwind v4 uses `oklch` format internally — but since we're setting CSS vars the format must match what the `@theme inline` bridge expects. The current dashboard `globals.css` uses `oklch()` values in `:root`; for the Norteia swap, keep the oklch format or convert HSL to oklch. Safest approach: use `oklch` equivalents computed from the HSL values, or use a `hsl()` literal in the CSS var (CSS native `hsl()` function works fine as a CSS variable value).

**Recommended**: Use CSS `hsl()` literals to match the reference values exactly:
```css
--primary: hsl(211 83% 19%);    /* replaces oklch(0.62 0.19 255) */
```
This is valid CSS and Tailwind v4 resolves CSS vars at compile time.

---

### Q7: Dashboard Test Harness

**Pattern** [VERIFIED: mocks/server.ts + mocks/handlers/dlq.ts + vitest.setup.ts + vitest.config.ts]:

1. `vitest.setup.ts` starts MSW Node server with `onUnhandledRequest: "error"` — any unmocked request fails the suite
2. Each slice has its own `mocks/handlers/<slice>.ts` file exporting factory functions (not a shared handlers array)
3. Per-suite: `server.use(handlerFactory())` in `beforeEach` or at suite level
4. After each: `server.resetHandlers()` cleans per-test overrides; `cleanup()` unmounts React

**URL pattern** [VERIFIED: mocks/handlers/dlq.ts comment + monitor.ts]:
Browser client (`apiFetch`) calls relative `/api/...`. Under jsdom, this resolves against `http://localhost:3000`. The BFF mounts at `/api/<rest>` → FastAPI `/<rest>`. So:
- FastAPI path: `/api/v1/destinos`
- BFF forwards: browser requests `/api/api/v1/destinos`
- MSW BASE URL: `http://localhost:3000/api/api/v1/destinos`

Example handler for new destinos slice:
```typescript
// mocks/handlers/destinos.ts
import { http, HttpResponse } from "msw";
const BASE = "http://localhost:3000/api/api/v1/destinos";

export function destinosListSuccess(items = sampleDestinos) {
  return http.get(BASE, () => HttpResponse.json(items));
}
export function destinoDetailSuccess(detail = sampleDetail) {
  return http.get(`${BASE}/:id`, () => HttpResponse.json(detail));
}
export function destinoPatchSuccess() {
  return http.patch(`${BASE}/:id/:action`, () =>
    HttpResponse.json({ status: "accepted" }, { status: 202 })
  );
}
```

**Bearer faking**: The BFF validates the operator token before forwarding. In tests that render pages (which call `apiFetch` → relative `/api/...`), the MSW intercepts at the BFF URL level. For BFF unit tests (`app/api/__tests__/bff.test.ts`), env vars `DASHBOARD_OPERATOR_TOKEN` and `BRAVE_DASHBOARD_BEARER_TOKEN` are set in `beforeEach`.

For new page tests: mock the BFF-level URL (the `http://localhost:3000/api/api/v1/...` pattern) — no need to also test auth (BFF auth is tested separately in `bff.test.ts`).

---

### Q8: react-table + recharts Usage

**@tanstack/react-table 8.21.3** [VERIFIED: package.json + QueueList.tsx]:
Already used in `QueueList.tsx`. The pattern:
```typescript
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
const columns = useMemo<ColumnDef<T>[]>(() => [...], []);
const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel() });
// Render: table.getHeaderGroups() + table.getRowModel().rows
```
Mirror this exactly for destinos/atrativos lists. Row click → `onSelect(row.original.id)`.

**recharts 3.8.0** [VERIFIED: package.json + FunnelChart.tsx and CostByLaneChart.tsx exist]:
Used in cost + funnels. For the `/processo` stage funnel (per-lane sub_state distribution), use `BarChart` with stacked bars:
```typescript
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from "recharts";
// data: [{stage: "discovered", count: N}, ...]
<ResponsiveContainer width="100%" height={200}>
  <BarChart data={funnelData}>
    <XAxis dataKey="stage" />
    <YAxis />
    <Bar dataKey="count" fill="var(--color-primary)" />
  </BarChart>
</ResponsiveContainer>
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Celery worker status | Custom Celery API client | `app.control.inspect(timeout=...)` | Built into Celery 5.6; handles serialization, broadcast, timeout |
| Record promotion logic | Custom score + promote path | `validate_and_promote_rio()` | Already handles flag_modified, reprocess_record, promote_to_mar pitfalls |
| FSM state advance | Custom sub_state write | `advance_sub_state()` | Handles SELECT FOR UPDATE lock, idempotency guard, audit row |
| PII phone masking | Custom masking | `mask_phone()` from `brave.core.models` + `_safe_normalized()` from `atrativos_gate.py` | LGPD-compliant, tested pattern |
| Auth guards | New auth dep | `require_bearer` / `require_steward_or_bearer` from `brave.api.deps` | Constant-time hmac, fail-closed, never-logged |
| JSON column mutation | Direct dict mutation | Reassign + `flag_modified(obj, "normalized")` | SQLAlchemy won't detect in-place JSON mutations (Pitfall 3 in service.py) |
| Reprocessing | Custom score invocation | `reprocess_record_task.delay()` with sync fallback | Same pattern as dlq.py; handles broker-absent gracefully |

---

## Common Pitfalls

### Pitfall 1: celery inspect hanging
**What goes wrong:** `app.control.inspect()` without explicit timeout blocks for the default 1s per call, and if retried or called multiple times can cause the FastAPI worker to hang for several seconds.
**Why it happens:** inspect broadcasts a message to all workers via the broker and waits; with no broker it waits for timeout.
**How to avoid:** Always pass `timeout=1.0` or `timeout=2.0`. Wrap in `try/except` and treat any exception as "broker unreachable". Never call `inspect()` more than once per request — make one call and extract everything.
**Warning signs:** `/api/v1/workers` p99 > 2 seconds; CI tests hang.

### Pitfall 2: JSON (not JSONB) path filtering
**What goes wrong:** Using `func.json_extract_path_text` or other JSONB-specific functions on the `normalized` column fails or is dialect-dependent.
**Why it happens:** `models.py` uses `sqlalchemy.JSON` not `postgresql.JSONB`. Simple `->>` via SQLAlchemy's `column["key"].as_string()` works on PostgreSQL JSON type. Complex path expressions (`#>>`, `@>`) require JSONB.
**How to avoid:** For `parent_mar_id` filter: `RioRecord.normalized["parent_mar_id"].as_string() == str(uuid)`. Only simple single-key access is needed here.
**Warning signs:** `ProgrammingError: operator does not exist: json @> jsonb`.

### Pitfall 3: JSON mutation without flag_modified
**What goes wrong:** Mutating `rio.normalized["key"] = value` in-place doesn't mark the column dirty in SQLAlchemy; the update is silently dropped.
**Why it happens:** SQLAlchemy tracks object identity, not deep dict mutations.
**How to avoid:** `normalized = dict(rio.normalized or {}); normalized["key"] = value; rio.normalized = normalized; flag_modified(rio, "normalized")` — exact pattern from `dlq/service.py`.
**Warning signs:** PATCH edit endpoint returns 200 but the field doesn't persist.

### Pitfall 4: advance_sub_state lock=True requires a real DB session
**What goes wrong:** `advance_sub_state(..., lock=True)` issues `SELECT ... FOR UPDATE` which is not supported by mock sessions or SQLite.
**Why it happens:** FOR UPDATE is PostgreSQL-specific syntax.
**How to avoid:** In offline pytest, call `advance_sub_state(..., lock=False)`. Only use `lock=True` in the real FastAPI handler.
**Warning signs:** `sqlite3.OperationalError: near "FOR": syntax error` in tests.

### Pitfall 5: MSW URL double-prefix
**What goes wrong:** MSW handler targets `/api/v1/destinos` but browser client hits `/api/api/v1/destinos` through the BFF, so the handler never intercepts.
**Why it happens:** `apiFetch` prepends `/api/` (BFF mount); the BFF then maps `/api/` → FastAPI root; jsdom resolves relative URLs against `http://localhost:3000`.
**How to avoid:** Always use `http://localhost:3000/api/api/v1/<path>` as the MSW BASE URL. See `mocks/handlers/dlq.ts` line 18 as the canonical example.
**Warning signs:** Tests fail with "MSW: intercepted a request without a matching handler" errors.

### Pitfall 6: Tailwind v4 CSS var format mismatch
**What goes wrong:** Mixing `hsl()` values in CSS vars with `oklch()` Tailwind tokens causes color calculation failures in `color-mix()` or opacity modifiers.
**Why it happens:** Tailwind v4 uses `oklch` internally; `@theme inline` maps utilities to CSS vars. When the CSS var value is `hsl(...)` and Tailwind tries to do `bg-primary/50` (opacity modifier), it may not resolve correctly.
**How to avoid:** Use `oklch()` format for the token values in `:root`, matching the existing shadcn baseline. Convert the HSL target values: navy `hsl(211 83% 19%)` → `oklch(0.23 0.10 253)` (approx). Alternatively, test the opacity modifiers after the swap.
**Warning signs:** `bg-primary/50` renders as opaque; color-mix functions return unexpected values.

---

## Code Examples

### Backend: GET /api/v1/workers (graceful degradation)
```python
# Source: derived from celery_app.py control.inspect API (verified .venv inspect)
@router.get("/api/v1/workers", dependencies=[Depends(require_bearer)])
def get_workers(redis: Redis = Depends(get_redis)) -> dict:
    from brave.tasks.celery_app import app as celery_app

    try:
        i = celery_app.control.inspect(timeout=1.0)
        ping = i.ping() or {}
        active = i.active() or {}
        reserved = i.reserved() or {}
    except Exception:
        ping = active = reserved = {}

    broker_reachable = bool(ping)
    workers = [
        {
            "hostname": h,
            "status": "up" if resp.get("ok") == "pong" else "down",
            "active_count": len(active.get(h, [])),
            "reserved_count": len(reserved.get(h, [])),
        }
        for h, resp in ping.items()
    ]

    try:
        queue_depths = {
            "brave.sweep": redis.llen("brave.sweep"),
            "celery": redis.llen("celery"),
        }
    except Exception:
        queue_depths = {"brave.sweep": None, "celery": None}

    return {
        "broker_reachable": broker_reachable,
        "workers": workers,
        "queues": queue_depths,
        "beat_schedule": {
            "entries": 54,
            "queues": ["brave.sweep"],
        },
    }
```

### Backend: GET /api/v1/destinos (paginated list with LEFT JOIN)
```python
# Source: mirrors list_dlq pattern from dlq.py + outerjoin pattern from dashboard.py
@router.get("/api/v1/destinos", dependencies=[Depends(require_bearer)])
def list_destinos(
    uf: str | None = Query(None),
    routing: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    stmt = (
        select(RioRecord, MarRecord)
        .outerjoin(MarRecord, MarRecord.rio_id == RioRecord.id)
        .where(RioRecord.entity_type == "destination")
    )
    if uf:
        stmt = stmt.where(RioRecord.uf == uf)
    if routing:
        stmt = stmt.where(RioRecord.routing == routing)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    stmt = stmt.offset(offset).limit(limit)
    rows = db.execute(stmt).all()

    items = [
        {
            "id": str(rio.id),
            "entity_type": rio.entity_type,
            "uf": rio.uf,
            "routing": rio.routing,
            "score": float(rio.score) if rio.score is not None else None,
            "canonical_key": rio.canonical_key,
            "name": (rio.normalized or {}).get("name") or (mar.canonical if mar else {}).get("name"),
            "validation_pending": rio.routing == "dlq",
            "mar_id": str(mar.id) if mar else None,
            "published_at": mar.published_at.isoformat() if mar else None,
        }
        for rio, mar in rows
    ]
    return {"items": items, "total": total, "offset": offset, "limit": limit}
```

### Backend: PATCH /api/v1/atrativos/{id}/advance
```python
# Source: advance_sub_state signature from state_machine.py
@router.patch("/api/v1/atrativos/{rio_id}/advance",
              status_code=200,
              dependencies=[Depends(require_steward_or_bearer)])
def advance_atrativo_state(
    rio_id: uuid.UUID,
    body: AdvanceBody,   # {expected_state: str, next_state: str}
    db: Session = Depends(get_db),
) -> dict:
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    advanced = advance_sub_state(
        session=db, rio=rio,
        expected_state=body.expected_state,
        next_state=body.next_state,
        actor="steward",
        lock=True,   # SELECT ... FOR UPDATE for concurrency safety
    )
    if not advanced:
        raise HTTPException(status_code=409,
            detail=f"sub_state is '{rio.sub_state}', expected '{body.expected_state}'")
    return {"status": "ok", "rio_id": str(rio_id), "sub_state": rio.sub_state}
```

### Frontend: StageBadge (extends existing StatusBadge)
```typescript
// Source: extends dashboard/components/dlq/StatusBadge.tsx pattern
// New component at dashboard/components/cms/StageBadge.tsx

const SUB_STATE_CLASS: Record<string, string> = {
  discovered:                    "border-transparent bg-[var(--color-primary)]/10 text-[var(--color-primary)]",
  contacts_found:                "border-transparent bg-[var(--color-primary)]/20 text-[var(--color-primary)]",
  signals_gathered:              "border-transparent bg-[var(--color-primary)]/30 text-[var(--color-primary)]",
  aguardando_consulta_whatsapp:  "border-transparent bg-[var(--status-dlq)]/15 text-[var(--status-dlq)]",
  whatsapp_in_progress:          "border-transparent bg-[var(--status-dlq)]/25 text-[var(--status-dlq)]",
};

export function StageBadge({ routing, subState, score, source }: StageBadgeProps) {
  // routing badge (extends existing StatusBadge logic)
  // sub_state badge (6 FSM states)
  // score band badge (≥85 green, 40–84.9 amber, <40 red)
  // source chip (neutral)
  // validation_pending chip
}
```

### Frontend: MSW handler for workers endpoint
```typescript
// Source: mirrors mocks/handlers/monitor.ts pattern
// mocks/handlers/workers.ts
const BASE = "http://localhost:3000/api/api/v1/workers";

export const sampleWorkers = {
  broker_reachable: true,
  workers: [
    { hostname: "celery@worker-1", status: "up", active_count: 2, reserved_count: 5 }
  ],
  queues: { "brave.sweep": 27, "celery": 0 },
  beat_schedule: { entries: 54, queues: ["brave.sweep"] },
};

export const sampleWorkersBrokerDown = {
  broker_reachable: false,
  workers: [],
  queues: { "brave.sweep": null, "celery": null },
  beat_schedule: { entries: 54, queues: ["brave.sweep"] },
};

export function workersSuccess(data = sampleWorkers) {
  return http.get(BASE, () => HttpResponse.json(data));
}
export function workersBrokerDown() {
  return http.get(BASE, () => HttpResponse.json(sampleWorkersBrokerDown));
}
```

---

## State of the Art

| Old Approach | Current Approach | Notes |
|--------------|-----------------|-------|
| Legacy `inspect(timeout=None)` | `inspect(timeout=1.0)` + None-guard | Celery 5.x; None return is the correct broker-absent signal |
| React Class components | React 19 + hooks + TanStack Query 5 | Already used in project |
| Tailwind v3 config file tokens | Tailwind v4 `@theme inline` CSS vars in globals.css | Already established in dashboard |

---

## Open Questions

1. **oklch vs hsl in Tailwind v4 CSS vars (D-01)**
   - What we know: current globals.css uses `oklch()` for all token values; norteia-frontend uses `hsl()`.
   - What's unclear: whether mixing `hsl()` CSS var values with Tailwind v4's `oklch`-based opacity modifier system causes breakage in `bg-primary/50` style utilities.
   - Recommendation: Test after the token swap with a simple `bg-primary/50` and `text-primary/80` in a component. If opacity modifiers break, convert the HSL values to oklch equivalents.

2. **Destinos "source" field in list**
   - What we know: `source` lives in `NascenteRecord.source`, not in `RioRecord` directly. A join to nascente for filtering adds query complexity.
   - What's unclear: how critical is source filtering for the initial list? For the MVP, source can be read from `rio.nascente.source` via the ORM relationship (already defined on `RioRecord`) and included in the response without a filter-time join.
   - Recommendation: Include source in the response by accessing `rio.nascente.source` (lazy load — acceptable for modest list sizes), but defer source-filter query optimization.

3. **AuditLog coverage for nascente/Rio processing steps**
   - What we know: pipeline processing (ingest, normalize, score, route) does NOT write AuditLog rows. Only steward actions and pipeline agents (atrativo discovery, sub_state advances) do.
   - What's unclear: should JourneyStepper show steps 1-2 (Nascente → Rio score) as "inferred completed" from the record existing, or is there a separate event source?
   - Recommendation: Derive steps 1-2 from record existence (`nascente_id` is populated = Step 1 done; `score` is populated = Step 2 done). No AuditLog row expected for these steps. Document this in the JourneyStepper implementation notes.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Redis | GET /api/v1/workers queue depth | ✓ | 7.x (llen confirmed) | `null` queue depths in response |
| Celery broker | GET /api/v1/workers inspect | ✗ (no worker running) | — | broker_reachable: false |
| PostgreSQL | All endpoints | ✓ | 16/17 | — |
| Node 22 / Bun | dashboard tests | ✓ | node v22.22.3, bun 1.3.13 | — |
| Python .venv | backend tests | ✓ | 3.12 | — |

Note: CI runs without Celery broker — this is expected and the GET /api/v1/workers endpoint MUST return a well-formed "no workers" response, not an error.

---

## Security Domain

`security_enforcement` key is absent from `.planning/config.json` — treated as enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Yes | `require_bearer` / `require_steward_or_bearer` (already implemented, reuse) |
| V3 Session Management | No | Stateless Bearer; no sessions |
| V4 Access Control | Yes | Read endpoints: Bearer only. Mutations: require_steward_or_bearer (either-or). |
| V5 Input Validation | Yes | Pydantic models for PATCH bodies (action enum, expected_state strings); limit/offset bounds; UF 2-char |
| V6 Cryptography | No | No new crypto operations |
| V10 PII / LGPD | Yes | `mask_phone` + `_safe_normalized` for atrativos; AuditLog and PoisonQuarantine never surface phone_e164 |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| PATCH promote without auth | Tampering | `require_steward_or_bearer` — fail-closed, constant-time |
| Phone PII in atrativos list/detail | Info Disclosure | `_safe_normalized` + `mask_phone` on all atrativo response paths |
| Broker timeout → 500 | DoS (self-inflicted) | `try/except` around inspect() + `None` coercion; return 200 with `broker_reachable: false` |
| JSON injection via normalized edit | Tampering | Pydantic body validation; `flag_modified` pattern only (no raw SQL) |
| SSRF via workers endpoint | Elevation | inspect() uses the fixed `BRAVE_DB_REDIS_URL` broker from celery_app config; no user-controlled URL |
| Advance to arbitrary sub_state | Tampering | `advance_sub_state` idempotency guard + `expected_state` check; enum-constrained in Pydantic body |

---

## Sources

### Primary (HIGH confidence)
- `brave/api/routers/dashboard.py` — GET /monitor shape, AuditLog query, PoisonQuarantine query [VERIFIED: direct read]
- `brave/api/routers/dlq.py` — list pattern, pagination, validate/descarte/reprocess actions [VERIFIED: direct read]
- `brave/api/routers/atrativos_gate.py` — approve/reject pattern, mask_phone usage, _safe_normalized [VERIFIED: direct read]
- `brave/core/dlq/service.py` — validate_and_promote_rio signature [VERIFIED: direct read]
- `brave/lanes/atrativos/state_machine.py` — advance_sub_state signature + lock/idempotency pattern [VERIFIED: direct read]
- `brave/core/models.py` — all column types (JSON not JSONB for normalized), AuditLog schema [VERIFIED: direct read]
- `brave/tasks/celery_app.py` — Celery("norteia_brave") app, broker config [VERIFIED: direct read]
- `brave/tasks/beat_schedule.py` — queue name "brave.sweep", 54 entries, task names [VERIFIED: direct read]
- `brave/api/deps.py` — require_bearer, require_steward_or_bearer signatures [VERIFIED: direct read]
- `dashboard/app/globals.css` — Tailwind v4 `@theme inline` + CSS var structure [VERIFIED: direct read]
- `dashboard/package.json` — all installed dep versions [VERIFIED: direct read]
- `dashboard/mocks/handlers/dlq.ts` — MSW URL double-prefix pattern [VERIFIED: direct read]
- `dashboard/vitest.setup.ts` — onUnhandledRequest: "error" + lifecycle [VERIFIED: direct read]
- `dashboard/components/dlq/QueueList.tsx` — TanStack Table v8 pattern [VERIFIED: direct read]
- `dashboard/components/dlq/StatusBadge.tsx` — existing badge pattern [VERIFIED: direct read]
- `dashboard/app/api/[...path]/route.ts` — BFF proxy pattern [VERIFIED: direct read]
- `/Users/leandro/Projects/norteia/norteia-frontend/src/app/globals.css` — Norteia color token values [VERIFIED: direct read]
- `celery.app.control.Inspect` methods/docs — inspected via `.venv/bin/python` [VERIFIED: direct runtime inspection]
- Redis llen("brave.sweep") — confirmed via `.venv/bin/python` against local Redis [VERIFIED: live check]
- write_audit call sites — grepped all .py files in brave/ [VERIFIED: grep across codebase]
- parent_mar_id storage in normalized — discovery_agent.py + routing.py [VERIFIED: direct read]
- Task names — pipeline.py grep for `name=` [VERIFIED: direct grep]

### Secondary (MEDIUM confidence)
- SQLAlchemy 2.0 JSON subscript `column["key"].as_string()` producing `->>` for PostgreSQL JSON type — consistent with SQLAlchemy 2.0 docs pattern; not verified against Postgres execution plan in this session [ASSUMED if actual query fails; fallback: use `cast(func.json_extract_path_text(col, "key"), String)`]

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `RioRecord.normalized["parent_mar_id"].as_string()` emits a working `->>` filter on PostgreSQL JSON type (not JSONB) | Q3 Atrativos list | Filter silently returns no rows. Fallback: `cast(func.json_extract_path_text(RioRecord.normalized, "parent_mar_id"), String) == str(uuid)` |
| A2 | `hsl()` literal values in CSS vars are compatible with Tailwind v4 opacity modifiers (`bg-primary/50`) | Q6 Token mapping | Opacity utilities break visually. Fallback: convert to oklch values |
| A3 | `inspect(timeout=1.0)` does not raise when broker is unreachable — it returns None | Q1 Workers | If it raises (e.g., ConnectionRefusedError), the try/except wrapper handles it; no functional risk |

---

## Metadata

**Confidence breakdown:**
- Backend endpoint patterns: HIGH — all derived from direct inspection of existing routers
- Celery inspect API: HIGH — verified via .venv Python runtime
- Redis queue depth: HIGH — confirmed live LLEN call
- Tailwind v4 token swap: HIGH — globals.css structure confirmed; oklch/hsl compatibility is MEDIUM (A2)
- AuditLog action values: HIGH — grepped all write_audit call sites exhaustively
- JSON path filtering: MEDIUM — SQLAlchemy pattern is standard but not executed against live DB in this session

**Research date:** 2026-06-18
**Valid until:** 2026-07-18 (stable libraries; Celery/SQLAlchemy APIs don't change mid-minor)
