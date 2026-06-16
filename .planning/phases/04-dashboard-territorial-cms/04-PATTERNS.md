# Phase 4: Dashboard (Territorial CMS) - Pattern Map

**Mapped:** 2026-06-16
**Files analyzed:** 18 (5 backend new/modified · 1 backend test · 12 frontend greenfield groups)
**Analogs found:** 6 / 6 backend files have strong in-repo analogs · 0 / 12 frontend (greenfield, no in-repo analog)

> **Split-repo reality:** the Python (`brave/`) side has precise, copy-from analogs — map them exactly. The Next.js (`dashboard/`) side is greenfield (`.gitkeep` only); there is **no in-repo React/Next analog**. For every frontend file below the analog column reads **"none — greenfield"** and the planner must follow UI-SPEC (`04-UI-SPEC.md`) + RESEARCH §5 (`04-RESEARCH.md`) stack patterns, not a fabricated analog.

---

## File Classification

### Backend (Python — `brave/`, exists; extend read-only per D-01)

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `brave/api/routers/dashboard.py` (NEW — monitor/cost/funnels/conversations + DLQ detail) | router | CRUD / aggregation read | `brave/api/routers/metrics.py` (aggregation) + `brave/api/routers/dlq.py` (GET list + DI shape) + `brave/api/routers/audit.py` (windowed read) | exact (read-aggregation) |
| `brave/api/deps.py` (MODIFIED — add `require_bearer` + `get_dashboard_config`) | middleware / DI | request-response auth | `require_steward` in `brave/api/routers/dlq.py` (lines 25-47) + `get_steward_config` in `deps.py` (lines 35-37) | exact |
| `brave/config/settings.py` (MODIFIED — add `DashboardConfig`) | config | request-response | `StewardConfig` in `settings.py` (lines 113-127) | exact |
| `brave/api/main.py` (MODIFIED — register `dashboard_router`) | config / wiring | n/a | `main.py` (lines 16-37, router import + `include_router`) | exact |
| `brave/api/routers/dlq.py` / `atrativos_gate.py` (MODIFIED — accept Bearer **or** steward on mutations, R4) | middleware | request-response auth | `require_steward` (both files) — extend to either-or | exact |
| Conversation persistence (OPTIONAL — `conversation_message` model + Alembic migration, RESEARCH §4 Option B) | model + migration | event-driven append-log | `ConsentLog` in `brave/core/models.py` (lines 348-398) — same "our-own-table, LGPD-minimized, FK to rio" posture | role-match |
| `tests/integration/test_dashboard_endpoints.py` (NEW) | test | request-response | `tests/integration/test_fastapi_endpoints.py` (whole file) | exact |

### Frontend (Next.js — `dashboard/`, GREENFIELD; no in-repo analog)

| New/Modified File (illustrative — planner finalizes tree) | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `dashboard/app/layout.tsx`, `app/page.tsx`, nav shell | provider / server component | request-response | none — greenfield | n/a |
| `dashboard/app/providers.tsx` (TanStack `QueryClientProvider`) | provider | request-response | none — greenfield | n/a |
| `dashboard/app/api/**/route.ts` (BFF Route Handlers — proxy to FastAPI, inject secret) | route (BFF) | request-response proxy | none — greenfield (mirrors backend `require_steward` *discipline* only) | n/a |
| `dashboard/lib/auth.ts` (browser Bearer validation at BFF edge) | utility | request-response auth | none — greenfield | n/a |
| `dashboard/lib/api-client.ts` (typed fetch wrappers / query keys) | utility | request-response | none — greenfield | n/a |
| `dashboard/components/dlq/QueueList.tsx`, `ReviewPanel.tsx` (master-detail, D-06) | component | CRUD | none — greenfield | n/a |
| `dashboard/components/dlq/ScoreBreakdownPanel.tsx` (§7.6 bars) | component | transform/render | none — greenfield | n/a |
| `dashboard/components/monitor/*` (tiles + Recharts) | component | polling read | none — greenfield | n/a |
| `dashboard/components/gate/*` (WhatsApp gate queue) | component | CRUD | none — greenfield (UI reuses `ReviewPanel`) | n/a |
| `dashboard/components/cost/*`, `funnels/*`, `conversations/*` | component | read / chart | none — greenfield | n/a |
| `dashboard/components/ui/*` (shadcn vendored: button/table/dialog/chart/...) | component | n/a | none — `npx shadcn add` at scaffold | n/a |
| `dashboard/**/*.test.tsx` + `mocks/handlers.ts` (Vitest 4 + MSW 2) | test | request-response | none — greenfield (mirrors backend offline-by-default *discipline*) | n/a |

---

## Pattern Assignments — Backend (concrete excerpts)

### `brave/api/routers/dashboard.py` (NEW: router, read-aggregation)

**Analogs:** `metrics.py` (full file — aggregation), `dlq.py` (GET list + response-dict shape), `audit.py` (windowed/ordered read). Register in `main.py`. Guard with the new `require_bearer` (read endpoints), not steward.

**Imports + router pattern** — copy from `metrics.py` lines 1-10 / `dlq.py` lines 10-22:
```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, require_bearer  # require_bearer is NEW (see deps.py below)
from brave.core.models import (
    AuditLog, LLMGeneration, MarRecord, NascenteRecord, PoisonQuarantine, RioRecord,
)

router = APIRouter()
```

**Aggregation core pattern (cost-by-lane/model, DASH-04)** — copy the `func.count` + `group_by` + dict-merge shape from `metrics.py` lines 30-45; swap to `func.sum(usd_cost)` over `LLMGeneration`:
```python
@router.get("/api/v1/cost", dependencies=[Depends(require_bearer)])
def get_cost(group_by: str = Query("lane"), db: Session = Depends(get_db)) -> dict:
    col = LLMGeneration.lane if group_by == "lane" else LLMGeneration.model_slug
    rows = db.execute(
        select(
            col,
            func.sum(LLMGeneration.usd_cost),
            func.sum(LLMGeneration.prompt_tokens + LLMGeneration.completion_tokens),
            func.count(LLMGeneration.id),
        ).group_by(col)
    ).fetchall()
    return {
        "group_by": group_by,
        "rows": [
            {"key": k, "usd_cost": float(cost or 0), "tokens": int(tok or 0), "count": int(n)}
            for k, cost, tok, n in rows
        ],
    }
```
> Reuse `metrics.py`'s exact idioms: `db.scalar(select(func.count(...)))` for single counts, `db.execute(select(col, func.count(...)).group_by(col)).fetchall()` for grouped, and the `{v: 0 for v in VALUES}` pre-seed so empty groups return 0 (lines 27-39).

**Monitor rates (DASH-02)** — derive from `AuditLog` action counts over a window. Borrow the ordered/windowed read shape from `audit.py` lines 25-32 (`select(...).order_by(AuditLog.created_at.desc())`); aggregate actions (`dlq_validated`/`dlq_rejected`/`dlq_reprocessed`) with `group_by(AuditLog.action)`. Failure alerts: `func.count(PoisonQuarantine.id)`. Throughput: `func.count(RioRecord.id)` filtered on `RioRecord.processed_at` window.

**DLQ detail (DASH-01, `GET /api/v1/dlq/{rio_id}`)** — extend `dlq.py`. The existing list (lines 70-83) deliberately **omits** `score_breakdown`, `normalized`, payload. The detail endpoint surfaces them by joining `NascenteRecord` (`db.get(RioRecord, rio_id)` → `.nascente` relationship at `models.py` line 154):
```python
@router.get("/api/v1/dlq/{rio_id}", dependencies=[Depends(require_bearer)])
def get_dlq_detail(rio_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")
    nascente = db.get(NascenteRecord, rio.nascente_id)
    return {
        "id": str(rio.id),
        "routing": rio.routing,
        "sub_state": rio.sub_state,
        "dlq_reason": rio.dlq_reason,
        "score": float(rio.score) if rio.score is not None else None,
        "score_version": rio.score_version,
        "score_breakdown": rio.score_breakdown or {},   # §7.6 per-criterion — DASH-01 panel
        "normalized": rio.normalized or {},
        "nascente_payload": nascente.payload if nascente else {},
        # whatsapp log: AuditLog rows where record_id == rio.id (order_by created_at)
    }
```
> **404 idiom** is copy-exact from `dlq.py` lines 100-102 / 147-149: `db.get(...)` then `if x is None: raise HTTPException(404, ...)`. Use it in every detail endpoint.

**Funnels (DASH-05)** — `NascenteRecord` (ingested, group by `source`/`uf`) → `RioRecord` (group by `routing`, `uf`) → `MarRecord` counts. Pure `group_by` aggregation; same shape as cost above. Honour the `entity_type`/`uf`/`source` query filters like `dlq.py` list lines 52-66 (`if uf: query = query.where(...)`).

**LGPD (R3, UI-SPEC PII rule):** conversation/gate read endpoints must **mask `phone_e164`** before returning (never ship raw PII to the browser). `ConsentLog.phone_e164` (`models.py` line 364) is the source — minimize it server-side.

---

### `brave/api/deps.py` (MODIFIED: add `require_bearer` + `get_dashboard_config`)

**Analog:** `require_steward` in `dlq.py` lines 25-47 (the auth *discipline*) + the config getters in `deps.py` lines 30-37 (the DI shape). **Copy `require_steward` verbatim, swap the header to `Authorization: Bearer`, strip the `Bearer ` prefix, compare against `DashboardConfig.bearer_token`.** Keep every security property: `hmac.compare_digest`, fail-closed on unset token, 401 **before** any DB work, secret never logged.

**Config getter** — copy `get_steward_config` (`deps.py` lines 35-37):
```python
def get_dashboard_config() -> DashboardConfig:
    """Return DashboardConfig (BRAVE_DASHBOARD_BEARER_TOKEN) for the dashboard read surface."""
    return DashboardConfig()
```

**Bearer dependency** — mirror `require_steward` (`dlq.py` lines 25-47), swapping the header:
```python
def require_bearer(
    authorization: str | None = Header(None, alias="Authorization"),
    dashboard_config: DashboardConfig = Depends(get_dashboard_config),
) -> None:
    expected = dashboard_config.bearer_token
    token = authorization.removeprefix("Bearer ").strip() if authorization else None
    if not token or not expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authorization: Bearer token required")
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid bearer token")
```
> Add imports `import hmac` and `from fastapi import Depends, Header, HTTPException, status` to `deps.py` (currently it imports neither — see lines 11-18). `require_steward` lives in `dlq.py`/`atrativos_gate.py`, not `deps.py`; the planner may relocate the new dependency into a small `brave/api/auth.py` if preferred, but the auth getters already live in `deps.py` so co-locating there matches the existing layout.

---

### `brave/config/settings.py` (MODIFIED: add `DashboardConfig`)

**Analog:** `StewardConfig` lines 113-127 — copy verbatim, rename class/prefix/field. Keep the **no-alias / `env_prefix`-only** rule (CR-02, file header lines 12-15) and the fail-closed `Field(default="")`:
```python
class DashboardConfig(BaseSettings):
    """Dashboard Bearer-header auth (DASH-06, D-02).

    BRAVE_DASHBOARD_BEARER_TOKEN gates the read-only dashboard endpoints (and,
    either-or with X-Steward-Secret, the mutation endpoints the BFF drives).
    Compared with hmac.compare_digest (constant-time) — never logged. Fail-closed:
    an unset token rejects all callers. Single operator token this milestone
    (multi-user/RBAC deferred).
    """

    bearer_token: str = Field(default="", description="Shared operator Bearer token")

    model_config = SettingsConfigDict(env_prefix="BRAVE_DASHBOARD_")
```
> Mirror `StewardConfig` exactly: no `Field(alias=...)` (secret-shadowing risk per CR-02), prefixed env var only (`BRAVE_DASHBOARD_BEARER_TOKEN`).

---

### `brave/api/main.py` (MODIFIED: register router)

**Analog:** `main.py` lines 16-37 — same two-line idiom (import + `app.include_router`). Add alongside the Phase 3 atrativos block:
```python
from brave.api.routers.dashboard import router as dashboard_router
...
# Phase 4: Dashboard read-aggregation surface (D-01, DASH-01..05)
app.include_router(dashboard_router)
```

---

### Mutation endpoints accept Bearer OR steward (R4, D-02)

**Analog + risk:** `require_steward` is referenced as a route dependency in `dlq.py` (lines 89, 130, 199, 273) and `atrativos_gate.py` (lines 174, 271). To let the dashboard's single Bearer token drive approve/reject/validate **without breaking Phase 2/3 steward tests**, replace the per-route `Depends(require_steward)` with an **either-or** dependency that passes if **either** a valid `X-Steward-Secret` **or** a valid `Authorization: Bearer` is present (both use `hmac.compare_digest`, fail-closed). Do **not** duplicate the mutation endpoints — the UI calls the existing PATCH/POST routes through the BFF.

---

### `tests/integration/test_dashboard_endpoints.py` (NEW: offline pytest + TestClient)

**Analog:** `tests/integration/test_fastapi_endpoints.py` (whole file). Reuse exactly:

- **TestClient fixture** (lines 17-23): `TestClient(app, raise_server_exceptions=False)`, `os.environ.setdefault("BRAVE_DB_URL", ...)`.
- **Secret fixture** (lines 26-32): set `os.environ["BRAVE_DASHBOARD_BEARER_TOKEN"] = "test-..."` (mirror `webhook_secret`).
- **Auth-gate-before-DB tests** (lines 57-88): the three `401`-without/with-bad-secret cases prove the gate fires pre-DB. **Replicate these for `require_bearer`** — missing header → 401, wrong token → 401, both before any DB work. This is the single most important pattern to copy (it is the security contract).
- **`@pytest.mark.integration`** on DB-dependent endpoint tests (lines 91, 172, 192); list/aggregation smoke tests assert shape (`isinstance(r.json(), list)` line 197; required-keys assertions lines 177-184).

---

## Pattern Assignments — Frontend (no in-repo analog)

**There is no existing Next.js/React/TanStack/MSW code in this repo.** `dashboard/` contains only `.gitkeep`. For every frontend file the planner must follow:

- **`04-UI-SPEC.md`** — the locked design contract: shadcn new-york/neutral/CSS-vars preset, Geist Sans+Mono, dark-default dense ops theme, the §7.6 `ScoreBreakdownPanel` / `ReviewPanel`+`QueueList` / `StatusBadge` bespoke components, PT-BR copy table, masked-phone LGPD rule, the exact shadcn block allowlist (Registry Safety table).
- **`04-RESEARCH.md` §5** — current stack & patterns: Next 16 App Router server/client boundary, Route Handlers as the BFF tier, `QueryClientProvider` wrapping interactive queues, `useMutation` + `onMutate` optimistic re-score + `onSettled → invalidateQueries(['dlq'])`, `useQuery({ refetchInterval })` for monitor polling, TanStack Table v8 for queues, Recharts 3 via shadcn `chart`, Vitest 4 + MSW 2 `setupServer` (Node) offline tests with success/empty/error/401 cases, `bunx vitest` runner.
- **`04-RESEARCH.md` §6** — the vertical-slice order (Foundation/auth → DLQ → Monitor → Gate → Cost → Funnels+Conversations); each slice pairs its UI with its backend endpoint (R1).

**Do not fabricate a frontend analog.** The only cross-repo *discipline* to carry (not code to copy) is: (1) the backend's constant-time, fail-closed auth posture → the BFF validates the browser Bearer before forwarding; (2) the collector's "100% offline by default" mandate → MSW mocks the network, no real FastAPI in the default suite (D-07).

---

## Shared Patterns

### Authentication (constant-time, fail-closed, never-logged)
**Source:** `require_steward` in `brave/api/routers/dlq.py` lines 25-47; `StewardConfig` in `brave/config/settings.py` lines 113-127.
**Apply to:** the new `require_bearer` dependency, `DashboardConfig`, the either-or mutation guard, and the BFF Route Handler's browser-token check.
```python
expected = config.secret
if not provided or not expected:                 # fail-closed: unset secret rejects all
    raise HTTPException(401, detail="... required")
if not hmac.compare_digest(provided, expected):  # constant-time
    raise HTTPException(401, detail="Invalid ...")
# secret never logged; 401 returned BEFORE any DB work
```

### Read-aggregation endpoint shape
**Source:** `brave/api/routers/metrics.py` lines 15-45 (counts + `group_by`), `brave/api/routers/audit.py` lines 13-43 (windowed/ordered + pagination), `brave/api/routers/dlq.py` lines 50-83 (filtered list + response-dict).
**Apply to:** every new `dashboard.py` GET (monitor/cost/funnels/conversations/DLQ-detail). Single counts via `db.scalar(select(func.count(...)))`; grouped via `db.execute(select(col, func.count/sum(...)).group_by(col)).fetchall()`; pre-seed `{v: 0 for v in VALUES}` so empty groups return 0; coerce `Numeric` → `float(... or 0)` in the dict (DLQ list line 78, metrics line 39).

### 404 / not-found
**Source:** `dlq.py` lines 100-102 / 147-149; `atrativos_gate.py` lines 209-210, 297-298.
**Apply to:** every detail endpoint — `obj = db.get(Model, id); if obj is None: raise HTTPException(404, ...)`.

### Audit-write on any state change
**Source:** `write_audit(session=db, action=..., entity_type=..., record_id=..., before_state=..., after_state=..., actor=...)` — `dlq.py` lines 116-123, 184-192; `atrativos_gate.py` lines 251-259. Imported from `brave.observability.audit`.
**Apply to:** mutations are reused (not rewritten), so no new audit calls are needed for approve/reject/validate. New read endpoints write **no** audit rows. (Only relevant if Option-B conversation logging is adopted.)

### Offline-by-default testing
**Source (backend):** `tests/integration/test_fastapi_endpoints.py` — TestClient, secret-via-env fixtures, `@pytest.mark.integration`, pre-DB 401 tests.
**Source (frontend discipline, no code):** mirror as Vitest 4 + MSW 2 — `setupServer`, mock FastAPI/BFF responses (success/empty/error/401), no real network (D-07, RESEARCH §5).

### JSONB mutation safety (only if mutating Rio JSON)
**Source:** `dlq.py` lines 153-161 — `normalized = dict(rio.normalized or {}); rio.normalized = normalized; flag_modified(rio, "normalized")`. SQLAlchemy does not auto-track in-place JSON mutations.
**Apply to:** N/A for read endpoints; relevant only if a new endpoint writes a JSON column (it should not — mutations are reused).

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| All of `dashboard/**` (Next.js app, BFF route handlers, React components, MSW/Vitest tests) | route / component / provider / test | request-response / CRUD / polling | **Greenfield.** No Next.js/React/TanStack/MSW code exists in this repo (`dashboard/` is `.gitkeep` only). Planner follows `04-UI-SPEC.md` + `04-RESEARCH.md` §5/§6, not an in-repo analog. |
| `conversation_message` model + Alembic migration (RESEARCH §4 Option B) | model + migration | event-driven append-log | No append-only conversation table exists; transcripts currently live in LangGraph `checkpoints`/`checkpoint_blobs` (un-friendly to read). Closest *posture* analog is `ConsentLog` (`models.py` lines 348-398: our-own-table, FK to `rio_records`, LGPD-minimized). **Open planner decision (R2):** Option A (decode LangGraph checkpoints) vs Option B (new log table). |
| Conversation transcript read endpoint (`GET /api/v1/conversations/{rio_id}`) | router | read | Data source is TBD pending the §4 A-vs-B decision; once decided it follows the same `dashboard.py` read-aggregation + `require_bearer` + masked-PII pattern as the rest. |

---

## Metadata

**Analog search scope:** `brave/api/routers/` (all 7 routers), `brave/api/main.py`, `brave/api/deps.py`, `brave/config/settings.py`, `brave/core/models.py`, `tests/integration/test_fastapi_endpoints.py`. Confirmed `dashboard/` greenfield and no `.claude/skills`/`.agents/skills` present.
**Files scanned:** 9 source/test files (1,805 lines) + 3 phase docs (CONTEXT/RESEARCH/UI-SPEC).
**Pattern extraction date:** 2026-06-16

## PATTERN MAPPING COMPLETE
