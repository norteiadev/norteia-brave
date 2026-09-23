---
phase: quick-260923-nrx
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - brave/core/mar/publication.py
  - brave/shared/exceptions.py
  - brave/clients/base.py
  - brave/clients/norteia_api.py
  - brave/clients/null_norteia_api.py
  - tests/fakes/fake_norteia_api.py
  - tests/unit/test_mar_publication.py
  - brave/tasks/pipeline.py
  - brave/tasks/__init__.py
  - brave/core/mar/sync.py
  - brave/api/deps.py
  - brave/api/routers/cms.py
  - brave/api/routers/dlq.py
  - brave/api/routers/atrativos.py
  - brave/api/routers/engine.py
  - brave/shared/whatsapp/agent.py
  - brave/cli.py
  - brave/shared/dtos.py
  - brave/shared/whatsapp/__init__.py
  - tests/integration/test_mar_push.py
  - tests/integration/test_push_destination_task.py
  - tests/unit/test_push_hash_skip.py
  - tests/unit/test_celery_task_registration.py
  - tests/unit/test_engine_depth_gating.py
  - tests/unit/lanes/test_copy_batch.py
  - tests/unit/test_mar_sync.py
  - tests/unit/api/test_mar_repush.py
  - tests/unit/test_transitions.py
  - tests/unit/test_atrativos_promote_bulk.py
  - tests/integration/test_destinos_lane.py
  - tests/test_cms_endpoints.py
  - tests/integration/test_atrativos_lane_e2e.py
  - tests/unit/test_scaffold_smoke.py
  - tests/unit/lanes/test_whatsapp_agent.py
  - CONTEXT.md
autonomous: true
requirements: [QUICK-260923-nrx]

must_haves:
  truths:
    - "A steward promote (cms promote + transition, dlq validate + validate-batch, atrativos transition + promote-bulk) that crosses the gate lands in Mar, writes exactly one audit row, commits, and only THEN enqueues brave.publish_mar with the rio id"
    - "A broker outage never fails a promote request: no 503 path remains; the result carries push_queued=False and the Mar row stays pending (pushed_at NULL) for the 15-min beat / Painel Reenviar"
    - "brave.publish_mar never promotes: it reads the active MarRecord, skips the POST when the payload hash matches push_hash, stamps push_hash/pushed_at only when the adapter returns True, and leaves the row pending on ApiDown"
    - "WhatsApp owner confirmation writes owner_horarios/owner_valor, then goes through the same promote(actor='whatsapp_owner') and enqueues brave.publish_mar"
    - "Exactly one push task exists (brave.publish_mar); brave.push_mar, brave.push_destination, brave.push_attraction and dispatch_pending_pushes are gone"
    - "Kernel purity (tests/unit/test_domain_boundaries.py) stays green and the full suite shows only the 8 baseline failures"
  artifacts:
    - path: "brave/core/mar/publication.py"
      provides: "Promotion/Published result types + promote / publish / republish_pending"
      exports: ["Promotion", "Published", "promote", "publish", "republish_pending"]
    - path: "brave/clients/norteia_api.py"
      provides: "Real adapter push(entity_type, payload) -> bool, health-gated"
      contains: "async def push"
    - path: "brave/clients/null_norteia_api.py"
      provides: "Null adapter push -> False"
      contains: "async def push"
    - path: "brave/shared/exceptions.py"
      provides: "ApiDown error"
      contains: "class ApiDown"
    - path: "brave/api/deps.py"
      provides: "get_publish_enqueue dependency returning publish_mar.delay"
      contains: "def get_publish_enqueue"
    - path: "brave/tasks/pipeline.py"
      provides: "single brave.publish_mar task"
      contains: "name=\"brave.publish_mar\""
    - path: "tests/unit/test_mar_publication.py"
      provides: "interface tests for promote/publish/republish_pending"
      min_lines: 80
    - path: "CONTEXT.md"
      provides: "domain glossary (Nascente, Rio, Mar, DLQ, Promoção, Publicação, Pendente)"
      contains: "Publicação"
  key_links:
    - from: "brave/api/routers/{cms,dlq,atrativos}.py"
      to: "brave.core.mar.publication.promote"
      via: "enqueue injected by Depends(get_publish_enqueue)"
      pattern: "promote\\(.*enqueue"
    - from: "brave/api/deps.py"
      to: "brave.tasks.pipeline.publish_mar.delay"
      via: "lazy import inside get_publish_enqueue"
      pattern: "publish_mar\\.delay"
    - from: "brave/tasks/pipeline.py publish_mar"
      to: "brave.core.mar.publication.publish"
      via: "task body"
      pattern: "publish\\(session"
    - from: "brave/api/routers/engine.py + beat repush_pending_mar"
      to: "brave.core.mar.publication.republish_pending"
      via: "module-top import in engine.py (its other brave.tasks imports for UF_LIST, _cascade_search_client, engine_sweep_run stay)"
      pattern: "republish_pending\\("
    - from: "brave/shared/whatsapp/agent.py _finalize_node"
      to: "brave.core.mar.publication.promote"
      via: "actor='whatsapp_owner', enqueue=push_confirmed_fn"
      pattern: "whatsapp_owner"
    - from: "brave/clients/norteia_api.py push"
      to: "brave.core.mar.sync.norteia_api_up"
      via: "cached health probe before POST, raises ApiDown"
      pattern: "norteia_api_up"
---

<objective>
Collapse the scattered "promote Rio → Mar, then push to norteia-api" flow into ONE deep kernel
module, `brave/core/mar/publication.py`, with three verbs (promote / publish / republish_pending).
Routers, WhatsApp finalize, cli and the engine router call the module; one Celery task
`brave.publish_mar` replaces the three push tasks; both norteia-api adapters expose
`push(entity_type, payload) -> bool`; a repo-root CONTEXT.md glossary names the concepts.

Purpose: the WR-01 "commit before dispatch" invariant, the audit write and the push-hash/outbox
rules live in one place instead of seven; the `isinstance(NorteiaApiClient)` seam leak disappears.
Output: publication.py + tests, rewired callers, deleted pass-through tasks/tests, CONTEXT.md.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/quick/260923-nrx-deepen-mar-publication-module/260923-nrx-CONTEXT.md
@./CLAUDE.md

All decisions Q1–Q13 in CONTEXT.md are LOCKED. Discretion choices made by the planner are stated
inline below and marked "(discretion)".

<interfaces>
Existing code the executor builds on (verified during planning — no exploration needed):

brave/core/dlq/service.py
  validate_and_promote_rio(session, rio, config: ScoreConfig | None = None) -> MarRecord | None
    validacao_humana_value=100 + flag_modified + flush, reprocess_record, refresh,
    promote_to_mar if routing == "mar". Writes no audit, does not commit. KEEP it unchanged
    (the loadtest harness and several tests use it); promote() calls it.

brave/core/mar/service.py
  promote_to_mar(session, rio) -> MarRecord | None   (None = 90-day backstop routed to dlq,
    dlq_reason="no_recent_reviews"; idempotent by source_ref; supersession D-03)
  build_push_payload(mar_record, rio_record) -> dict  (rio_record unused, kept for signature)

brave/core/mar/sync.py
  norteia_api_up(redis=None) -> bool | None   (None = externals off / no URL; cached 30s)
  pending_push_rows(session, limit=500) -> list[(rio_id, entity_type)]   (active, pushed_at NULL)
  count_pending_pushes(session) -> int

brave/observability/audit.py
  write_audit(session, action, entity_type=None, record_id=None, before_state=None,
              after_state=None, actor="pipeline") -> AuditLog

brave/shared/exceptions.py: BraveError, TransientError(BraveError), PermanentError(BraveError)

brave/core/models.MarRecord columns used: id, rio_id, entity_type, source_ref, push_hash,
  pushed_at, superseded_by_id, published_at.

brave/tasks/pipeline.py (today, to be replaced):
  _http_error_body(exc) ~449 (KEEP, used by publish_mar retry log)
  _build_push_payload ~460 (shim → DELETE), _push_hash ~480 (→ move into publication),
  _mark_pushed ~487 (DELETE, isinstance leak), _norteia_api_down ~501 (KEEP for the beat),
  dispatch_pending_pushes ~515 (DELETE → republish_pending), repush_pending_mar ~533 (KEEP, rewire),
  push_mar ~549-655, push_destination_task ~698-805, push_attraction_task ~2181-2285 (DELETE all 3),
  build_graph(... push_confirmed_fn=push_attraction_task.delay ...) at ~2394 and ~2585.
  Existing digest: sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
  — publication MUST keep this exact formula so stored push_hash values stay valid (otherwise
  every Mar row re-POSTs once after deploy).

brave/api/routers/engine.py ~150-164: POST /api/v1/mar/repush — keeps its 503 when
  norteia_api_up(redis) is False, then lazily imports dispatch_pending_pushes from tasks (to remove).

Current audit vocabulary (dashboard RATE_ACTIONS counts "dlq_validated" — must not change):
  cms PATCH /destinos/{id}/promote → "dlq_validated" (any outcome)
  dlq PATCH /dlq/{id}/validate and POST /dlq/validate-batch → "dlq_validated" (any outcome)
  cms/atrativos transition promote edge + atrativos promote-bulk → "transition_mar" when in Mar,
    "promote_held" when held (bulk adds batch_id to after_state)
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: publication module + adapter push(entity_type, payload) + interface tests</name>
  <files>brave/core/mar/publication.py, brave/shared/exceptions.py, brave/clients/base.py, brave/clients/norteia_api.py, brave/clients/null_norteia_api.py, tests/fakes/fake_norteia_api.py, tests/unit/test_mar_publication.py, tests/integration/test_mar_push.py</files>
  <behavior>
    tests/unit/test_mar_publication.py (mark @pytest.mark.integration, use the global db_session
    fixture from tests/conftest.py against norteia_brave_test; build Nascente+Rio rows the way
    tests/integration/test_destinos_lane.py::_make_dlq_record does). enqueue = a plain list's
    .append; the fake adapter is a tiny in-file class with async push recording calls and a
    configurable return value / exception.
    - promoted: promotable destino → Promotion(routing="mar", mar_id set, held_reason None,
      push_queued True); enqueue list == [str(rio.id)]; one AuditLog row with the given action
      and actor; the Mar row is committed (visible from a fresh query after expire_all).
    - held by backstop: attraction with no most_recent_review_at → routing "dlq",
      held_reason "no_recent_reviews", mar_id None, enqueue list empty, audit action = held_action.
    - held by score: low-score record → routing != "mar", enqueue empty, push_queued False.
    - dispatch fails: enqueue raises RuntimeError → no exception escapes, push_queued False,
      routing "mar" and the Mar row stays committed with pushed_at NULL.
    - publish pushes: fake returns True → status "pushed", push_hash + pushed_at stamped,
      fake received (entity_type, payload).
    - same hash skips POST: second publish with unchanged data → status "unchanged", fake called once.
    - Null does not stamp: NullNorteiaApiClient → status "not_sent", push_hash and pushed_at stay None.
    - ApiDown stays pending: fake raises ApiDown → status "api_down", pushed_at stays None, no raise.
    - publish never promotes: publish on a rio with no active MarRecord → status "not_in_mar",
      no MarRecord created.
    - republish_pending: use a MagicMock session (same style as the old
      test_mar_sync dispatch test: session.execute.return_value.all.return_value =
      [(a, "attraction"), (d, "destination")]) → returns 2 and the enqueue list ==
      [str(a), str(d)]. Do NOT use db_session here: promote() commits, so db_session is not
      isolated and other pending rows may leak into the count.
    tests/integration/test_mar_push.py (respx, existing style): add two tests for the real
    adapter — push("attraction", payload) returns True on a 2xx without any `async with` at the
    call site; push raises ApiDown (and makes no POST) when brave.core.mar.sync.norteia_api_up is
    monkeypatched to return False.
  </behavior>
  <action>
    1. brave/shared/exceptions.py: add `class ApiDown(TransientError)` with a one-line docstring
       ("norteia-api confirmed down by the cached health probe; the push stays pending").
    2. Adapters (per Q10/Q11):
       - brave/clients/norteia_api.py: add a module dict of entity_type → path
         ("destination" → "/api/internal/territorial/destinations", "attraction" →
         "/api/internal/territorial/attractions"); make push_destination/push_attraction use it
         (keep both methods and __aenter__/__aexit__ unchanged in behaviour — the Pact test calls
         them and must stay untouched). Add optional constructor kwarg `redis: Any | None = None`.
         Add `async def push(self, entity_type, payload) -> bool`: if
         `sync.norteia_api_up(self._redis) is False` raise ApiDown (no POST) — import the module
         (`from brave.core.mar import sync`) and call it as a module attribute, NOT
         `from ... import norteia_api_up`, so the test's monkeypatch of
         brave.core.mar.sync.norteia_api_up reaches it;
         otherwise open its own lifecycle via `async with self:` and `await self._post(path, payload)`,
         return True. HTTP errors propagate (the task's retry handles them). Update the module
         docstring usage example to `await client.push("destination", payload)`.
       - brave/clients/null_norteia_api.py: replace both methods with `async def push(self,
         entity_type, payload) -> bool: return False` and update the docstring (never stamps).
       - brave/clients/base.py NorteiaApiClientProtocol: replace push_destination/push_attraction
         with the single `async def push(self, entity_type: str, payload: dict[str, Any]) -> bool`.
       - tests/fakes/fake_norteia_api.py: add `push(entity_type, payload) -> bool` that routes to
         the existing push_destination/push_attraction recorders and returns True (keeps the
         e2e tests that call the old methods working).
    3. brave/core/mar/publication.py (kernel — imports only brave.core / brave.shared /
       brave.config / brave.clients / brave.observability; NEVER brave.tasks, per D-18):
       - `@dataclass(frozen=True) Promotion`: routing: str, mar_id: uuid.UUID | None,
         held_reason: str | None, push_queued: bool. `@dataclass(frozen=True) Published`:
         status: str — one of "pushed" | "unchanged" | "not_sent" | "api_down" | "not_in_mar"
         (discretion).
       - `promote(session, rio, *, actor, enqueue, action="dlq_validated", held_action=None,
         extra=None, config=None) -> Promotion` (per Q1/Q7; `action`/`held_action`/`extra` are
         discretion so the existing audit vocabulary in <interfaces> is preserved exactly and the
         dashboard's dlq_validated rate is untouched; `config` lets bulk/WhatsApp reuse a loaded
         ScoreConfig). Steps: capture before_state {"routing", "score"}; call
         validate_and_promote_rio(session, rio, config); in_mar = returned MarRecord is not None;
         after_state {"routing", "score", **(extra or {})} plus "reason": rio.dlq_reason when held;
         write_audit(action if in_mar else (held_action or action), actor=actor, ...); capture
         routing / mar id / dlq_reason into locals; session.commit() (WR-01: commit BEFORE
         enqueue — say so in one comment); if in_mar call enqueue(str(rio.id)) inside try/except
         Exception → structlog error "publish_enqueue_failed" and push_queued=False (per Q3: a
         broker outage never fails the caller; the pending outbox recovers it). No "automatic
         promotion" flag (ENG-05).
       - `publish(session, rio_id, api) -> Published` (per Q2 — never promotes): load RioRecord;
         return "not_in_mar" if missing or rio.routing != "mar"; load the active MarRecord for that
         rio_id (superseded_by_id IS NULL, newest published_at first); "not_in_mar" if none; payload =
         build_push_payload(mar, rio); digest with the EXACT formula from <interfaces> (private
         `_digest`); if mar.push_hash == digest return "unchanged"; call
         `asyncio.run(api.push(mar.entity_type, payload))` catching ApiDown → "api_down"; on True
         stamp push_hash + pushed_at (datetime.now(UTC)) and commit → "pushed"; on False →
         "not_sent" (Null adapter; stamping would make the first real push a silent no-op — keep that
         rationale as one comment). Other exceptions propagate to the task.
       - `republish_pending(session, enqueue) -> int`: for each (rio_id, _) in
         sync.pending_push_rows(session) call enqueue(str(rio_id)); return the count (broker errors
         propagate, same as today's dispatch_pending_pushes).
       Module docstring: 5-8 lines naming Promoção (sync, human-validated entry into Mar) vs
       Publicação (async send) vs Pendente (pushed_at NULL outbox).
    4. Write the tests from <behavior> first (RED), then implement (GREEN).
  </action>
  <verify>
    <automated>cd /Users/leandro/Projects/norteia/norteia-brave/.claude/worktrees/mar-publication && export BRAVE_DB_URL='postgresql+psycopg://brave:brave@localhost:5432/norteia_brave_test' BRAVE_DB_REDIS_URL='redis://localhost:6379/15' && unset RUN_REAL_EXTERNALS && ../../../.venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_mar_publication.py tests/integration/test_mar_push.py tests/contract/test_pact_norteia_api.py tests/unit/test_domain_boundaries.py && ../../../.venv/bin/ruff check brave/core/mar/publication.py brave/clients/ brave/shared/exceptions.py tests/unit/test_mar_publication.py</automated>
  </verify>
  <done>All listed tests pass (Pact test file unmodified); publication.py exports the five names and imports nothing from brave.tasks; `grep -n "isinstance" brave/core/mar/publication.py` is empty; ruff clean.</done>
</task>

<task type="auto">
  <name>Task 2: single brave.publish_mar task + rewire every caller, delete the 3 push tasks</name>
  <files>brave/tasks/pipeline.py, brave/tasks/__init__.py, brave/core/mar/sync.py, brave/api/deps.py, brave/api/routers/cms.py, brave/api/routers/dlq.py, brave/api/routers/atrativos.py, brave/api/routers/engine.py, brave/shared/whatsapp/agent.py, brave/shared/whatsapp/__init__.py, brave/shared/dtos.py, brave/cli.py</files>
  <action>
    1. brave/tasks/pipeline.py (per Q4):
       - DELETE push_mar, push_destination_task, push_attraction_task, dispatch_pending_pushes,
         _build_push_payload, _push_hash, _mark_pushed. No aliases for old task names (the outbox
         re-dispatches any in-flight rows).
       - ADD a private `_norteia_api()` factory: when AppConfig().run_real_externals, return
         NorteiaApiClient(base_url=BRAVE_NORTEIA_API_URL, service_token=BRAVE_NORTEIA_API_SERVICE_TOKEN,
         redis=redis.from_url(BRAVE_DB_REDIS_URL default "redis://localhost:6379/0")); else
         NullNorteiaApiClient().
       - ADD `@shared_task(bind=True, max_retries=3, name="brave.publish_mar", acks_late=True,
         reject_on_worker_lost=True, time_limit=300) def publish_mar(self, rio_id: str) -> str`:
         session via _get_session(); return publish(session, uuid.UUID(rio_id), _norteia_api()).status;
         on Exception: rollback, `raise self.retry(exc=exc, max_retries=3)`, and on
         MaxRetriesExceededError log "publish_mar_max_retries_exceeded" with rio_id, error and
         response=_http_error_body(exc) (same WR-02 behaviour as today; discretion: the old
         PermanentError branch is dropped because nothing in publish raises it); finally close.
       - repush_pending_mar: keep the externals-off / _norteia_api_down() gates; replace the
         dispatch call with `republish_pending(session, publish_mar.delay)`.
       - Both build_graph call sites (~2394, ~2585): push_confirmed_fn=publish_mar.delay.
       - Update the module docstring (lines ~1-25) and the section comment above the old
         push_attraction_task to describe publish_mar; remove imports that become unused
         (hashlib/json/datetime etc. — let ruff tell you).
       - brave/tasks/__init__.py docstring: push_mar → publish_mar.
       - brave/core/mar/sync.py docstring: "push tasks" → "brave.publish_mar"; re-dispatch now lives
         in brave.core.mar.publication.republish_pending.
    2. brave/api/deps.py (per Q6): add `get_publish_enqueue() -> Callable[[str], None]` that lazily
       imports and returns brave.tasks.pipeline.publish_mar.delay (api layer may import tasks;
       lazy keeps app startup light). Tests override it via app.dependency_overrides.
    3. Routers (per Q1/Q3/Q5) — each promote site becomes: load rio (404 as today) → promote(...)
       → read the Promotion. Add parameter `enqueue: Callable[[str], None] =
       Depends(get_publish_enqueue)` to each affected endpoint. Import `promote` at module top
       (`from brave.core.mar.publication import promote`) so tests can patch
       `brave.api.routers.<mod>.promote`. Remove every broker-down 503 block and the
       now-redundant commit/refresh/write_audit around the promote. Responses stay backward
       compatible, adding `"push_queued"` where a single record is promoted.
       - cms.py promote_destino (~420): promote(db, rio, actor="steward", enqueue=enqueue) (action
         default "dlq_validated"); return {"status": "accepted", "rio_id", "routing": p.routing,
         "push_queued": p.push_queued}.
       - cms.py transition_destino promote edge (~586): promote(..., actor="steward",
         action="transition_mar", held_action="promote_held"); if p.routing != "mar" raise the same
         409 with detail using p.held_reason (or "reprovado"); else return {"status": "ok",
         "to": body.to} immediately (the module already audited + committed — do NOT fall through
         to the generic transition audit). Delete the trailing D2 push block.
       - dlq.py validate_dlq_record: promote(..., actor="steward"); return {"status": "accepted",
         "rio_id", "routing", "push_queued"}. validate_batch: per row promote(...); validated += 1;
         no 503, the loop always finishes. Remove the now-unused validate_and_promote_rio import.
       - atrativos.py transition_atrativo promote edge: same shape as cms transition. Delete the
         trailing D2 push block. promote_bulk_atrativos: keep candidate selection + dry-run in the
         router (Q7); per id: rio = db.get → promote(db, rio, actor="steward", enqueue=enqueue,
         action="transition_mar", held_action="promote_held", extra={"batch_id": batch_id},
         config=config); p.routing == "mar" → promoted count, and append id to push_failed when
         not p.push_queued; else held.append({"id", "reason": p.held_reason}); keep the per-record
         try/except + db.rollback + failed list. Delete the separate dispatch loop. Response keys
         unchanged. Remove the validate_and_promote_rio import.
       - engine.py (~150-164): keep the 503 when norteia_api_up(redis) is False; replace the tasks
         import with `republish_pending(db, enqueue)` (enqueue via Depends(get_publish_enqueue)).
         Import `republish_pending` at MODULE TOP of engine.py (`from brave.core.mar.publication
         import republish_pending`) so test_mar_repush can monkeypatch engine_router.republish_pending.
         Only the dispatch_pending_pushes import goes away; the other legit brave.tasks imports in
         engine.py (~242 UF_LIST, ~263 _cascade_search_client, ~380 engine_sweep_run) STAY.
    4. brave/shared/whatsapp/agent.py: import promote at MODULE LEVEL (`from
       brave.core.mar.publication import promote`, not inside the node) so tests can patch
       brave.shared.whatsapp.agent.promote. _finalize_node (per Q8/Q9): on owner confirmation write
       owner_horarios/owner_valor into a copy of normalized, flag_modified, flush (drop the
       validacao_humana_value line — promote sets it), then `promotion = promote(session, record,
       actor="whatsapp_owner", action="owner_validated", enqueue=push_confirmed_fn or (lambda
       _rid: None), config=score_config)` (discretion: new audit action "owner_validated" so the
       steward dlq_validated rate is unaffected). Promote commits inside the node — accepted per
       Q9. Log finalize_reprocessed with promotion.routing. Remove the manual reprocess + dispatch
       block and the local reprocess import. Keep the push_confirmed_fn parameter name (minimal
       diff) but update the docstrings to "enqueue for brave.publish_mar" — including the
       module docstring (~13-29) that names push_attraction_task.
       Docstring-only touch-ups (they name deleted symbols): brave/shared/whatsapp/__init__.py
       (~19: push_attraction_task → brave.publish_mar) and brave/shared/dtos.py (~40:
       _build_push_payload → brave.core.mar.service.build_push_payload).
    5. brave/cli.py fixture run (per Q5): replace promote_to_mar + the hand-built payload + Null
       push_destination with promote(session, rio, actor="cli", enqueue=lambda _rid: None) and,
       when routing is "mar", publish(session, rio.id, NullNorteiaApiClient()); print
       "Push: {published.status}" (backstop line kept when routing != "mar"). Remove unused imports.
  </action>
  <verify>
    <automated>cd /Users/leandro/Projects/norteia/norteia-brave/.claude/worktrees/mar-publication && ! rtk proxy grep -rn --include='*.py' -e "push_destination_task" -e "push_attraction_task" -e "dispatch_pending_pushes" -e "_mark_pushed" -e "_build_push_payload" -e "def push_mar" -e "brave.push_mar" -e "isinstance(api_client" brave && ! rtk proxy grep -rn -e "broker unavailable" -e "broker indisponível" brave/api/routers && ! rtk proxy grep -n "dispatch_pending_pushes" brave/api/routers/engine.py && ../../../.venv/bin/ruff check brave/ && export BRAVE_DB_URL='postgresql+psycopg://brave:brave@localhost:5432/norteia_brave_test' BRAVE_DB_REDIS_URL='redis://localhost:6379/15' && unset RUN_REAL_EXTERNALS && ../../../.venv/bin/python -c "from brave.tasks.celery_app import app; app.loader.import_default_modules(); assert 'brave.publish_mar' in app.tasks; assert 'brave.push_attraction' not in app.tasks" && ../../../.venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_mar_publication.py tests/unit/test_domain_boundaries.py</automated>
  </verify>
  <done>Grep gates empty; ruff clean on brave/; brave.publish_mar registered and the old task names absent; publication + boundary tests green. (Stale tests referencing deleted names are fixed in Task 3.)</done>
</task>

<task type="auto">
  <name>Task 3: replace stale tests, add CONTEXT.md glossary, full-suite gate</name>
  <files>tests/integration/test_push_destination_task.py, tests/unit/test_push_hash_skip.py, tests/integration/test_mar_push.py, tests/unit/test_celery_task_registration.py, tests/unit/test_engine_depth_gating.py, tests/unit/lanes/test_copy_batch.py, tests/unit/test_mar_sync.py, tests/unit/api/test_mar_repush.py, tests/unit/test_transitions.py, tests/unit/test_atrativos_promote_bulk.py, tests/integration/test_destinos_lane.py, tests/test_cms_endpoints.py, tests/integration/test_atrativos_lane_e2e.py, tests/unit/test_scaffold_smoke.py, tests/unit/lanes/test_whatsapp_agent.py, CONTEXT.md</files>
  <action>
    Replace, don't layer (per Q12). Pact contract test stays untouched.
    1. DELETE tests/integration/test_push_destination_task.py and tests/unit/test_push_hash_skip.py
       (their behaviour is now covered by tests/unit/test_mar_publication.py).
    2. tests/integration/test_mar_push.py: delete "Test 6" (inspects push_mar source).
    3. tests/unit/test_celery_task_registration.py: "brave.push_mar" → "brave.publish_mar".
    4. tests/unit/test_engine_depth_gating.py: monkeypatch pipeline.publish_mar instead of push_mar
       (update the docstrings that name push_mar).
    5. tests/unit/lanes/test_copy_batch.py ~996: patch "brave.tasks.pipeline.publish_mar".
    6. tests/unit/test_mar_sync.py: delete test_dispatch_routes_each_row_to_its_entity_task
       (republish_pending is covered in test_mar_publication); keep the beat tests (repush still
       uses _norteia_api_down / _get_session).
    7. tests/unit/api/test_mar_repush.py: in the client fixture override get_publish_enqueue with a
       no-op/list; in _patch replace the pipeline.dispatch_pending_pushes patch with
       monkeypatch.setattr(engine_router, "republish_pending", calls). Assertions unchanged.
    8. tests/unit/test_transitions.py (destino + atrativo promote edges): patch
       "brave.api.routers.cms.promote" / "brave.api.routers.atrativos.promote" to return a
       Promotion (routing "mar", push_queued True) or a held Promotion (routing "dlq",
       held_reason "no_recent_reviews"); call the router with enqueue=<list>.append; assert the
       result dict / 409 status + detail containing the reason, and that promote was called with
       actor="steward", action="transition_mar", held_action="promote_held". Drop the
       write_audit/.delay/db.commit assertions for these edges (the module owns them). Pass
       enqueue in any other direct router call in that file that now requires it.
    9. tests/unit/test_atrativos_promote_bulk.py: patch f"{MOD}.promote" instead of
       validate_and_promote_rio + push_attraction_task; assert promoted/held/failed and that
       push_failed lists exactly the ids whose Promotion has push_queued False; assert
       extra={"batch_id": ...} is passed. Drop write_audit action assertions (module-owned now,
       covered in test_mar_publication).
    10. tests/integration/test_destinos_lane.py (~280-400, 3 broker-down tests) and
        tests/test_cms_endpoints.py (~690-760): replace monkeypatching task .delay with
        app.dependency_overrides[get_publish_enqueue] = lambda: <callable that raises RuntimeError>; flip
        expectations from 503 to 202; keep the WR-01 assertions (routing "mar" committed, audit
        row persists) and assert response push_queued is False; the batch test asserts every row
        was validated. Rename the tests accordingly (e.g. *_broker_down_returns_202_pending). If
        any other test in these files hangs on a real broker, override get_publish_enqueue in its
        client fixture with a list append (always remove the override in teardown).
    11. tests/integration/test_atrativos_lane_e2e.py ~1131: import build_push_payload from
        brave.core.mar.service instead of pipeline._build_push_payload.
    12. tests/unit/test_scaffold_smoke.py (~230-238): the NorteiaApiClientProtocol check now
        asserts hasattr(NorteiaApiClientProtocol, "push") (drop push_destination/push_attraction).
    12b. tests/unit/lanes/test_whatsapp_agent.py: add one async test for the _finalize_node
        owner-confirmed path — state with extraction {existe: "sim", funcionando: "sim",
        horarios, valor}, MagicMock session (session.get returns the rio MagicMock), patch
        brave.shared.whatsapp.agent.promote returning a Promotion(routing "mar"); assert promote
        was called once with actor="whatsapp_owner" and enqueue IS the passed push_confirmed_fn,
        and that record.normalized already held owner_horarios/owner_valor at call time (capture
        normalized inside the patch side_effect).
    12c. Run the full suite; fix any other test that still references a deleted name.
    13. Create repo-root CONTEXT.md (per Q13): short glossary in pt-BR, one short paragraph per
        term: Nascente, Rio, Mar, DLQ, **Promoção** (síncrona, entrada validada por humano no Mar —
        steward ou dono via WhatsApp; `promote`), **Publicação** (envio assíncrono para a
        norteia-api — `brave.publish_mar` / `publish`), **Pendente** (linha do Mar com
        pushed_at NULL — o outbox; beat de 15 min e "Reenviar" do Painel chamam
        `republish_pending`). Point at brave/core/mar/publication.py. No other sections.
  </action>
  <verify>
    <automated>cd /Users/leandro/Projects/norteia/norteia-brave/.claude/worktrees/mar-publication && ! rtk proxy grep -rln -e "push_destination_task" -e "push_attraction_task" -e "dispatch_pending_pushes" -e "_build_push_payload" -e "pipeline.push_mar" -e "\"brave.push_mar\"" tests --include='*.py' && test ! -f tests/integration/test_push_destination_task.py && test ! -f tests/unit/test_push_hash_skip.py && rtk proxy grep -c "Publicação" CONTEXT.md && ../../../.venv/bin/ruff check tests/ && export BRAVE_DB_URL='postgresql+psycopg://brave:brave@localhost:5432/norteia_brave_test' BRAVE_DB_REDIS_URL='redis://localhost:6379/15' && unset RUN_REAL_EXTERNALS && ../../../.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -20</automated>
  </verify>
  <done>Full suite: only the 8 baseline failures remain (test_atrativos_chain_e2e::test_chain_stops_at_dlq_no_auto_gate, test_atrativos_lane_e2e::test_sc2_discovery_skips_absent_parent_destino, 6 in tests/integration/test_engine_endpoints.py — 409/noop). No test references a deleted name; CONTEXT.md exists with the 7 terms; ruff clean.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| dashboard/steward → FastAPI | promote/validate/bulk mutations; auth unchanged (require_steward_or_bearer, edit-lock) |
| WhatsApp owner → finalize node | untrusted owner reply already validated by instructor/Pydantic extraction |
| Brave worker → norteia-api | Bearer service token over HTTP, Mar payload leaves the system |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-nrx-01 | Repudiation | publication.promote | mitigate | every promote writes one AuditLog row with actor (steward / whatsapp_owner / cli) before commit; tested in test_mar_publication |
| T-nrx-02 | Elevation of Privilege | brave.publish_mar | mitigate | publish never promotes and returns "not_in_mar" unless rio.routing == "mar" and an active MarRecord exists — a forged/stale rio_id on the queue cannot push a non-Mar record |
| T-nrx-03 | Denial of Service | broker outage on promote | accept | no longer 503s; row stays pending (pushed_at NULL), visible in the Painel pending count and re-dispatched by the 15-min beat / Reenviar |
| T-nrx-04 | Information Disclosure | NorteiaApiClient.push / publish_mar logs | mitigate | token stays in headers only; logs carry rio_id, error and response body, never headers (unchanged discipline) |
| T-nrx-05 | Tampering | push_hash formula | mitigate | publication keeps the exact sha256(json.dumps(sort_keys=True, default=str)) formula so stored hashes stay valid |
| T-nrx-06 | Spoofing | router auth | accept | auth dependencies untouched; only the body of the handlers changes |
</threat_model>

<verification>
- Task 1–3 automated commands green.
- tests/unit/test_domain_boundaries.py green (publication.py imports no brave.tasks).
- Full suite = the 8 baseline failures only, run ONLY against norteia_brave_test + Redis db 15 with
  RUN_REAL_EXTERNALS unset (never the operator's norteia_brave / Redis db 0).
</verification>

<success_criteria>
- One module (brave/core/mar/publication.py) owns promote → audit → commit → enqueue, publish, and republish_pending.
- One Celery task, brave.publish_mar; the three old push tasks, the shims and the isinstance seam are gone.
- No promote endpoint returns 503 on a broker outage; bulk still reports push_failed.
- WhatsApp finalize and cli go through the same module.
- CONTEXT.md glossary exists at repo root.
</success_criteria>

<output>
Create `.planning/quick/260923-nrx-deepen-mar-publication-module/260923-nrx-SUMMARY.md` when done.
</output>
