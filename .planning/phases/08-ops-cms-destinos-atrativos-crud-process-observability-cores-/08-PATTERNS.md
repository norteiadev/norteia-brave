# Phase 8: Ops CMS — Destinos/Atrativos CRUD + Process Observability — Pattern Map

**Mapped:** 2026-06-18
**Files analyzed:** 20 (new or modified)
**Analogs found:** 19 / 20

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `brave/api/routers/cms.py` | router | CRUD | `brave/api/routers/dlq.py` | exact |
| `brave/api/routers/workers.py` | router | request-response | `brave/api/routers/dashboard.py` (get_monitor) | role-match |
| `dashboard/app/globals.css` | config | transform | `dashboard/app/globals.css` `:root`/`.dark` blocks | exact (edit-in-place) |
| `dashboard/components/cms/StageBadge.tsx` | component | transform | `dashboard/components/dlq/StatusBadge.tsx` | exact |
| `dashboard/components/cms/JourneyStepper.tsx` | component | transform | `dashboard/components/dlq/ReviewPanel.tsx` (log section) | role-match |
| `dashboard/components/cms/DestinoList.tsx` | component | CRUD | `dashboard/components/dlq/QueueList.tsx` | exact |
| `dashboard/components/cms/AtrativoList.tsx` | component | CRUD | `dashboard/components/dlq/QueueList.tsx` | exact |
| `dashboard/components/cms/DetailPanel.tsx` | component | CRUD | `dashboard/components/dlq/ReviewPanel.tsx` | exact |
| `dashboard/components/processo/WorkerBoard.tsx` | component | request-response | `dashboard/components/monitor/MonitorTiles.tsx` | role-match |
| `dashboard/components/processo/FailuresPanel.tsx` | component | request-response | `dashboard/components/monitor/AlertsPanel.tsx` | role-match |
| `dashboard/app/destinos/page.tsx` | page | CRUD | `dashboard/app/dlq/page.tsx` | exact |
| `dashboard/app/destinos/[id]/page.tsx` | page | CRUD | `dashboard/app/dlq/page.tsx` | role-match |
| `dashboard/app/atrativos/page.tsx` | page | CRUD | `dashboard/app/dlq/page.tsx` | exact |
| `dashboard/app/atrativos/[id]/page.tsx` | page | CRUD | `dashboard/app/dlq/page.tsx` | role-match |
| `dashboard/app/processo/page.tsx` | page | request-response | `dashboard/app/monitor/page.tsx` | exact |
| `dashboard/app/page.tsx` | page | transform | `dashboard/app/page.tsx` (SURFACES array) | exact (edit-in-place) |
| `dashboard/lib/destinos-api.ts` | utility | CRUD | `dashboard/lib/dlq-api.ts` | exact |
| `dashboard/lib/atrativos-api.ts` | utility | CRUD | `dashboard/lib/dlq-api.ts` | exact |
| `dashboard/lib/workers-api.ts` | utility | request-response | `dashboard/lib/monitor-api.ts` | exact |
| `dashboard/mocks/handlers/destinos.ts` | test | CRUD | `dashboard/mocks/handlers/dlq.ts` | exact |
| `dashboard/mocks/handlers/atrativos.ts` | test | CRUD | `dashboard/mocks/handlers/dlq.ts` | exact |
| `dashboard/mocks/handlers/workers.ts` | test | request-response | `dashboard/mocks/handlers/monitor.ts` | exact |

---

## Pattern Assignments

### `brave/api/routers/cms.py` (router, CRUD)

**Analog:** `brave/api/routers/dlq.py` + `brave/api/routers/dashboard.py`

**Imports pattern** (`dlq.py` lines 1-22, `dashboard.py` lines 1-37):
```python
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.api.deps import get_db, require_bearer, require_steward_or_bearer
from brave.core.models import AuditLog, MarRecord, NascenteRecord, RioRecord
from brave.observability.audit import write_audit

router = APIRouter()
```

**Bearer read-list pattern** (`dlq.py` lines 51-84):
```python
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
    # count total before paging (dashboard.py pattern)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.offset(offset).limit(limit)).all()
    items = [...]   # serialize each (rio, mar) tuple
    return {"items": items, "total": total, "offset": offset, "limit": limit}
```

**Detail with AuditLog** (`dashboard.py` lines 69-121 — `get_dlq_detail`):
```python
@router.get("/api/v1/destinos/{rio_id}", dependencies=[Depends(require_bearer)])
def get_destino_detail(rio_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="RioRecord not found")

    nascente = db.get(NascenteRecord, rio.nascente_id)
    audit_rows = list(db.scalars(
        select(AuditLog)
        .where(AuditLog.record_id == rio.id)
        .order_by(AuditLog.created_at.asc())
    ).all())
    # child atrativos summary: SELECT count + GROUP BY sub_state WHERE entity_type='attraction'
    # AND normalized['parent_mar_id'] == str(mar.id) if promoted
    return {
        "id": str(rio.id), "routing": rio.routing,
        "score_breakdown": rio.score_breakdown or {},
        "normalized": rio.normalized or {},
        "audit_log": [{"action": r.action, "actor": r.actor,
                       "after_state": r.after_state,
                       "created_at": r.created_at.isoformat() if r.created_at else None}
                      for r in audit_rows],
        ...
    }
```

**Steward-mutation / PATCH pattern** (`dlq.py` lines 87-177 — reprocess / validate / descarte):
```python
@router.patch(
    "/api/v1/destinos/{rio_id}/promote",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def promote_destino(rio_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "score": float(rio.score or 0)}
    from brave.core.dlq.service import validate_and_promote_rio
    validate_and_promote_rio(db, rio)
    db.refresh(rio)

    if rio.routing == "mar":
        try:
            from brave.tasks.pipeline import push_destination_task
            push_destination_task.delay(str(rio_id))
        except Exception:
            pass  # No broker in tests/dev

    write_audit(session=db, action="dlq_validated", entity_type=rio.entity_type,
                record_id=rio.id,
                before_state=before_state,
                after_state={"routing": rio.routing, "score": float(rio.score or 0)},
                actor="steward")
    return {"status": "accepted", "rio_id": str(rio_id), "routing": rio.routing}
```

**Descarte pattern** (`dlq.py` lines 241-272):
```python
rio.routing = "descarte"
rio.dlq_reason = "steward_rejected"
write_audit(session=db, action="dlq_rejected", entity_type=rio.entity_type,
            record_id=rio.id, before_state=before_state,
            after_state={"routing": "descarte", "dlq_reason": "steward_rejected"},
            actor="steward")
return {"status": "ok", "routing": "descarte", "rio_id": str(rio_id)}
```

**Reprocess pattern** (`dlq.py` lines 87-125):
```python
try:
    from brave.tasks.pipeline import reprocess_record_task
    reprocess_record_task.delay(str(rio_id))
except Exception:
    from brave.config.settings import ScoreConfig
    from brave.core.rio.routing import reprocess_record
    reprocess_record(db, rio_id, ScoreConfig())
write_audit(session=db, action="dlq_reprocessed", ...)
return {"status": "accepted", "rio_id": str(rio_id)}
```

**Atrativos list with JSON subscript filter** (RESEARCH Q3, not in existing code yet):
```python
@router.get("/api/v1/atrativos", dependencies=[Depends(require_bearer)])
def list_atrativos(
    uf: str | None = Query(None),
    sub_state: str | None = Query(None),
    parent_mar_id: str | None = Query(None),
    routing: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(RioRecord).where(RioRecord.entity_type == "attraction")
    if uf:
        stmt = stmt.where(RioRecord.uf == uf)
    if sub_state:
        stmt = stmt.where(RioRecord.sub_state == sub_state)
    if parent_mar_id:
        # JSON (not JSONB) subscript — emits normalized->>'parent_mar_id' in PG
        stmt = stmt.where(
            RioRecord.normalized["parent_mar_id"].as_string() == str(parent_mar_id)
        )
    if routing:
        stmt = stmt.where(RioRecord.routing == routing)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(db.scalars(stmt.offset(offset).limit(limit)).all())
    ...
```

**PII masking** (`atrativos_gate.py` lines 137-151, `_safe_normalized`):
```python
# Import and reuse — DO NOT re-implement
from brave.core.models import mask_phone
# from brave.api.routers.atrativos_gate import _safe_normalized
# or inline the same pattern:
def _safe_normalized(normalized: dict | None) -> dict:
    n = dict(normalized or {})
    contacts = n.get("contacts")
    if isinstance(contacts, dict) and "phone_e164" in contacts:
        contacts = dict(contacts)
        contacts["phone_masked"] = mask_phone(contacts.pop("phone_e164", None))
        n["contacts"] = contacts
    return n
```

**advance_sub_state PATCH** (`state_machine.py` lines 28-90 + RESEARCH Q5):
```python
@router.patch(
    "/api/v1/atrativos/{rio_id}/advance",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
def advance_atrativo_state(rio_id: uuid.UUID, body: AdvanceBody,
                           db: Session = Depends(get_db)) -> dict:
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    from brave.lanes.atrativos.state_machine import advance_sub_state
    advanced = advance_sub_state(
        session=db, rio=rio,
        expected_state=body.expected_state,
        next_state=body.next_state,
        actor="steward",
        lock=True,   # SELECT ... FOR UPDATE; use lock=False only in offline tests
    )
    if not advanced:
        raise HTTPException(status_code=409,
            detail=f"sub_state is '{rio.sub_state}', expected '{body.expected_state}'")
    return {"status": "ok", "rio_id": str(rio_id), "sub_state": rio.sub_state}
```

**JSON mutation (edit canonical fields)** (`service.py` lines 36-41 — Pitfall 3):
```python
# NEVER mutate in-place: rio.normalized["key"] = value  → silently dropped
normalized = dict(rio.normalized or {})
normalized["key"] = value
rio.normalized = normalized
flag_modified(rio, "normalized")   # required: SQLAlchemy won't detect deep mutation
session.flush()
```

---

### `brave/api/routers/workers.py` (router, request-response)

**Analog:** `brave/api/routers/dashboard.py` (`get_monitor`, lines 124-221)

**Imports pattern** (`dashboard.py` lines 18-38):
```python
import uuid
from fastapi import APIRouter, Depends, Query
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, require_bearer
from brave.core.models import PoisonQuarantine

router = APIRouter()
```

**Workers endpoint — graceful degradation** (RESEARCH Q1, full pattern verified):
```python
@router.get("/api/v1/workers", dependencies=[Depends(require_bearer)])
def get_workers(redis: Redis = Depends(get_redis)) -> dict:
    from brave.tasks.celery_app import app as celery_app

    try:
        i = celery_app.control.inspect(timeout=1.0)
        ping     = i.ping()     or {}   # None → {} when broker unreachable
        active   = i.active()   or {}
        reserved = i.reserved() or {}
    except Exception:
        ping = active = reserved = {}

    broker_reachable = bool(ping)
    workers = [
        {
            "hostname": h,
            "status": "up" if resp.get("ok") == "pong" else "down",
            "active_count":   len(active.get(h, [])),
            "reserved_count": len(reserved.get(h, [])),
        }
        for h, resp in ping.items()
    ]

    try:
        queue_depths = {
            "brave.sweep": redis.llen("brave.sweep"),
            "celery":       redis.llen("celery"),
        }
    except Exception:
        queue_depths = {"brave.sweep": None, "celery": None}

    return {
        "broker_reachable": broker_reachable,
        "workers": workers,
        "queues": queue_depths,
        "beat_schedule": {"entries": 54, "queues": ["brave.sweep"]},
    }
```

**Failures endpoint** (mirrors `get_monitor` PoisonQuarantine query, `dashboard.py` lines 196-204):
```python
@router.get("/api/v1/failures", dependencies=[Depends(require_bearer)])
def get_failures(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    rows = list(db.scalars(
        select(PoisonQuarantine)
        .order_by(PoisonQuarantine.quarantined_at.desc())
        .limit(limit)
    ).all())

    by_task: dict[str, int] = {}
    for r in rows:
        by_task[r.task_name] = by_task.get(r.task_name, 0) + 1

    return {
        "total": len(rows),
        "by_task": by_task,
        "items": [
            {
                "id": str(r.id),
                "task_name": r.task_name,
                "error_message": r.error_message,
                "quarantined_at": r.quarantined_at.isoformat() if r.quarantined_at else None,
            }
            for r in rows
        ],
    }
```

---

### `dashboard/app/globals.css` (config, transform — edit in place)

**Analog:** `dashboard/app/globals.css` (lines 49-112) — edit ONLY `:root` and `.dark` CSS var values; the `@theme inline` block (lines 11-47) MUST NOT be changed.

**Existing token structure to replace** (lines 49-112):
```css
/* DO NOT TOUCH @theme inline block (lines 11-47) — it already maps
   --color-primary: var(--primary) etc.  Only swap the values below. */

:root {
  /* REPLACE these two lines only: */
  --primary: oklch(0.62 0.19 255);           /* current shadcn blue   → navy */
  --accent:  oklch(0.97 0 0);                /* current near-white    → terracota */
  /* Also update: */
  --background: oklch(1 0 0);               /* current pure-white    → off-white */
  --primary-foreground: oklch(0.985 0 0);   /* keep near-white — OK */
  --accent-foreground:  oklch(0.205 0 0);   /* update to near-white when accent=terracota */
  /* Status tokens (already exist, keep oklch format): */
  --status-mar:      oklch(0.65 0.17 150);  /* success green — already correct */
  --status-dlq:      oklch(0.75 0.16 80);   /* warning amber — already correct */
  --status-descarte: oklch(0.58 0.22 27);   /* destructive red — already correct */
  /* ADD new tokens for StageBadge (not yet present): */
  --status-in-progress: ...;                /* navy alias = same as --primary */
}
.dark {
  /* REPLACE: */
  --primary: oklch(0.62 0.19 255);          /* current → lighter navy for dark bg */
  --accent:  oklch(0.269 0 0);              /* current → lighter terracota for dark bg */
}
```

**Target values (convert HSL → oklch to match existing format per Pitfall 6):**
- navy `hsl(211 83% 19%)` → `oklch(0.23 0.10 253)` (approx; verify with `color-mix` test)
- terracota `hsl(11 53% 46%)` → `oklch(0.48 0.12 30)` (approx)
- off-white background `hsl(40 33% 98%)` → `oklch(0.98 0.01 90)` (approx)
- dark navy `hsl(211 83% 25%)` → `oklch(0.28 0.10 253)` (approx)
- dark terracota `hsl(11 53% 50%)` → `oklch(0.52 0.12 30)` (approx)

**Pitfall 6 warning:** Test `bg-primary/50` after the swap. If opacity modifiers break, use `hsl()` literals instead of oklch (both are valid CSS var values; only oklch is required for Tailwind's `@theme`-calculated vars, but CSS vars set as `hsl(...)` pass through CSS color functions correctly in current browsers).

---

### `dashboard/components/cms/StageBadge.tsx` (component, transform)

**Analog:** `dashboard/components/dlq/StatusBadge.tsx` (lines 1-60) — extend, do not replace

**Full analog to copy-extend** (`StatusBadge.tsx` lines 1-60):
```typescript
"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// Existing ROUTING_CLASS map to copy as-is:
const ROUTING_CLASS: Record<string, string> = {
  mar:      "border-transparent bg-[var(--status-mar)]/15 text-[var(--status-mar)]",
  dlq:      "border-transparent bg-[var(--status-dlq)]/15 text-[var(--status-dlq)]",
  descarte: "border-transparent bg-[var(--status-descarte)]/15 text-[var(--status-descarte)]",
};

// ADD for StageBadge — in_progress uses navy (--color-primary):
const ROUTING_CLASS_EXTENDED: Record<string, string> = {
  ...ROUTING_CLASS,
  in_progress: "border-transparent bg-[var(--color-primary)]/10 text-[var(--color-primary)]",
};

// ADD sub_state FSM progression (navy gradient by depth):
const SUB_STATE_CLASS: Record<string, string> = {
  discovered:                   "border-transparent bg-[var(--color-primary)]/10 text-[var(--color-primary)]",
  contacts_found:               "border-transparent bg-[var(--color-primary)]/20 text-[var(--color-primary)]",
  signals_gathered:             "border-transparent bg-[var(--color-primary)]/30 text-[var(--color-primary)]",
  aguardando_consulta_whatsapp: "border-transparent bg-[var(--status-dlq)]/15 text-[var(--status-dlq)]",
  whatsapp_in_progress:         "border-transparent bg-[var(--status-dlq)]/25 text-[var(--status-dlq)]",
};
```

**Badge render pattern** (`StatusBadge.tsx` lines 32-60 — copy the `<Badge>` usage exactly):
```typescript
// Badge variant pattern:
<Badge
  variant={tone ? "outline" : "secondary"}
  className={cn("font-mono text-[12px] font-semibold", tone, className)}
>
  {label}
</Badge>
```

**Score band pattern** (derived from `ScoreBreakdownPanel.tsx` `capColor`/`totalCapClass`, lines 72-83):
```typescript
function scoreClass(score: number | null): string {
  if (score == null) return "";
  if (score >= 85) return "border-transparent bg-[var(--status-mar)]/15 text-[var(--status-mar)]";
  if (score >= 40) return "border-transparent bg-[var(--status-dlq)]/15 text-[var(--status-dlq)]";
  return "border-transparent bg-[var(--status-descarte)]/15 text-[var(--status-descarte)]";
}
```

---

### `dashboard/components/cms/JourneyStepper.tsx` (component, transform)

**Analog:** `dashboard/components/dlq/ReviewPanel.tsx` audit log section (lines 128-151) + RESEARCH Q4 step mapping

**Audit log render pattern to extend** (`ReviewPanel.tsx` lines 128-151):
```typescript
// Existing log render (copy list structure, extend with step semantics):
<ul className="flex flex-col gap-1">
  {detail.whatsapp_log.map((entry) => (
    <li key={entry.id}
        className="flex items-baseline gap-2 font-mono text-[12px] tabular-nums">
      <span className="text-muted-foreground">
        {entry.created_at ?? "—"}
      </span>
      <span className="font-semibold">{entry.action}</span>
      {entry.actor ? (
        <span className="text-muted-foreground">· {entry.actor}</span>
      ) : null}
    </li>
  ))}
</ul>
```

**Step mapping per RESEARCH Q4:**
```typescript
// Destino steps: infer steps 1-2 from record existence (no AuditLog for these)
// Step 1: Nascente — completed if nascente_id is populated (always true for any record)
// Step 2: Rio/Score — completed if score is populated
// Step 3: DLQ gate — show if AuditLog has dlq_validated/dlq_rejected/dlq_reprocessed rows
// Step 4: Mar — current step if routing === 'mar'

// Atrativo steps: map AuditLog action + after_state.sub_state:
// atrativo_discovered          → step 1 (discovered)
// sub_state_advanced (contacts_found)    → step 2
// sub_state_advanced (signals_gathered)  → step 3
// sub_state_advanced (aguardando_*)      → step 4
// whatsapp_gate_approved                 → step 5
// routing === 'mar' / whatsapp_gate_rejected → step 6
```

---

### `dashboard/components/cms/DestinoList.tsx` + `AtrativoList.tsx` (component, CRUD)

**Analog:** `dashboard/components/dlq/QueueList.tsx` (lines 1-307) — copy entire structure

**TanStack Table v8 pattern** (`QueueList.tsx` lines 1-118):
```typescript
"use client";

import {
  flexRender, getCoreRowModel, useReactTable,
  type ColumnDef, type RowSelectionState,
} from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

// columns definition:
const columns = useMemo<ColumnDef<DestinoListItem>[]>(() => [
  {
    id: "name",
    header: "Nome",
    cell: ({ row }) => (
      <span className="font-mono text-[12px]">{row.original.name ?? row.original.id.slice(0, 8)}</span>
    ),
  },
  {
    id: "score",
    header: "Score",
    cell: ({ row }) => (
      <span className="font-mono text-[12px] tabular-nums">
        {row.original.score != null ? row.original.score.toFixed(1) : "—"}
      </span>
    ),
  },
  {
    id: "routing",
    header: "Estado",
    cell: ({ row }) => <StageBadge routing={row.original.routing} score={row.original.score} />,
  },
], []);

// Table instantiation (lines 110-118):
const table = useReactTable({
  data, columns,
  state: { rowSelection },
  onRowSelectionChange: setRowSelection,
  getRowId: (r) => r.id,
  enableRowSelection: true,
  getCoreRowModel: getCoreRowModel(),
});
```

**Row click pattern** (`QueueList.tsx` line 217-218):
```typescript
<TableRow
  onClick={() => onSelect?.(row.original.id)}
  className={cn("h-9 cursor-pointer", selectedId === row.original.id && "bg-muted")}
>
```

**Filter pattern** (`QueueList.tsx` lines 125-141 — UF tabs):
```typescript
// Replace UF_PRIORITY tabs with UF dropdown + routing filter for CMS:
const [uf, setUf] = useState<string | undefined>(undefined);
const [routing, setRouting] = useState<string | undefined>(undefined);
// useQuery with these as deps in queryKey
```

**Loading/error/empty states** (`QueueList.tsx` lines 181-247) — copy `QueueSkeleton` / `QueueError` / `QueueEmpty` verbatim, change copy strings to "Destinos" / "Atrativos".

---

### `dashboard/components/cms/DetailPanel.tsx` (component, CRUD)

**Analog:** `dashboard/components/dlq/ReviewPanel.tsx` (lines 1-183) — copy full structure, inject actions differently

**Full panel structure** (`ReviewPanel.tsx` lines 32-183):
```typescript
export function DetailPanel({
  rioId,
  actions,
  fetchDetail,    // injected fetcher so destinos/atrativos can differ
  queryKeys,      // injected query keys
}: {
  rioId: string | null;
  actions?: (detail: unknown) => ReactNode;
  fetchDetail: (id: string) => Promise<unknown>;
  queryKeys: { detail: (id: string) => readonly unknown[] };
}) {
  // Mirror ReviewPanel lines 40-163 exactly:
  const query = useQuery({
    queryKey: rioId ? queryKeys.detail(rioId) : ["detail", "none"],
    queryFn: () => fetchDetail(rioId as string),
    enabled: rioId != null,
  });
  // ... loading / error / 401 states (copy verbatim from ReviewPanel.tsx lines 54-94)
  // ... render: header with StageBadge + ScoreBreakdownPanel + audit log as JourneyStepper
}
```

**Action-agnostic injection** (`ReviewPanel.tsx` lines 152-160):
```typescript
{actions ? (
  <>
    <Separator />
    <div className="flex flex-wrap items-center gap-2">
      {actions(detail)}
    </div>
  </>
) : null}
```

---

### `dashboard/components/processo/WorkerBoard.tsx` (component, request-response)

**Analog:** `dashboard/components/monitor/MonitorTiles.tsx` (lines 1-165)

**Polling hook instantiation** (`useMonitor.ts` lines 20-27 — mirror exactly):
```typescript
"use client";
import { useQuery } from "@tanstack/react-query";
import { WORKERS_REFETCH_INTERVAL_MS, fetchWorkers, workersKeys } from "@/lib/workers-api";

export function useWorkers() {
  return useQuery({
    queryKey: workersKeys.data(),
    queryFn: fetchWorkers,
    refetchInterval: WORKERS_REFETCH_INTERVAL_MS,
    refetchOnWindowFocus: false,
  });
}
```

**Tile render pattern** (`MonitorTiles.tsx` lines 119-145 — `VolumeTile`):
```typescript
// Copy VolumeTile for worker up/down tiles:
function WorkerTile({ hostname, status, activeCount }: ...) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border bg-card p-4">
      <span className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
        {hostname.replace("celery@", "")}
      </span>
      <span className={`text-[28px] font-semibold leading-none tabular-nums
        ${status === "up" ? "text-[var(--status-mar)]" : "text-[var(--status-descarte)]"}`}>
        {status === "up" ? "UP" : "DOWN"}
      </span>
      <span className="text-[12px] text-muted-foreground">{activeCount} ativas</span>
    </div>
  );
}
```

**Loading / error states** (`MonitorTiles.tsx` lines 22-56 — copy `isPending` skeleton + `isError` with refetch button verbatim):
```typescript
if (isPending) {
  return (
    <div data-testid="worker-board-skeleton"
         className="grid grid-cols-2 gap-4 sm:grid-cols-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton key={i} className="h-24 rounded-lg" />
      ))}
    </div>
  );
}
```

---

### `/processo` page (`dashboard/app/processo/page.tsx`) (page, request-response)

**Analog:** `dashboard/app/monitor/page.tsx` (lines 1-42)

**Page layout pattern** (`monitor/page.tsx` lines 1-42 — copy exactly):
```typescript
"use client";

import { WorkerBoard } from "@/components/processo/WorkerBoard";
import { FailuresPanel } from "@/components/processo/FailuresPanel";
// + HumanPendingTiles (reuse existing DLQ count + gate count endpoints)
// + StageFunnel (recharts BarChart, mirror CostByLaneChart pattern)

export default function ProcessoPage() {
  return (
    <main className="flex min-h-dvh flex-col gap-6 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-[20px] font-semibold">Processo Brave</h1>
        <span className="text-[12px] text-muted-foreground">
          Workers · falhas · fila humana · funil · atualização ao vivo (10s)
        </span>
      </header>

      <WorkerBoard />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_1fr]">
        <section className="rounded-md border p-4">
          {/* StageFunnel recharts BarChart */}
        </section>
        <section>
          <FailuresPanel />
        </section>
      </div>
    </main>
  );
}
```

---

### `/destinos` + `/atrativos` list pages (page, CRUD)

**Analog:** `dashboard/app/dlq/page.tsx` (lines 1-155)

**Master-detail layout pattern** (`dlq/page.tsx` lines 44-83):
```typescript
"use client";
import { useState } from "react";

export default function DestinosPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <main className="flex h-dvh flex-col gap-4 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-[20px] font-semibold">Destinos</h1>
        <span className="text-[12px] text-muted-foreground">
          Todas as etapas · Nascente → Rio → Mar
        </span>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-8 xl:grid-cols-[minmax(360px,1fr)_2fr]">
        <section className="min-h-0 overflow-hidden">
          <DestinoList selectedId={selectedId} onSelect={setSelectedId} />
        </section>
        <section className="min-h-0 overflow-hidden rounded-md border">
          <DetailPanel
            rioId={selectedId}
            fetchDetail={fetchDestinoDetail}
            queryKeys={destinoKeys}
            actions={(detail) => <DestinoActions detail={detail} />}
          />
        </section>
      </div>
    </main>
  );
}
```

**Action bar pattern** (`dlq/page.tsx` lines 85-155 — `DlqActions`):
```typescript
function DestinoActions({ detail, pending }: ...) {
  return (
    <>
      <Button size="sm" disabled={pending} onClick={onPromote}>
        Promover para Mar
      </Button>
      <Button size="sm" variant="outline" disabled={pending} onClick={onReprocess}>
        Reprocessar
      </Button>
      {/* Destructive behind AlertDialog (copy pattern exactly from DlqActions lines 123-148) */}
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button size="sm" variant="destructive" disabled={pending}>Descartar</Button>
        </AlertDialogTrigger>
        ...
      </AlertDialog>
    </>
  );
}
```

---

### `dashboard/app/page.tsx` (page, transform — edit in place)

**Analog:** `dashboard/app/page.tsx` lines 15-22 (SURFACES array)

**Pattern to extend** (`page.tsx` lines 15-22):
```typescript
const SURFACES = [
  { href: "/dlq",           title: "Fila DLQ",        desc: "..." },
  { href: "/monitor",       title: "Monitor Brave",   desc: "..." },
  { href: "/gate",          title: "Gate WhatsApp",   desc: "..." },
  { href: "/cost",          title: "Custo & LLM",     desc: "..." },
  { href: "/funnels",       title: "Funis",           desc: "..." },
  { href: "/conversations", title: "Conversas",       desc: "..." },
  // ADD three new entries here:
  { href: "/destinos",  title: "Destinos",  desc: "CMS territorial · lista/detalhe/ações por etapa" },
  { href: "/atrativos", title: "Atrativos", desc: "CMS atrativo · FSM sub_state · detalhe/ações" },
  { href: "/processo",  title: "Processo",  desc: "Workers · falhas · fila humana · funil" },
];
```

---

### `dashboard/lib/destinos-api.ts` + `atrativos-api.ts` (utility, CRUD)

**Analog:** `dashboard/lib/dlq-api.ts` (lines 1-133) — copy full structure

**Query key + fetcher pattern** (`dlq-api.ts` lines 63-133):
```typescript
import { apiFetch } from "@/lib/api-client";

export interface DestinoListItem {
  id: string;
  entity_type: string;
  uf: string | null;
  routing: string;
  score: number | null;
  name: string | null;
  canonical_key: string | null;
  validation_pending: boolean;
  mar_id: string | null;
}

// TanStack query key factory — same prefix pattern as dlqKeys:
export const destinoKeys = {
  all: ["destinos"] as const,
  list: (filters: Record<string, unknown>) => ["destinos", "list", filters] as const,
  detail: (id: string) => ["destinos", "detail", id] as const,
};

// qs helper — copy verbatim from dlq-api.ts lines 71-78
function qs(params: Record<string, string | number | undefined>): string { ... }

export function fetchDestinoList(params: {uf?: string; routing?: string; offset?: number; limit?: number}): Promise<{items: DestinoListItem[]; total: number}> {
  return apiFetch(`api/v1/destinos${qs(params)}`);
}

export function fetchDestinoDetail(id: string): Promise<DestinoDetail> {
  return apiFetch(`api/v1/destinos/${id}`);
}

export function promoteDestino(id: string): Promise<MutationResult> {
  return apiFetch(`api/v1/destinos/${id}/promote`, { method: "PATCH" });
}

export function descarteDestino(id: string): Promise<MutationResult> {
  return apiFetch(`api/v1/destinos/${id}/descarte`, { method: "PATCH" });
}

export function reprocessDestino(id: string): Promise<MutationResult> {
  return apiFetch(`api/v1/destinos/${id}/reprocess`, { method: "PATCH" });
}
```

---

### `dashboard/lib/workers-api.ts` (utility, request-response)

**Analog:** `dashboard/lib/monitor-api.ts` (lines 1-67)

**Polling constant + typed fetcher pattern** (`monitor-api.ts` lines 17-67):
```typescript
import { apiFetch } from "@/lib/api-client";

export const WORKERS_REFETCH_INTERVAL_MS = 10_000;  // same as monitor (10s)

export interface WorkerInfo {
  hostname: string;
  status: "up" | "down";
  active_count: number;
  reserved_count: number;
}

export interface WorkersData {
  broker_reachable: boolean;
  workers: WorkerInfo[];
  queues: { "brave.sweep": number | null; celery: number | null };
  beat_schedule: { entries: number; queues: string[] };
}

export const workersKeys = {
  all: ["workers"] as const,
  data: () => ["workers", "data"] as const,
};

export function fetchWorkers(): Promise<WorkersData> {
  return apiFetch<WorkersData>("api/v1/workers");
}

export function fetchFailures(limit = 50): Promise<FailuresData> {
  return apiFetch<FailuresData>(`api/v1/failures?limit=${limit}`);
}
```

---

### `dashboard/mocks/handlers/destinos.ts` + `atrativos.ts` + `workers.ts` (test)

**Analog:** `dashboard/mocks/handlers/dlq.ts` (lines 1-201) + `dashboard/mocks/handlers/monitor.ts` (lines 1-88)

**Critical URL pattern** (`dlq.ts` line 18 — MUST copy exactly, Pitfall 5):
```typescript
// ALWAYS double-prefix: browser hits /api/ (BFF) which strips /api/ → FastAPI path
const BASE = "http://localhost:3000/api/api/v1/destinos";
//                                  ^^^^ BFF mount   ^^^^ FastAPI router prefix
```

**Handler factory pattern** (`dlq.ts` lines 97-201):
```typescript
import { http, HttpResponse } from "msw";

export const sampleDestinos: DestinoListItem[] = [
  { id: "11111111-...", routing: "dlq", score: 72.4, name: "Pelourinho", uf: "BA",
    canonical_key: "ba:salvador:pelourinho", validation_pending: true, mar_id: null, entity_type: "destination" },
  { id: "22222222-...", routing: "mar", score: 91.2, name: "Copacabana", uf: "RJ",
    canonical_key: "rj:rio:copacabana", validation_pending: false, mar_id: "mar-...", entity_type: "destination" },
];

export function destinosListSuccess(items = sampleDestinos) {
  return http.get(BASE, ({ request }) => {
    const url = new URL(request.url);
    const uf = url.searchParams.get("uf");
    const filtered = uf ? items.filter((i) => i.uf === uf) : items;
    return HttpResponse.json({ items: filtered, total: filtered.length, offset: 0, limit: 50 });
  });
}
export function destinosListEmpty() {
  return http.get(BASE, () => HttpResponse.json({ items: [], total: 0, offset: 0, limit: 50 }));
}
export function destinosListError(statusCode = 500) {
  return http.get(BASE, () => HttpResponse.json({ detail: "boom" }, { status: statusCode }));
}
export function destinoDetailSuccess(detail = sampleDetail) {
  return http.get(`${BASE}/:id`, () => HttpResponse.json(detail));
}
export function destinoPromoteSuccess() {
  return http.patch(`${BASE}/:id/promote`, () =>
    HttpResponse.json({ status: "accepted", routing: "mar" }, { status: 202 }));
}
// ... etc; mirror dlqHandlers barrel at bottom
export const destinoHandlers = [destinosListSuccess(), destinoDetailSuccess(), destinoPromoteSuccess()];
```

**Workers handler** (mirrors `monitor.ts` lines 66-88):
```typescript
const BASE_WORKERS = "http://localhost:3000/api/api/v1/workers";
const BASE_FAILURES = "http://localhost:3000/api/api/v1/failures";

export const sampleWorkers: WorkersData = {
  broker_reachable: true,
  workers: [{ hostname: "celery@worker-1", status: "up", active_count: 2, reserved_count: 5 }],
  queues: { "brave.sweep": 27, celery: 0 },
  beat_schedule: { entries: 54, queues: ["brave.sweep"] },
};

export const sampleWorkersBrokerDown: WorkersData = {
  broker_reachable: false,
  workers: [],
  queues: { "brave.sweep": null, celery: null },
  beat_schedule: { entries: 54, queues: ["brave.sweep"] },
};

export function workersSuccess(data = sampleWorkers) {
  return http.get(BASE_WORKERS, () => HttpResponse.json(data));
}
export function workersBrokerDown() {
  return http.get(BASE_WORKERS, () => HttpResponse.json(sampleWorkersBrokerDown));
}
export function workersError(statusCode = 500) {
  return http.get(BASE_WORKERS, () => HttpResponse.json({ detail: "boom" }, { status: statusCode }));
}
```

**Offline pytest pattern for broker-absent** (RESEARCH Q1):
```python
# In pytest — DO NOT use a real Celery broker in CI:
from unittest.mock import MagicMock

def test_workers_broker_down(client, monkeypatch):
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
    assert resp.json()["broker_reachable"] is False
    assert resp.json()["workers"] == []
```

**advance_sub_state offline test (Pitfall 4 — use lock=False)**:
```python
# In test: lock=False because sqlite/mock session has no FOR UPDATE
def test_advance_sub_state_mock(db_session):
    rio = RioRecord(sub_state="discovered", ...)
    result = advance_sub_state(
        session=db_session, rio=rio,
        expected_state="discovered", next_state="contacts_found",
        actor="steward", lock=False,  # REQUIRED for mock sessions
    )
    assert result is True
    assert rio.sub_state == "contacts_found"
```

---

## Shared Patterns

### Authentication (apply to ALL new endpoints)
**Source:** `brave/api/deps.py` lines 52-121
```python
# Read-only endpoints:
@router.get("/api/v1/...", dependencies=[Depends(require_bearer)])
# Mutation endpoints:
@router.patch("/api/v1/...", dependencies=[Depends(require_steward_or_bearer)])
```
Both are already implemented — import from `brave.api.deps`, never re-implement.

### Write-Audit (apply to ALL PATCH handlers)
**Source:** `brave/api/routers/dlq.py` lines 117-124 and 263-270
```python
from brave.observability.audit import write_audit
write_audit(
    session=db,
    action="dlq_validated",           # use the canonical action string
    entity_type=rio.entity_type,
    record_id=rio.id,
    before_state=before_state,         # capture BEFORE the mutation
    after_state={"routing": rio.routing, "score": float(rio.score or 0)},
    actor="steward",
)
```

### JSON Mutation (flag_modified — apply to ALL canonical field edits)
**Source:** `brave/core/dlq/service.py` lines 36-41
```python
normalized = dict(rio.normalized or {})
normalized["key"] = new_value
rio.normalized = normalized
flag_modified(rio, "normalized")   # REQUIRED — SQLAlchemy won't detect in-place mutations
session.flush()
```

### 404 Pattern (apply to ALL detail/mutation endpoints)
**Source:** `brave/api/routers/dashboard.py` lines 81-85
```python
rio = db.get(RioRecord, rio_id)
if rio is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="RioRecord not found")
```

### apiFetch + BFF (apply to ALL frontend API callers)
**Source:** `dashboard/lib/api-client.ts` lines 55-75
```typescript
// All callers use apiFetch with the FastAPI path (not the BFF-prefixed path):
apiFetch<T>("api/v1/destinos")      // results in fetch("/api/api/v1/destinos")
// Never: fetch("/api/v1/destinos") — that bypasses the BFF Bearer injection
```

### Error states (apply to ALL list + detail components)
**Source:** `dashboard/components/dlq/QueueList.tsx` lines 275-307 + `ReviewPanel.tsx` lines 65-94
```typescript
// Three states: 401 → "Sessão expirada", generic error → "Não foi possível carregar" + retry, loading → Skeleton
// Copy the exact PT-BR copy strings for consistency.
```

### TanStack Query invalidation after mutations (apply to ALL mutation hooks)
**Source:** `dashboard/components/dlq/dlq-actions.ts` (pattern implied by the DLQ mutation hooks):
```typescript
// After a successful PATCH, invalidate the list cache so it re-fetches:
import { useQueryClient } from "@tanstack/react-query";
const qc = useQueryClient();
onSuccess: () => {
  qc.invalidateQueries({ queryKey: destinoKeys.all });
}
```

### Recharts BarChart for funnel (apply to `/processo` stage funnel)
**Source:** `dashboard` recharts usage (CostByLaneChart.tsx exists; confirmed installed recharts 3.8.0):
```typescript
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
<ResponsiveContainer width="100%" height={200}>
  <BarChart data={funnelData}>
    <XAxis dataKey="stage" />
    <YAxis />
    <Bar dataKey="count" fill="var(--color-primary)" />
  </BarChart>
</ResponsiveContainer>
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `brave/api/routers/workers.py` GET /api/v1/workers | router | request-response | No existing Celery-inspect endpoint in the project; pattern fully derived from RESEARCH Q1 + Celery 5.6.x docs |

(All other files have adequate existing analogs in the codebase.)

---

## Critical Pitfalls to Propagate into Plans

| Pitfall | File(s) Affected | Pattern Source |
|---|---|---|
| `inspect(timeout=1.0)` + `None → {}` coercion | `workers.py` | RESEARCH Q1 |
| `as_string()` JSON subscript not `@>` JSONB path | `cms.py` atrativos filter | RESEARCH Q3 |
| `flag_modified` after JSON key reassign | `cms.py` edit canonical | `service.py` lines 36-41 |
| `lock=False` in offline pytest for advance_sub_state | backend tests | `state_machine.py` + RESEARCH Pitfall 4 |
| MSW BASE URL double-prefix `http://localhost:3000/api/api/v1/...` | all handler files | `mocks/handlers/dlq.ts` line 18 |
| oklch format in CSS vars for Tailwind v4 opacity modifiers | `globals.css` | RESEARCH Pitfall 6 |

---

## Metadata

**Analog search scope:** `brave/api/routers/`, `brave/core/dlq/`, `brave/lanes/atrativos/`, `brave/api/deps.py`, `brave/core/models.py`, `dashboard/app/`, `dashboard/components/`, `dashboard/lib/`, `dashboard/mocks/`
**Files scanned:** 27 source files read directly
**Pattern extraction date:** 2026-06-18
