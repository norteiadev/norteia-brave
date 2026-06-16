---
phase: 04-dashboard-territorial-cms
reviewed: 2026-06-16T22:55:24Z
depth: standard
files_reviewed: 31
files_reviewed_list:
  - alembic/versions/0005_conversation_message.py
  - brave/api/deps.py
  - brave/api/main.py
  - brave/api/routers/atrativos_gate.py
  - brave/api/routers/dashboard.py
  - brave/api/routers/dlq.py
  - brave/compliance/gate.py
  - brave/config/settings.py
  - brave/core/models.py
  - brave/tasks/pipeline.py
  - dashboard/app/api/[...path]/route.ts
  - dashboard/lib/auth.ts
  - dashboard/lib/api-client.ts
  - dashboard/lib/dlq-api.ts
  - dashboard/lib/gate-api.ts
  - dashboard/lib/cost-api.ts
  - dashboard/lib/monitor-api.ts
  - dashboard/lib/funnels-api.ts
  - dashboard/lib/conversations-api.ts
  - dashboard/components/dlq/dlq-actions.ts
  - dashboard/components/dlq/QueueList.tsx
  - dashboard/components/dlq/ReviewPanel.tsx
  - dashboard/components/dlq/ScoreBreakdownPanel.tsx
  - dashboard/components/gate/gate-actions.ts
  - dashboard/components/gate/GateQueue.tsx
  - dashboard/components/gate/RampContext.tsx
  - dashboard/components/conversations/ConversationList.tsx
  - dashboard/components/conversations/TranscriptPanel.tsx
  - dashboard/components/cost/useCost.ts
  - dashboard/components/monitor/useMonitor.ts
findings:
  critical: 3
  warning: 6
  info: 5
  total: 14
status: issues_found
---

# Phase 4: Code Review Report

**Reviewed:** 2026-06-16T22:55:24Z
**Depth:** standard
**Files Reviewed:** 31
**Status:** issues_found

## Summary

Phase 4 delivers a Next.js operations dashboard (Territorial CMS), a thin read-only FastAPI dashboard router, an additive `conversation_message` append-only log, and a Bearer-at-the-edge auth surface. The BFF proxy, the constant-time compares, and the migration chaining are sound. The cost/monitor/funnels read endpoints are genuinely read-only.

Three BLOCKER-class defects undercut the milestone's two stated invariants (LGPD/no-raw-phone and read-only/correctness):

1. **The WhatsApp gate queue endpoint (`GET /api/v1/atrativos/gate`) is completely unauthenticated AND returns the raw `normalized` dict containing `contacts.phone_e164`** — a raw E.164 phone number. This is simultaneously an auth-bypass and an LGPD PII leak on the exact path the review brief flagged as PII-sensitive. The dashboard's own `gate-api.ts`/UI claim "the backend masks phone_e164 server-side" — it does not.
2. **The conversation append-only logger (`_log_conversation_messages`) drops or mis-orders inbound replies** because it slices the graph message list by a persisted-row count while also appending the inbound turn out of position — the append-only idempotency math is wrong, so transcripts will silently lose owner replies (the LGPD-minimized audit record the dashboard reads).
3. **The same logger trusts `messages[already_logged:]` to align persisted rows with graph turns**, but the graph's `messages` list and the persisted-row count can diverge (inbound prepend, partial prior commit), producing duplicate or skipped rows on retry — violating the append-only "no duplicate / no drop" contract.

Warnings cover an `int()` crash path on corrupted Redis ramp counters, an inconsistent `monitor.alerts.failures` (unwindowed count behind a windowed block), a misleading docstring on `require_bearer` ("mirrors require_steward exactly" while behaving differently), the gate-context endpoint silently swallowing all errors in the UI, and a TanStack optimistic-update key-scoping bug. Info items cover dead/duplicated code and naming.

## Critical Issues

### CR-01: WhatsApp gate queue endpoint is unauthenticated and leaks raw phone_e164 (auth bypass + LGPD)

**File:** `brave/api/routers/atrativos_gate.py:124-174`
**Issue:**
`list_whatsapp_gate_queue` has **no auth dependency** (only `db: Session = Depends(get_db)`), unlike every other dashboard read endpoint in `dashboard.py` which carries `dependencies=[Depends(require_bearer)]`. Anyone who can reach the FastAPI port (or the BFF, which forwards GETs) gets the queue with no token.

Worse, the response includes `"normalized": r.normalized or {}` (line 171). The Rio `normalized` dict stores the raw contact phone at `normalized["contacts"]["phone_e164"]` — confirmed in `contact_finder_agent.py:106` (`new_normalized["contacts"] = contact.model_dump()`) and `brave/tasks/pipeline.py:56-57` (`_extract_contact_phone` reads `normalized["contacts"]["phone_e164"]`). So this endpoint emits the raw E.164 number in cleartext.

This directly contradicts the dashboard's own contract: `dashboard/lib/gate-api.ts:17-19` states "the backend masks `phone_e164` server-side. The UI receives only `phone_masked`". It does not — the UI's `maskedPhoneFrom()` looks for a `phone_masked` key that the backend never produces here, so either the phone is exposed raw to anyone who inspects the payload, or it silently shows nothing. The PII is in the wire payload regardless of what the UI renders.

**Fix:**
1. Add the Bearer guard to match the rest of the dashboard surface:
```python
@router.get("/api/v1/atrativos/gate", dependencies=[Depends(require_bearer)])
def list_whatsapp_gate_queue(...):
```
2. Never emit the raw `normalized` dict. Either strip the contacts block or mask it before returning:
```python
from brave.core.models import mask_phone

def _safe_normalized(normalized: dict | None) -> dict:
    n = dict(normalized or {})
    contacts = n.get("contacts")
    if isinstance(contacts, dict) and "phone_e164" in contacts:
        contacts = dict(contacts)
        contacts["phone_masked"] = mask_phone(contacts.pop("phone_e164", None))
        n["contacts"] = contacts
    return n
# ...
"normalized": _safe_normalized(r.normalized),
"phone_masked": mask_phone((r.normalized or {}).get("contacts", {}).get("phone_e164")),
```

---

### CR-02: Conversation logger drops/mis-orders inbound replies — append-only "no drop" contract violated

**File:** `brave/tasks/pipeline.py:96-142` (`_log_conversation_messages`)
**Issue:**
The function builds `messages` from `final_state["messages"]`, then conditionally appends the inbound `reply_text` to the **end** of that list (lines 101-107), then persists `messages[already_logged:]` (line 119).

Two correctness failures:

1. **Inbound reply mis-ordered / dropped.** On resume, LangGraph's checkpointed `final_state["messages"]` already contains the full accumulated turn history (all prior outbound asks + the inbound that the graph itself appended + the follow-up). The code appends the inbound `reply_text` to the *end* only when no turn with `role=="user" and content==inbound_text` exists. But if the graph appended the inbound in the *middle* (its real chronological position) and then a follow-up outbound after it, the `any(...)` check passes and the inbound is logged in the correct place — fine. However if the graph did NOT append it (the documented fallback case), the inbound is appended *after* the follow-up outbound, so the persisted transcript shows the owner's reply AFTER Norteia's response to it — a corrupted, out-of-order transcript.

2. **Slice misalignment loses turns.** `already_logged` counts rows already in `conversation_message` for this rio_id. `messages` is the graph's current full list. These two are only equal if every prior graph turn was persisted 1:1 in order. The outreach write-point logs only OUTBOUND asks from its final state; the resume write-point then sees a different `messages` length (graph history) than `already_logged` (rows written). If `len(messages) < already_logged` (e.g., graph state was trimmed, or a prior partial commit wrote more rows than the current graph exposes), `messages[already_logged:]` is empty and **new turns are silently dropped**; if it's larger by coincidence, turns get duplicated.

**Fix:**
Stop using positional slicing against a count. Track what is logged by a stable key (turn index in the thread, or a content+direction+role hash), and reconcile against persisted rows. Minimum viable fix — persist the inbound explicitly at the resume write-point independent of the graph list, and key idempotency on (rio_id, sequence):
```python
# Persist inbound first, deterministically, before reading graph turns:
if inbound_text and not _already_logged(session, rio_id, "inbound", inbound_text):
    session.add(ConversationMessage(rio_id=..., direction="inbound", role="user",
                                    content=inbound_text, phone_masked=phone_masked))
# Then append only graph OUTBOUND turns not already persisted, matched by content,
# not by positional slice.
```
Add a unit test that resumes twice with the same `reply_text` and asserts exactly one inbound row plus correct chronological order.

---

### CR-03: Append-only idempotency keyed on a row count, not on identity — duplicates/skips on retry

**File:** `brave/tasks/pipeline.py:109-142`
**Issue:**
`acks_late=True` + `max_retries=3` means `outreach_task` / `resume_conversation_task` can run the body more than once (worker lost after `graph.ainvoke` but before/after commit, or a retry). The idempotency guard for conversation logging is `already_logged = count(rows for rio_id)` then `new_turns = messages[already_logged:]`.

If the task partially committed (the model comment at `models.py:440-442` claims "never orphaned/uncommitted", but `session.commit()` at `pipeline.py:1122`/`1292` is a single commit that can be retried after a transient post-commit failure) OR the graph returns a different-length `messages` on the replay (LangGraph checkpoint advanced), the count-based slice does not correspond to "turns not yet logged". Result: on replay with a shorter `messages` list, `messages[already_logged:]` is empty (silent skip); on replay where the graph re-emits turns, rows duplicate. There is no unique constraint on `conversation_message` to backstop this (migration `0005` adds only a non-unique `ix_conversation_message_rio_id`).

**Fix:**
Make append idempotent by identity, not by count. Options:
- Add a `turn_seq INTEGER` column + unique `(rio_id, turn_seq)` constraint and write with `ON CONFLICT DO NOTHING`.
- Or dedupe before insert by `(rio_id, direction, role, content)` existence check.
Either makes a re-run a true no-op regardless of `messages` length drift. This is the only structural guarantee of the stated append-only "idempotent retry" contract (`pipeline.py:73-80`).

## Warnings

### WR-01: `int(raw)` on ramp/quality counters can crash the read-only ramp-context and monitor endpoints

**File:** `brave/api/routers/atrativos_gate.py:232,256` and `brave/compliance/gate.py:130`
**Issue:**
`used = int(raw)` (line 232) and `uf_used = int(uf_raw)` (line 256) assume the Redis value parses as an int. A corrupted/manually-set key, or a key written by a different type, raises `ValueError`, turning the read-only advisory endpoint into a 500. The brief requires the ramp-context to be advisory/non-blocking; an uncaught `ValueError` here breaks the gate panel for the operator during exactly the incident window they need it. The frontend `RampContext.tsx:41-48` only handles `isError` gracefully, but a 500 from the API still degrades the panel to "indisponível" instead of showing the real counter.
**Fix:**
```python
def _safe_int(raw) -> int:
    try:
        return int(raw) if raw is not None else 0
    except (ValueError, TypeError):
        return 0
used = _safe_int(raw)
```

### WR-02: `monitor.alerts.failures` is an all-time count behind a windowed block (misleading metric)

**File:** `brave/api/routers/dashboard.py:193`
**Issue:**
`get_monitor` is documented and shaped as a rolling-window read (`since_hours`, `window_start`), and `rates`/`throughput` correctly filter on `created_at >= window_start`. But `failures = db.scalar(select(func.count(PoisonQuarantine.id)))` counts **every** poison row ever recorded, with no window filter. The operator sees a monotonically growing "failures" alert that never reflects the selected window, making the alert useless for "is something failing right now". `PoisonQuarantine` has a `quarantined_at` column available for the filter.
**Fix:**
```python
failures = db.scalar(
    select(func.count(PoisonQuarantine.id)).where(
        PoisonQuarantine.quarantined_at >= window_start
    )
) or 0
```

### WR-03: `require_bearer` docstring claims it "mirrors require_steward exactly" but the contract differs

**File:** `brave/api/deps.py:56-74`
**Issue:**
The docstring says it mirrors `require_steward` "exactly, swapping the header". It does not: `require_steward` (dlq.py:25) returns a 401 with `detail="X-Steward-Secret header required"`; `require_bearer` parses `Authorization: Bearer ...` via `removeprefix("Bearer ")`. A header of `Authorization: Basic xyz` is not rejected as malformed — `removeprefix` is a no-op, leaving `token="Basic xyz"`, which then fails the `compare_digest`. Functionally fail-closed (good), but the "exactly mirrors" claim is false and will mislead the next maintainer into assuming header-shape validation that isn't there. A reviewer relying on the docstring would not notice that a non-`Bearer` scheme is silently treated as a token guess rather than a 400/malformed.
**Fix:** Either require the `Bearer ` prefix explicitly (reject otherwise) or correct the docstring to state the actual behavior. Prefer explicit:
```python
if not authorization or not authorization.startswith("Bearer "):
    raise HTTPException(401, "Authorization: Bearer token required")
token = authorization[len("Bearer "):].strip()
```

### WR-04: RampContext silently swallows ALL errors (including 401) as a generic "indisponível"

**File:** `dashboard/components/gate/RampContext.tsx:41-48`
**Issue:**
`if (query.isError || !query.data)` collapses every failure — 401 session-expired, 500, network — into the same dashed "Contexto de ramp/qualidade indisponível." box. Every other view in this phase (QueueList, GateQueue, ConversationList, TranscriptPanel) distinguishes 401 to drive the re-login flow. Here a 401 looks like a benign data gap, so the operator keeps approving outreach with a stale/blank ramp+quality panel and never learns their session expired. Given the panel's entire purpose is showing the RED auto-pause state, masking a 401 as "indisponível" is a safety regression.
**Fix:** Branch on `error instanceof ApiError && error.status === 401` and surface the session-expired message, consistent with the sibling components; keep the dashed fallback only for non-auth advisory failures.

### WR-05: Optimistic validate update is scoped to one (uf, entityType) key; the invalidate is broad — UI flicker / stale rows

**File:** `dashboard/components/dlq/dlq-actions.ts:51-82`
**Issue:**
`useValidateDlqRecord` optimistically removes the row from `dlqKeys.list(uf, entityType)` only. But `QueueList` keys its query on `dlqKeys.list(uf, entityType)` where `uf` changes as the operator clicks UF tabs. If the optimistic mutation was triggered from one UF view and the cache holds other UF lists, only the current key is patched. The `onSettled` `invalidateQueries(['dlq'])` then refetches everything — correct eventually — but between `onMutate` and refetch settle, any other mounted `['dlq','list',...]` observer (e.g., a second pane) shows the just-validated row as still present, then it pops out on refetch. Minor, but it's a correctness-of-optimism gap the comment ("the row is removed from the visible queue immediately") overstates. Also: if `previous` is `undefined` (query not yet cached), `onError` rollback is a no-op and a failed validate leaves the row removed-then-refetched, a visible flicker.
**Fix:** Either drop the optimistic removal and rely on the (already present) broad invalidate, or apply the optimistic filter across all cached `['dlq','list',...]` entries via `qc.getQueriesData({ queryKey: ['dlq','list'] })` and snapshot/restore each in the context.

### WR-06: `quality_rating_webhook` calls `get_redis()` directly instead of the injected dependency — breaks test override + ignores fail-closed contract

**File:** `brave/api/routers/atrativos_gate.py:454`
**Issue:**
`quality_rating_webhook` takes `db` via DI but calls `redis = get_redis()` directly (not `redis: Redis = Depends(get_redis)`). This bypasses FastAPI's dependency-override mechanism, so a test that overrides `get_redis` to a fakeredis instance does not affect this handler — it constructs a fresh client via the module-level singleton. Since this is the endpoint that **sets the RED auto-pause flag**, a test asserting "RED webhook pauses sends" can pass against a different Redis than the gate reads, masking a real wiring bug. (Compare: `get_ramp_context` and `get_monitor` correctly inject `redis`.)
**Fix:** `def quality_rating_webhook(payload: dict, db: Session = Depends(get_db), redis: Redis = Depends(get_redis)):` and drop the inline `get_redis()` call.

## Info

### IN-01: Duplicate `require_steward` definitions across routers

**File:** `brave/api/routers/atrativos_gate.py:62-85` and `brave/api/routers/dlq.py:25-47`
**Issue:** Two byte-identical `require_steward` functions (the docstrings even say "copied verbatim"). With `require_steward_or_bearer` now centralized in `deps.py`, both local copies are dead for the mutation routes (they use the centralized either-or guard). The remaining standalone `require_steward` in `atrativos_gate.py` is not referenced by any route in the file.
**Fix:** Delete the unused local `require_steward` copies; import from a single source if still needed.

### IN-02: `queryKeys` in api-client.ts is dead / superseded

**File:** `dashboard/lib/api-client.ts:78-82`
**Issue:** `queryKeys` (`health`, `dlq`, `dlqDetail`) duplicates and conflicts with the per-slice key factories (`dlqKeys`, `gateKeys`, `monitorKeys`, etc.). The `dlq`/`dlqDetail` shapes here differ from `dlqKeys.list`/`dlqKeys.detail`, so accidental use would split the cache. It appears unused by the reviewed components.
**Fix:** Remove `queryKeys` or reduce it to `{ health }` and delete the conflicting DLQ entries.

### IN-03: Duplicated `qs()` and `UF_PRIORITY` across data layers

**File:** `dashboard/lib/dlq-api.ts:71-78`, `dashboard/lib/gate-api.ts:76-83`, `dashboard/lib/funnels-api.ts` (inline), `dlq-api.ts:21` + `gate-api.ts:25`
**Issue:** The `qs()` query-string helper and the `UF_PRIORITY` constant are copy-pasted in multiple modules. Divergence risk (one gets a fix the other doesn't).
**Fix:** Hoist `qs()` and `UF_PRIORITY` into a shared `lib/query.ts` / `lib/uf.ts`.

### IN-04: Dead `final_extraction` attach logic only ever fires on the last outbound turn

**File:** `brave/tasks/pipeline.py:96-132`
**Issue:** `final_extraction` is attached only when `is_last and direction == "outbound"`. In the outreach write-point there is no extraction yet (`extraction=None` in initial_state), and in resume the last turn may be inbound, so the extraction snapshot is frequently `None` even when the graph produced one. This is a latent data-completeness gap in the transcript's `extracted` column rather than a crash, but the dashboard renders `extracted` per `TranscriptPanel.tsx:145-149` and will usually show nothing.
**Fix:** Attach `final_extraction` to the conversation's most recent OUTBOUND row regardless of slice position, or store it on a dedicated column.

### IN-05: `validate_batch` re-imports modules inside the per-row loop

**File:** `brave/api/routers/dlq.py:230-243`
**Issue:** `flag_modified`, `ScoreConfig`, and `reprocess_record` are imported inside the `for rio in rows` loop (lines 232, 240-241), re-executing the import machinery on every iteration. Functionally correct (import cache makes it cheap) but it's a code smell and obscures the dependency surface. Not a perf finding per scope — flagged as quality only.
**Fix:** Hoist the three imports above the loop.

---

_Reviewed: 2026-06-16T22:55:24Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
