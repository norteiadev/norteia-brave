---
phase: 03-atrativos-lane-whatsapp-compliance
reviewed: 2026-06-15T00:00:00Z
depth: standard
files_reviewed: 20
files_reviewed_list:
  - brave/lanes/atrativos/discovery_agent.py
  - brave/lanes/atrativos/contact_finder_agent.py
  - brave/lanes/atrativos/signal_agent.py
  - brave/lanes/atrativos/state_machine.py
  - brave/lanes/atrativos/whatsapp_agent.py
  - brave/lanes/atrativos/schemas.py
  - brave/compliance/gate.py
  - brave/compliance/consent_log.py
  - brave/compliance/quality_rating.py
  - brave/clients/places.py
  - brave/clients/apify.py
  - brave/clients/whatsapp.py
  - brave/clients/null_whatsapp.py
  - brave/api/routers/atrativos_gate.py
  - brave/api/main.py
  - brave/core/models.py
  - brave/core/rio/routing.py
  - brave/config/settings.py
  - brave/tasks/pipeline.py
  - alembic/versions/0004_consent_log.py
findings:
  critical: 4
  warning: 9
  info: 5
  total: 18
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-06-15
**Depth:** standard
**Files Reviewed:** 20
**Status:** issues_found

## Summary

Reviewed the Phase 3 Atrativos lane (Discovery → ContactFinder → Signal FSM, the LangGraph WhatsApp conversation, the D-11 send-path compliance gate, LGPD consent/opt-out logging, the steward-authed gate endpoints, and the Celery tasks).

The architecture invariants the brief asked me to verify mostly hold structurally: `send_template` is called only inside `_compliant_send` (grep-verified), `_compliant_send` always calls `send_path_gate` first, the ramp counter uses INCR-before-call with DECR-on-breach, and the mutating gate endpoints (`/approve`, `/reject`) carry `require_steward`. However, several defects undermine the compliance guarantees those structures are meant to deliver:

1. **The opt-out keyword detector produces false positives** that silently opt out legitimate contacts (substring match of `"NÃO"`/`"PARAR"` etc.). This is an LGPD-data-correctness bug that wrongly suppresses contacts.
2. **The quality-rating RED auto-pause can be written to an ephemeral fakeredis instance** when production Redis is briefly unreachable, so the pause flag never reaches the gate — sends continue while quality is RED (BSP violation).
3. **The send-path gate's `contact_phone` is read from `rio.normalized["contact_phone"]` in `outreach_task`, but the discovery/contact pipeline stores the phone under `normalized["contacts"]["phone_e164"]`** — so production outreach sends to an empty phone and the consent record is written for an empty phone, breaking the suppression key.
4. **The FSM idempotency guards rely on session-level mutation without row locking**, and the LangGraph nodes mutate a `rio` object captured at task entry while a separate `AsyncPostgresSaver` connection commits checkpoints — concurrent inbound webhooks for the same `rio_id` can double-send.

Details below.

## Critical Issues

### CR-01: Opt-out keyword detection uses substring match — false-positive opt-outs (LGPD data corruption)

**File:** `brave/lanes/atrativos/whatsapp_agent.py:255-259`
**Issue:** Opt-out is detected with `if kw in upper_text` against `OPT_OUT_KEYWORDS = {"SAIR","PARAR","CANCELAR","REMOVER","STOP","NÃO"}`. This is an unanchored substring match. Common legitimate PT-BR replies trigger false opt-outs:
- `"NÃO sei o horário, mas estamos abertos"` → contains `"NÃO"` → **opted out**
- `"Não vamos parar de funcionar"` → contains both `"NÃO"` and `"PARAR"` → **opted out**
- `"Pode cancelar minha dúvida anterior"` → contains `"CANCELAR"` → **opted out**

Because `record_opt_out` is append-only and "NEVER unset" (T-03-03-07), a false positive permanently suppresses a valid contact and routes the record to DLQ with `dlq_reason="owner_opted_out"`. This is silent data loss of exactly the high-value owner-validated records the pipeline exists to capture, and it mis-records LGPD opt-out state.
**Fix:** Match opt-out only when the message *is* a bare keyword (after stripping punctuation/whitespace), not when it merely contains one:
```python
import re
# normalize: strip, uppercase, drop surrounding punctuation, collapse spaces
normalized_words = set(re.findall(r"[A-ZÁ-Ú]+", upper_text))
detected_keyword = next((kw for kw in OPT_OUT_KEYWORDS if kw in normalized_words), None)
# Optionally also accept exact full-string match: upper_text.strip(" .!,") in OPT_OUT_KEYWORDS
```
Whole-word/standalone matching is the BSP-standard opt-out semantics. Keep "NÃO" only if a standalone "NÃO" reply is intended as opt-out; otherwise drop it (it is far too common a word to treat as opt-out).

### CR-02: Quality-rating RED pause can be lost when Redis is transiently down (BSP auto-pause bypass)

**File:** `brave/api/deps.py:96-112`, `brave/api/routers/atrativos_gate.py:266-304`, `brave/compliance/quality_rating.py:43-65`
**Issue:** `get_redis()` caches a module-global client and, on **any** exception during `Redis.from_url(...).ping()`, silently falls back to `fakeredis.FakeRedis()` and caches it for the process lifetime. The quality-rating webhook calls `set_quality_flag(get_redis(), "RED")`. If production Redis has a momentary blip at the instant the webhook (or the first redis access in the process) runs, the RED flag is written to an in-process fakeredis that the Celery workers never see. The compliance gate (condition 8, `is_quality_red`) reads from the *workers'* Redis, which has no flag. Result: sends continue while Meta/Twilio quality is RED — a BSP violation that risks number suspension, exactly the failure the auto-pause exists to prevent. The fakeredis fallback also means the RED flag is lost on every process restart with no TTL/recovery path.
**Fix:** Do not silently fall back to fakeredis in any code path that participates in compliance enforcement. Fakeredis fallback (if kept at all) must be gated on an explicit dev/test flag, and must fail-closed in production:
```python
def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        client = Redis.from_url(url, socket_connect_timeout=1)
        client.ping()           # let it raise in prod — do NOT swallow
        _redis_client = client
    return _redis_client
```
Additionally, a RED quality signal should fail-closed: if the gate cannot reach Redis to check `wa:quality_red`, it must block the send, not pass.

### CR-03: outreach_task reads contact_phone from the wrong normalized key — sends to empty phone, breaks consent/suppression

**File:** `brave/tasks/pipeline.py:945`, `brave/lanes/atrativos/contact_finder_agent.py:102-106`, `brave/lanes/atrativos/whatsapp_agent.py:175,189-196`
**Issue:** `outreach_task` builds the initial state with `contact_phone = (rio.normalized or {}).get("contact_phone", "")`. But `ContactFinderAgent` stores the phone at `normalized["contacts"] = contact.model_dump()`, i.e. under `normalized["contacts"]["phone_e164"]`. There is no `normalized["contact_phone"]` key anywhere in the pipeline. Consequences in production (`run_real_externals=True`):
- `contact_phone` is `""`.
- `_send_opening_node` calls `write_consent_record(phone_e164="")` — the LGPD consent row, and therefore the entire suppression/opt-out key, is keyed on the empty string.
- `send_path_gate` condition 1 finds the empty-phone consent row (it exists) and passes; `_compliant_send` then calls `wa_client.send_template(to="", ...)`.
- Every opted-out check and `lookup_rio_id_by_phone` for inbound replies is keyed on the empty string, so inbound routing and opt-out suppression collapse across all records.

This is a correctness + LGPD-suppression defect: the gate's "legal basis recorded" and "opt-out honored" conditions become meaningless because they all key on `""`.
**Fix:** Source the phone from the contacts sub-dict and abort if absent:
```python
contacts = (rio.normalized or {}).get("contacts") or {}
contact_phone = contacts.get("phone_e164") or ""
if not contact_phone:
    # no reachable owner — route to DLQ instead of dispatching an empty send
    rio.routing = "dlq"; rio.dlq_reason = "no_contact_phone"; session.commit()
    return
```
Also harden `send_path_gate` to reject an empty `contact_phone` explicitly (a consent row keyed on `""` must never satisfy condition 1).

### CR-04: Concurrent inbound webhooks for the same rio_id can double-send (no FSM row lock; out-of-band session mutation)

**File:** `brave/tasks/pipeline.py:1019-1118`, `brave/lanes/atrativos/state_machine.py:54-69`, `brave/api/routers/atrativos_gate.py:312-359`
**Issue:** The inbound webhook dispatches `resume_conversation_task` with no de-duplication, and the task's only idempotency guard is `if rio.sub_state != "whatsapp_in_progress": return` (an unlocked read). Two inbound replies arriving close together (owner double-taps, or Twilio re-delivers) produce two concurrent tasks; both read `sub_state == "whatsapp_in_progress"`, both resume the LangGraph from the same checkpoint, and both can execute `_ask_followup_node`/`_send_opening`-style sends through the gate. The ramp counter (CR-04 reserve-before-call) limits *total* volume but does not prevent two follow-ups to the *same* owner in the same window. Worse, the gate's `sub_state` check (condition 6) reads from `rio` loaded by the task, while the conversation state/turn count lives in the AsyncPostgresSaver checkpoint on a *different* connection — so the turn-count guard (`max_turns`) and the SQLAlchemy `sub_state` guard are not transactionally coupled. `advance_sub_state` likewise does a bare `if rio.sub_state != expected_state` with no `SELECT ... FOR UPDATE`, so it is not safe under concurrency despite being documented as the idempotency point (D-01).
**Fix:** Lock the row before the guard in every FSM-advancing task and in `advance_sub_state`:
```python
rio = session.get(RioRecord, rio_uuid, with_for_update=True)  # SELECT ... FOR UPDATE
if rio.sub_state != "whatsapp_in_progress":
    return
```
For the conversation, also serialize per-thread (e.g. a short-lived Redis lock keyed `lock:atrativo:{rio_id}` around `graph.ainvoke`) so two checkpoint resumes cannot interleave sends.

## Warnings

### WR-01: Tenacity retries every exception including non-retryable client errors

**File:** `brave/clients/whatsapp.py:127-132`, `brave/clients/places.py:103-108,156-161`, `brave/clients/apify.py:78-83`
**Issue:** All four real clients use `retry=retry_if_exception_type(Exception)` — i.e. retry on *everything*. The whatsapp client's docstring claims "tenacity wraps 5xx Twilio errors", but the predicate retries 4xx auth failures, `ComplianceError` (if it ever propagated here), malformed-request 400s, and `RuntimeError`. Retrying a 401/400 three times with exponential backoff wastes time, can compound rate-limit pressure, and for WhatsApp can re-attempt a send that should fail fast. `places.py` even defines an unused `_is_retryable` helper (line 37) that is never wired into the `@retry` predicate.
**Fix:** Use the existing predicate functions: `retry=retry_if_exception(_is_twilio_5xx)` for whatsapp, `retry=retry_if_exception(_is_retryable)` for places, and a similarly narrowed predicate for apify. Remove the dead `_is_retryable` wiring gap in places.py.

### WR-02: Bare `except Exception: pass` swallows Celery dispatch failures in gate endpoints

**File:** `brave/api/routers/atrativos_gate.py:187-193,351-357`, `brave/tasks/pipeline.py` (multiple `except ... pass` blocks at 306-309, 311-316, 826-829, 831-836)
**Issue:** `/approve` flips `sub_state` to `whatsapp_in_progress`, then `try: outreach_task.delay(...) except Exception: pass`. If the broker is down in production (not just tests), the record is silently left in `whatsapp_in_progress` with no outreach ever dispatched and no error surfaced — it is now invisible to the gate queue (`GET` only lists `aguardando_consulta_whatsapp`) and stuck forever. The same swallow pattern appears for `resume_conversation_task` and in `push_mar`/`push_attraction_task` after max retries (`pass # Phase N adds DLQ`), where permanently failed production pushes vanish with no DLQ.
**Fix:** Distinguish "no broker in tests" from "broker down in prod". Gate on `run_real_externals` / an explicit test flag, and log at error level when dispatch fails in a real environment. For the push tasks, route permanently failed pushes to a retry DLQ rather than `pass` (the comments acknowledge this is deferred — at minimum it must log).

### WR-03: quality_rating_webhook and inbound webhook are unauthenticated mutating endpoints

**File:** `brave/api/routers/atrativos_gate.py:266-304,312-359`
**Issue:** Both POST endpoints mutate compliance/conversation state with no authentication and only a "Production TODO: add Twilio signature validation" comment. `quality-rating-webhook` lets any caller who can reach the service set/clear the global send-pause flag — an attacker can clear a legitimate RED pause (resume sends during a quality incident) or set a spurious RED (DoS the outreach pipeline). `inbound` lets any caller inject arbitrary `body` text into a victim's conversation by spoofing `from`, which the LLM then extracts and which can trigger `owner_confirmed` promotion to Mar or a forced opt-out. "Behind FastAPI, not public-facing without infra auth" is an assumption, not an enforced control.
**Fix:** Implement Twilio `RequestValidator` signature verification (or a shared-secret header mirroring `require_steward`) before parsing the payload — not as a deferred TODO. These are the two endpoints that most need authentication because they directly drive the compliance gate and the promotion path.

### WR-04: 24h-window check in recv_reply_node is computed but never enforces; gate reads a key the agent never writes

**File:** `brave/lanes/atrativos/whatsapp_agent.py:278-292`, `brave/compliance/gate.py:228-235`
**Issue:** `_recv_reply_node` computes `window_open` by comparing `last_inbound_at` to now and returns it in the state update. But the gate's condition 5 reads `window_open` from `rio.normalized.get("window_open", True)` — a key that is **never written** to `rio.normalized` anywhere in the lane (only to LangGraph state). So the gate always sees the default `True` and the 24h window is effectively never enforced for follow-ups. Additionally the window math is wrong in concept: comparing `last_inbound_at` to "now" right after setting `last_inbound_at = now` always yields `0s < 86400` → open; the window should be measured at *send time* against the last *inbound* message, not recomputed to now each turn.
**Fix:** Persist the window state the gate actually reads (write `rio.normalized["window_open"]` / `last_inbound_at` via `flag_modified`), and compute the window at the moment of send against the genuine last-inbound timestamp. Otherwise condition 5 is dead code that gives false assurance.

### WR-05: _next_utc_midnight can raise ValueError on month/year boundaries

**File:** `brave/compliance/gate.py:57-66`
**Issue:** `tomorrow = tomorrow.replace(day=tomorrow.day + 1)` throws `ValueError: day is out of range for month` on the last day of any month (e.g. `replace(day=32)` on Jan 31, `day=29/30/31` on Feb). This is the TTL set on the ramp counter on the first send of each day; on a month-end day, the **first** send of the day raises inside `check_and_increment_ramp` after the INCR. Because the exception propagates (not a ComplianceError), the send is aborted but the counter was already incremented and never DECR'd — the daily count is permanently inflated by one per failed attempt, and month-end outreach is broken.
**Fix:** Use `timedelta` arithmetic, which the function already imports for the branch:
```python
from datetime import timedelta
tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
return tomorrow
```

### WR-06: outreach_task / resume mutate a session-bound `rio` across an asyncio.run boundary, then commit — sync session used inside event loop

**File:** `brave/tasks/pipeline.py:880-968,1042-1118`, `brave/lanes/atrativos/whatsapp_agent.py` (all nodes call `session.flush()`)
**Issue:** A synchronous SQLAlchemy `Session` (created by `_get_session()`) is captured in `build_graph` closures and used (`session.flush()`, `select(...).scalar()`, `reprocess_record`) from inside `async def` nodes driven by `asyncio.run(_run())`. Mixing a sync psycopg session with `await`ed graph execution on the same thread is fragile: the AsyncPostgresSaver opens its own async connection to the same DB, and the sync session's flushes are interleaved with async checkpoint writes with no shared transaction. If the graph awaits between a `flush()` and the outer `session.commit()`, partial state (consent row written, send recorded, but sub_state not advanced) can be committed out of order or left dangling on an exception path that rolls back the sync session but not the checkpoint.
**Fix:** Either use an async session for the nodes, or restructure so all DB mutations happen in the sync task body (before/after `asyncio.run`) and the graph nodes return pure state deltas. At minimum, document and test the failure mode where the checkpoint commits but the sync session rolls back (duplicate-send on retry).

### WR-07: finalize_node uses `rio.uf` via getattr but parent-destino linkage / municipio not validated; reprocess uses stale captured rio

**File:** `brave/lanes/atrativos/whatsapp_agent.py:542`, `brave/core/rio/routing.py:222-234`
**Issue:** `_finalize_node` calls `_reprocess(session, uuid.UUID(state["rio_id"]), score_config)`, which re-`session.get`s the record — good — but the node then continues to mutate and log the *captured* `rio` object (from `build_graph`), not `reprocessed_rio`, for everything except the routing read. The two are the same identity only if the captured `rio` is still attached to the same session; after `asyncio.run` boundaries and intermediate flushes this is not guaranteed. Mixing the captured `rio` and the freshly-fetched `reprocessed_rio` invites reading stale `routing`/`normalized`.
**Fix:** Operate on a single freshly-fetched record inside the node (re-`session.get` at the top of finalize, mutate that), and never reference the closure-captured `rio` for post-reprocess reads.

### WR-08: corroboracao always returns 40 for any non-empty Apify dict (dead conditional)

**File:** `brave/lanes/atrativos/signal_agent.py:320-330`
**Issue:** `_compute_corroboracao` computes `has_followers` and `has_posts`, then returns 40.0 if `has_followers or has_posts or len(ig_data) > 0`. Since the function already returned 0.0 for empty `ig_data`, any non-empty dict has `len(ig_data) > 0 == True`, so the `has_followers`/`has_posts` computation is dead — a profile with `followers=0` and no posts (e.g. a found-but-inactive account, or an error-shaped dict) still scores full corroboração. Note also `has_posts` reads `"post_count"` while the apify client writes `"posts_count"` (line 148) — a key mismatch making that branch always-false regardless.
**Fix:** Decide the real signal (e.g. require `followers > 0` or a recent `last_post`) and drop the `or len(ig_data) > 0` catch-all. Fix the `post_count` vs `posts_count` key mismatch.

### WR-09: write_consent_record always inserts a new row — duplicate consent rows on retry, and gate condition 1 can pass after opt-out

**File:** `brave/lanes/atrativos/whatsapp_agent.py:189-196`, `brave/compliance/consent_log.py:38-93,96-116`
**Issue:** `_send_opening_node` calls `write_consent_record` unconditionally on every entry, and the function docstring states it "always creates a NEW row". If `outreach_task` is retried (acks_late + reject_on_worker_lost guarantees redelivery on worker loss) the opening node runs again and inserts a second consent row for the same phone. More importantly: `is_opted_out` returns True if *any* row has `opted_out=True`, but `write_consent_record` always inserts a fresh `opted_out=False` row — so gate condition 1 (`legal_basis_row is not None`, queried without an opted_out filter) will find the new non-opted row and pass, while condition 3 (`is_opted_out`) finds the old opted-out row and blocks. The ordering saves it *today*, but the data model now holds contradictory rows (opted-out + active) for one phone, and any future query that picks "the latest row" (as `record_opt_out`/`lookup_rio_id_by_phone` do via `order_by first_contact_at desc`) can resurrect an opted-out contact into an active conversation.
**Fix:** Make `write_consent_record` upsert (check for an existing row for the phone; if it exists and is opted-out, refuse to create a new active row and abort the outreach). Never create a new active consent row for a phone that already has an opted-out row.

## Info

### IN-01: Unused/`noqa` re-export and unused local in gate

**File:** `brave/tasks/pipeline.py:83`, `brave/compliance/gate.py:254,260`
**Issue:** `quarantine_poison` is re-exported with `# noqa: F401` (acceptable but worth a comment), and in `send_path_gate` `uf = getattr(rio, "uf", None)` is computed, immediately ignored (`_ = uf`), with `check_and_increment_ramp(..., uf=None)` always called global. The per-UF code path is entirely dead. Either wire it or delete the dead local + comment block (lines 254-260).
**Fix:** Remove the unused `uf` capture and the `_ = uf` line, or implement the per-UF layer.

### IN-02: `_seconds_until_midnight` is defined but never used

**File:** `brave/compliance/gate.py:69-73`
**Issue:** Dead helper — the ramp TTL uses `expireat(_next_utc_midnight())`, never the seconds fallback the docstring references.
**Fix:** Delete it, or use it as the documented EXPIREAT fallback.

### IN-03: AtrativoResult.origem_value/completude_value carried in schema but DiscoveryAgent hardcodes payload values

**File:** `brave/lanes/atrativos/schemas.py:89-102`, `brave/lanes/atrativos/discovery_agent.py:289,285`
**Issue:** `AtrativoResult` has `origem_value`/`completude_value` fields with defaults, but DiscoveryAgent ignores `result.origem_value`/`result.completude_value` and instead hardcodes `"origem_value": 60.0` and recomputes completude via `_compute_completude`. The schema fields are misleading dead surface (and instructor will spend tokens generating them).
**Fix:** Drop the two `_value` fields from `AtrativoResult` (they are pipeline-computed, not LLM-extracted), or actually consume them.

### IN-04: place_details requests fields the result normalization never reads (formatted_phone_number)

**File:** `brave/clients/places.py:180-189`, `brave/lanes/atrativos/contact_finder_agent.py:89-90`
**Issue:** ContactFinder reads `details.get("formatted_phone_number")` first, but `RealPlacesClient.place_details` only ever returns `international_phone_number` (no `formatted_phone_number` key) — the field mask requests `internationalPhoneNumber` only. The `formatted_phone_number` branch is always None in production; only the fallback works. Harmless but confusing.
**Fix:** Drop the `formatted_phone_number` lookup, or add the field to the mask + result dict.

### IN-05: Magic numbers for window/age thresholds

**File:** `brave/lanes/atrativos/whatsapp_agent.py:283` (`86400`), `brave/lanes/atrativos/signal_agent.py:101-104` (30/180 days)
**Issue:** 24h window (`86400`) and atualidade day-bands (30/180) are inline literals duplicated across files (also `_seconds_until_midnight`). Centralize for calibration (the §7.6 thresholds already live in ScoreConfig).
**Fix:** Move to named constants / config so the window and age bands are tunable in one place.

---

_Reviewed: 2026-06-15_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
