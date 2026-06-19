---
phase: 08-ops-cms-destinos-atrativos-crud-process-observability-cores
reviewed: 2026-06-18T00:00:00Z
depth: standard
files_reviewed: 28
files_reviewed_list:
  - brave/api/main.py
  - brave/api/routers/cms.py
  - brave/api/routers/workers.py
  - dashboard/app/atrativos/[id]/page.tsx
  - dashboard/app/atrativos/page.tsx
  - dashboard/app/destinos/[id]/page.tsx
  - dashboard/app/destinos/page.tsx
  - dashboard/app/globals.css
  - dashboard/app/page.tsx
  - dashboard/app/processo/__tests__/processo.test.tsx
  - dashboard/app/processo/page.tsx
  - dashboard/components/cms/AtrativoList.tsx
  - dashboard/components/cms/DestinoList.tsx
  - dashboard/components/cms/DetailPanel.tsx
  - dashboard/components/cms/JourneyStepper.tsx
  - dashboard/components/cms/StageBadge.tsx
  - dashboard/components/cms/__tests__/AtrativoList.test.tsx
  - dashboard/components/cms/__tests__/DestinoList.test.tsx
  - dashboard/components/cms/__tests__/test-utils.tsx
  - dashboard/components/processo/FailuresPanel.tsx
  - dashboard/components/processo/WorkerBoard.tsx
  - dashboard/lib/atrativos-api.ts
  - dashboard/lib/destinos-api.ts
  - dashboard/lib/workers-api.ts
  - dashboard/mocks/handlers/atrativos.ts
  - dashboard/mocks/handlers/destinos.ts
  - dashboard/mocks/handlers/workers.ts
  - tests/test_cms_endpoints.py
  - tests/test_workers_endpoints.py
findings:
  critical: 1
  warning: 7
  info: 4
  total: 12
status: issues_found
---

# Phase 8: Code Review Report

**Reviewed:** 2026-06-18
**Depth:** standard
**Files Reviewed:** 28 (+ cross-referenced deps.py, state_machine.py, dlq/service.py, pipeline.py, schemas.py, models.py, beat_schedule.py)
**Status:** issues_found

## Summary

Reviewed the Phase 8 ops CMS (Destinos/Atrativos CRUD) plus process-observability surface. The four focus areas held up well overall:

- **Bearer/steward auth** is enforced via `Depends(require_bearer)` / `Depends(require_steward_or_bearer)` on every endpoint in `cms.py` and `workers.py`, with constant-time `hmac.compare_digest` and fail-closed defaults in `deps.py`. Auth tests cover the 401-before-DB path. No auth bypass found.
- **FSM 409-conflict** correctness is sound: `advance_atrativo_state` delegates to `advance_sub_state(lock=True)` which re-fetches under `SELECT ... FOR UPDATE` before the guard and returns `False` on mismatch, mapped to a 409. Identity-map reuse means the route's `rio.sub_state` reflects the locked write.
- **flag_modified** usage is correct in both `edit_destino` and `edit_atrativo` (reassign-then-`flag_modified`, no in-place mutation).
- **LGPD phone masking** is correct for the actual data shape: `phone_e164` is only ever stored under `normalized["contacts"]` (contact_finder_agent.py), and `_safe_normalized` masks exactly that path.

However there is **one BLOCKER**: `_safe_normalized` masks only `phone_e164` but the contacts dict it returns (verbatim) also contains the owner's `email` (ContactResult schema), and both the list and detail endpoints ship the whole contacts object to the dashboard — leaking owner email PII that the data-minimization contract (R3) is supposed to gate. Several WARNINGs cover a Celery-before-commit race in promote, fabricated/undercounted observability numbers, and a descarte path that does not unpublish already-promoted Mar records.

## Critical Issues

### CR-01: Owner email PII leaked to dashboard — `_safe_normalized` masks only `phone_e164`

**File:** `brave/api/routers/cms.py:58-74` (helper), `:441` (list), `:499` (detail)
**Issue:** `_safe_normalized` pops/masks only `contacts["phone_e164"]` and returns the rest of the contacts dict unchanged. The `ContactResult` schema (`brave/lanes/atrativos/schemas.py:110-135`) persists `email`, `ig_handle`, and `website` alongside `phone_e164` under `normalized["contacts"]`. Both atrativo response paths return that whole dict:

- list: `"contacts_summary": _safe_normalized(rio.normalized).get("contacts")` → returns `{website, email, ig_handle}` (email is the owner's personal contact email).
- detail: `"normalized": _safe_normalized(rio.normalized)` → contacts sub-dict with `email` intact.

The frontend `AtrativoListItem.contacts_summary` type (`dashboard/lib/atrativos-api.ts:33-37`) only declares `phone_masked` + `website`, so `email` is silently transmitted but undocumented. Under LGPD R3 (data minimization), the owner's email is PII and is not gated by the masking contract. The PII tests (`tests/test_cms_endpoints.py:364-414`) only assert `phone_e164` absence; they do not catch the email leak.

**Fix:** Build an explicit allow-listed contacts summary instead of returning the raw dict. Mask/drop every non-website contact field:
```python
def _safe_contacts(contacts: dict | None) -> dict:
    c = dict(contacts or {})
    out = {"website": c.get("website")}
    if "phone_e164" in c:
        out["phone_masked"] = mask_phone(c.get("phone_e164"))
    if c.get("email"):
        out["email_masked"] = _mask_email(c["email"])  # or omit entirely
    return out
```
Apply it in both list (`contacts_summary`) and detail (`normalized["contacts"]`) paths, and add a test asserting the raw owner email never appears in either response.

## Warnings

### WR-01: `promote_destino` dispatches Celery task before the request transaction commits

**File:** `brave/api/routers/cms.py:245-254`
**Issue:** `validate_and_promote_rio(db, rio)` only `flush()`es (`brave/core/dlq/service.py:41,49`) — it does not commit. The route then dispatches `push_destination_task.delay(str(rio_id))` while still inside the request transaction; `get_db` commits only after the handler returns. `push_destination_task`/`push_mar` open their own session and early-return when `rio.routing != "mar"` (`brave/tasks/pipeline.py:424-425`). If the worker picks up the task before the request transaction commits, it reads the pre-promote state and silently no-ops, so the push to norteia-api is dropped. This is a real read-before-commit race in production (broker present); offline tests never exercise it because `.delay()` raises and is swallowed.
**Fix:** Commit before dispatch, then enqueue:
```python
validate_and_promote_rio(db, rio)
db.commit()
db.refresh(rio)
if rio.routing == "mar":
    try:
        push_destination_task.delay(str(rio_id))
    except Exception:
        pass
```
or enqueue via an after-commit hook.

### WR-02: `/failures` `total` is mislabeled — reports page size, not total quarantine count

**File:** `brave/api/routers/workers.py:107-108`
**Issue:** `"total": len(rows)` where `rows` is already capped by `.limit(limit)`. With more than `limit` quarantine rows, `total` reports `limit` (max 200), not the true count. The dashboard FailuresPanel (`dashboard/components/processo/FailuresPanel.tsx:56,64-68`) renders this as "Total: N falhas", so operators see an undercount during incident spikes — exactly when accuracy matters. The test (`tests/test_workers_endpoints.py:260-262`) even comments around this ambiguity instead of asserting the contract.
**Fix:** Compute the real total separately and rename the page-size field:
```python
total = db.scalar(select(func.count()).select_from(PoisonQuarantine)) or 0
...
return {"total": total, "returned": len(rows), "by_task": by_task, "items": [...]}
```

### WR-03: `by_task` counts only the returned page, not all failures

**File:** `brave/api/routers/workers.py:103-105`
**Issue:** `by_task` is computed from the `limit`-capped `rows`, so the per-task breakdown chips (`FailuresPanel.tsx:72-82`) reflect only the most recent `limit` rows, not the true distribution. Combined with WR-02 this makes the anomaly-detection breakdown unreliable above the page size.
**Fix:** Aggregate `by_task` with a grouped DB query independent of the page limit:
```python
by_task = dict(db.execute(
    select(PoisonQuarantine.task_name, func.count()).group_by(PoisonQuarantine.task_name)
).all())
```

### WR-04: `beat_schedule.entries` is a fabricated literal, not live schedule data

**File:** `brave/api/routers/workers.py:77`
**Issue:** The endpoint returns a hardcoded `{"entries": 54, "queues": ["brave.sweep"]}`. The real schedule is built dynamically in `brave/tasks/beat_schedule.py` (2 entries per UF in `UF_LIST`), so `54` happens to match today but drifts silently the moment `UF_LIST` changes. The dashboard (`WorkerBoard.tsx:149-153`) presents this as live observability ("N entradas agendadas"). The test (`tests/test_workers_endpoints.py:249-251`) hardcodes the same `54`, freezing the coupling.
**Fix:** Derive from the real schedule:
```python
from brave.tasks.beat_schedule import BRAVE_BEAT_SCHEDULE
"beat_schedule": {
    "entries": len(BRAVE_BEAT_SCHEDULE),
    "queues": ["brave.sweep"],
},
```

### WR-05: `descarte_destino` does not unpublish an already-promoted Mar record

**File:** `brave/api/routers/cms.py:273-299`
**Issue:** Descarte sets `routing="descarte"` / `dlq_reason="steward_rejected"` but never touches the existing `MarRecord` or notifies norteia-api. A destino that already reached Mar (and was pushed downstream) can be descartado in the CMS while the canonical record remains published in norteia-api. The detail endpoint will then show `routing="descarte"` for a record still live in "Mar", violating the core invariant that only trustworthy records remain in Mar. The AlertDialog copy in the UI ("não será publicado no Mar") implies unpublish semantics that the backend does not deliver.
**Fix:** Either block descarte when a `MarRecord` exists (409 + guidance to use a retract flow) or implement a retract path that removes/flags the Mar record and pushes a delete/depublish to norteia-api. Document the chosen semantics.

### WR-06: Human-pending tiles and funnel silently cap at 500 items

**File:** `dashboard/app/processo/page.tsx:47,59,64-65`
**Issue:** `dlqTotal = dlqItems?.length` and `gateTotal = gateItems?.length` derive counts from a list fetched with `limit=500` (`fetchDlqList(undefined, undefined, 500)`, `fetchGateQueue(undefined, 500)`). Beyond 500 rows the "DLQ pendente" / "Gate WhatsApp" tiles undercount with no indication, and the funnel (`buildFunnelData`) is likewise truncated. For a 24/7 all-Brazil collector these queues can exceed 500.
**Fix:** Expose a count/total from the DLQ and gate endpoints (or a dedicated counts endpoint) and render that, rather than `array.length` of a capped list. At minimum, show "500+" when the returned length equals the limit.

### WR-07: `reprocess_destino` writes an audit row even when reprocess silently fails

**File:** `brave/api/routers/cms.py:322-340`
**Issue:** The `try` enqueues `reprocess_record_task.delay(...)`; the broad `except Exception` falls back to a synchronous `reprocess_record`. If the synchronous fallback itself raises (e.g., DB/config error), it propagates — but in the broker-present path, `.delay()` can succeed while the worker later fails, and the endpoint unconditionally writes a `dlq_reprocessed` audit row and returns 202. The audit log therefore asserts a reprocess that may never have happened. Also, the bare `except Exception` masks import/runtime errors that are not "broker absent."
**Fix:** Narrow the fallback to the broker-connection error class, and only write the audit row after the chosen path is confirmed dispatched (or annotate the audit row with the dispatch mode). Avoid swallowing unrelated exceptions.

## Info

### IN-01: Unused imports in CMS list tests

**File:** `dashboard/components/cms/__tests__/AtrativoList.test.tsx:1,9`; `dashboard/components/cms/__tests__/DestinoList.test.tsx:1`
**Issue:** `render` is imported from `@testing-library/react` but never used (only `renderWithClient` is used). `sampleAtrativos` is imported in AtrativoList.test.tsx but unused.
**Fix:** Remove the unused imports; lint (`ruff`/eslint) should flag these.

### IN-02: `vi` used without import (relies on `globals: true`)

**File:** `dashboard/components/cms/__tests__/DestinoList.test.tsx:60`
**Issue:** `vi.fn()` is called but `vi` is not imported, while `describe/it/expect/beforeEach` ARE explicitly imported on line 2. This works only because `vitest.config.ts` sets `globals: true`, but the mixed style (explicit imports for some globals, implicit for `vi`) is inconsistent and fragile.
**Fix:** Either import `vi` explicitly alongside the others, or drop the explicit vitest imports and rely on globals consistently.

### IN-03: `StageBadge` sub_state lookup is case-sensitive while routing is normalized

**File:** `dashboard/components/cms/StageBadge.tsx:94,98,107,110`
**Issue:** `routing` is looked up via `routing.toLowerCase()` (defensive), but `subState` is used raw in `SUB_STATE_CLASS[subState]` and `toTitleCase(subState)`. If the backend ever returns a sub_state with unexpected casing it falls through to the muted default silently. Minor inconsistency, not a bug today (backend emits lowercase).
**Fix:** Apply the same normalization to `subState` for consistency, or document that sub_state casing is contract-guaranteed.

### IN-04: JourneyStepper marks the terminal step "completed" (green ✓) for descarte/rejected paths

**File:** `dashboard/components/cms/JourneyStepper.tsx:139-145,176-178`
**Issue:** For `routing === "descarte"` (and gate-rejected atrativos), `completedSteps.add(6)` plus `currentIdx = 6` renders the "Mar / DLQ" terminal step with the green "completed" check and `--status-mar` color — visually identical to a successful Mar promotion. A rejected/descartado record reads as "successfully completed" in the journey. Not a data bug, but a misleading status affordance in the ops UI.
**Fix:** Distinguish terminal outcomes — use a rejected/neutral style (e.g., `--status-descarte`) when `routing` is `descarte`/`dlq` at the terminal step, rather than the success green.

---

## Narrative Findings (AI reviewer)

All findings above are narrative findings from direct code review. No `<structural_findings>` block was provided with this review, so there is no fallow structural substrate to reconcile.

---

_Reviewed: 2026-06-18_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
