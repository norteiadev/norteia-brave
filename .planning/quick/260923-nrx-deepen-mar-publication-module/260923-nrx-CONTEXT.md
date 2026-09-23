# Quick Task 260923-nrx: Deepen the Mar publication module - Context

**Gathered:** 2026-09-23 (architecture review + grilling session with the user)
**Status:** Ready for planning — ALL decisions below are LOCKED, do not revisit.

<domain>
## Task Boundary

Replace the scattered "promote a Rio record into Mar, then push it to norteia-api" flow with ONE
deep module, `brave/core/mar/publication.py`. Today the flow spans 7+ modules: routers
(cms.py, atrativos.py, dlq.py) each repeat `validate_and_promote_rio → write_audit → commit →
refresh → if routing=="mar": push_*_task.delay → 503 if run_real_externals`; three near-identical
Celery push tasks in brave/tasks/pipeline.py re-run `promote_to_mar` before POSTing; the
"commit before dispatch" invariant (WR-01) lives only in comments.

Vocabulary: module, interface, implementation, depth, seam, adapter, leverage, locality.
</domain>

<facts>
## Verified facts (from exploration)

- `push_mar` (pipeline.py ~550) has ZERO production callers — dead.
- `push_destination_task` (~698) and `push_attraction_task` (~2186) differ only in endpoint + log names.
- Both push tasks re-run `promote_to_mar` (idempotent) before POST.
- Outbox already exists implicitly: Mar rows with `pushed_at IS NULL` are pending; beat
  `brave.repush_pending_mar` (15 min) calls `dispatch_pending_pushes` (pipeline.py ~515);
  engine router `routers/engine.py:162` imports `dispatch_pending_pushes` from tasks (Painel "Reenviar").
- `_mark_pushed` uses `isinstance(api_client, NorteiaApiClient)` to decide stamping (seam leak).
  Real client requires `async with`; Null does not.
- `_norteia_api_down()` builds Redis from os.environ inside tasks; `brave/core/mar/sync.py` has `norteia_api_up(redis)`.
- Only two doors into Mar (ENG-05, pipeline.py ~2888): human DLQ gate (steward) and WhatsApp
  owner finalize. Both set `validacao_humana_value=100` then re-score via `reprocess_record`.
- WhatsApp finalize (`brave/shared/whatsapp/agent.py` ~600-650) writes `owner_horarios`/`owner_valor`,
  sets validacao_humana=100, reprocesses, and calls injected `push_confirmed_fn` (= push_attraction_task.delay,
  passed at pipeline.py ~2394 and ~2585). It does NOT call promote_to_mar itself — the push task did.
- `brave/core/dlq/service.py::validate_and_promote_rio` = validacao_humana=100 + flag_modified + reprocess + promote_to_mar if mar.
- `brave/cli.py` calls promote_to_mar directly (dev tool; fixture already has validacao_humana=100).
- D-18 kernel rule (core/shared must not import tasks/lanes/domains) enforced by tests/unit/test_domain_boundaries.py.
- Router call sites: cms.py ~439-480 and ~588-660 (push_destination_task), dlq.py ~232-270 and ~317-345,
  atrativos.py ~123-190 (single) and ~250-330 (promote_bulk: per-record commit, never raises, returns push_failed list).
</facts>

<decisions>
## Implementation Decisions (LOCKED)

Q1. The module owns audit + commit + enqueue. It takes `actor`; routers just read the result.
Q2. The publish task NEVER promotes: it reads the active MarRecord, builds payload, hash-compare, POST, stamp.
Q3. Dispatch failure (broker down) never fails the request: result carries `push_queued=False`;
    the outbox (pushed_at NULL + 15-min beat + Painel Reenviar) recovers. Remove the 503 paths.
Q4. Delete `push_mar`, `push_destination_task`, `push_attraction_task`. One task `brave.publish_mar(rio_id)`
    picks the endpoint by entity_type. No aliases for old names (outbox re-dispatches in-flight ones).
Q5. Scope: all steward entries (cms, dlq, atrativos single + bulk) + WhatsApp finalize + cli.py.
Q6. Lives at `brave/core/mar/publication.py` (kernel). Enqueue is injected as `Callable[[str], None]`
    (same pattern as push_confirmed_fn). Routers get it via a FastAPI dependency (brave/api/deps.py)
    that returns `publish_mar.delay`; tests override with an in-memory list.
Q7. Interface — three verbs:
    - `promote(session, rio, *, actor, enqueue) -> Promotion` (Promotion: routing, mar_id, held_reason,
      push_queued). Does validacao_humana=100, re-score, promote_to_mar (with 90-day backstop), audit,
      commit, then enqueue if mar. No "automatic promotion" flag (ENG-05: every Mar entry is human-validated).
    - `publish(session, rio_id, api) -> Published` — body of the brave.publish_mar task.
    - `republish_pending(session, enqueue) -> int` — replaces dispatch_pending_pushes; engine router stops importing tasks.
    Bulk candidate selection (min_score, require_description) stays in the router, which loops `promote`.
Q8. WhatsApp writes owner_horarios/owner_valor itself, then calls `promote(actor="whatsapp_owner")`.
Q9. promote commits inside the WhatsApp graph node — accepted (no commit=False flag).
Q10. Both norteia-api adapters expose `async push(entity_type, payload) -> bool`. Real returns True after
     2xx and owns its HTTP lifecycle internally (no `async with` at call site). Null returns False.
     Module stamps push_hash/pushed_at only on True. `isinstance` disappears.
Q11. Real adapter checks the cached health probe (core/mar/sync.norteia_api_up) before POST and raises
     `ApiDown`; publish treats ApiDown as "stays pending" (no retry burn). Null is never down.
Q12. Tests: replace, don't layer. New `tests/unit/test_mar_publication.py` through the interface with
     list-enqueue + fake adapter: promoted, held by backstop, held by score, dispatch fails (push_queued=False),
     same hash skips POST, Null doesn't stamp, ApiDown stays pending. Delete task tests that patch
     `_get_session` for push tasks (test_push_destination_task.py, push-task parts of test_mar_push.py,
     test_push_hash_skip.py → moved into publication tests). Router tests assert the promote result,
     not `.delay`. Pact contract test unchanged. Update test_celery_task_registration.py.
Q13. Create repo-root `CONTEXT.md` (domain glossary) with: Nascente, Rio, Mar, DLQ, **Promoção**
     (synchronous, human-validated entry into Mar), **Publicação** (async send to norteia-api),
     **Pendente** (Mar row with pushed_at NULL — the outbox).

### Claude's Discretion
- Exact dataclass shapes of Promotion / Published; retry policy of brave.publish_mar (keep max_retries=3
  and PermanentError-logs behaviour of today's push tasks).
- Whether `_build_push_payload`/`_push_hash`/`_mark_pushed`/`_norteia_api_down`/`_http_error_body` move
  into publication.py or are deleted (pass-throughs should be deleted).
- Keep router HTTP response shapes backward compatible for the dashboard where feasible
  (bulk still returns push_failed = ids with push_queued False).
</decisions>

<testing>
## Test environment (MANDATORY — never touch the operator's real DB/Redis)

```
export BRAVE_DB_URL='postgresql+psycopg://brave:brave@localhost:5432/norteia_brave_test'
export BRAVE_DB_REDIS_URL='redis://localhost:6379/15'
unset RUN_REAL_EXTERNALS
../../../.venv/bin/python -m pytest -q -p no:cacheprovider
```
(venv lives in the main checkout; run from the worktree root.) NEVER point tests at `norteia_brave`
or Redis db 0 — the live stack uses them.

Baseline at base commit: exactly 8 pre-existing failures —
test_atrativos_chain_e2e::test_chain_stops_at_dlq_no_auto_gate,
test_atrativos_lane_e2e::test_sc2_discovery_skips_absent_parent_destino,
and 6 in tests/integration/test_engine_endpoints.py (409/noop). Anything else failing = regression.
</testing>
