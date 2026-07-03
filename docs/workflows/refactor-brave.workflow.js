export const meta = {
  name: 'refactor-brave-spec-driven',
  description: 'Execute one phase (args.phase A..H) of the spec-driven Brave refactor: scout → implement → review → self-test',
  whenToUse: 'Reusable per-phase harness for docs/ultraplan-refactor-brave.md. Invoke once per phase; gate on green suite between phases.',
  phases: [
    { title: 'Scout' },
    { title: 'Implement' },
    { title: 'Review' },
    { title: 'Self-test' },
  ],
}

// ---------------------------------------------------------------------------
// Shared schemas
// ---------------------------------------------------------------------------
const SCOUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['topic', 'findings', 'notes'],
  properties: {
    topic: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['file', 'detail'],
        properties: {
          file: { type: 'string' },
          lines: { type: 'string' },
          symbol: { type: 'string' },
          detail: { type: 'string' },
        },
      },
    },
    risks: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
}

const IMPL_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['changed_files', 'summary'],
  properties: {
    changed_files: { type: 'array', items: { type: 'string' } },
    new_files: { type: 'array', items: { type: 'string' } },
    deleted_files: { type: 'array', items: { type: 'string' } },
    migrations: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
    deviations: { type: 'array', items: { type: 'string' } },
    follow_ups: { type: 'array', items: { type: 'string' } },
  },
}

const REVIEW_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['verdict', 'summary'],
  properties: {
    verdict: { type: 'string', enum: ['pass', 'concerns', 'block'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['severity', 'file', 'issue'],
        properties: {
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          file: { type: 'string' },
          line: { type: 'string' },
          issue: { type: 'string' },
          fix: { type: 'string' },
        },
      },
    },
    guardrails: { type: 'string' },
    summary: { type: 'string' },
  },
}

const TEST_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['passed', 'summary'],
  properties: {
    passed: { type: 'boolean' },
    summary: { type: 'string' },
    failing: { type: 'array', items: { type: 'string' } },
    tail: { type: 'string' },
  },
}

// ---------------------------------------------------------------------------
// Standing context injected into every agent prompt
// ---------------------------------------------------------------------------
const REPO = '/Users/leandro/Projects/norteia/norteia-brave'
const GUARDRAILS = `
GUARDRAILS (never break — a review that misses these is a failed review):
- tests/contract/test_pact_norteia_api.py stays GREEN and byte-identical: the Mar push
  payload dict (keys, order-insensitive values, float types) and the paths
  /api/internal/territorial/{destinations,attractions} are UNCHANGED.
- Single Celery queue "celery"; no task_routes, no custom Queue(), no -Q flag.
- Offline posture: run_real_externals=False by default; clients injectable; CI keyless.
- Import rule (D-18): brave.core / brave.shared NEVER import brave.domains / brave.tasks;
  domains never import each other. tests/unit/test_no_test_imports_in_brave.py enforces variants.
- WhatsApp subsystem preserved (LangGraph/consent/ramp/quality-rating/Twilio).
DECIDED DEFAULTS (do not reopen):
- Score binary: >=80 → Mar, else → DLQ. No descarte band, no mar_ready override.
- routing column is String(32), NOT a PG enum: backfill descarte→dlq in Alembic, no ALTER TYPE.
- no-reviews / review >90 days → DLQ.
`

const TEST_CMD =
  `cd ${REPO} && env -u RUN_REAL_EXTERNALS .venv/bin/python -m pytest -q 2>&1 | tail -45`

// ---------------------------------------------------------------------------
// Phase specs
// ---------------------------------------------------------------------------
const PHASES = {
  A: {
    goal: 'Scaffolding with NO behavior change: brave/shared/{exceptions,dtos}.py + re-export shims; move _build_push_payload → mar/service.build_push_payload returning a MarPushPayload whose .model_dump() is byte-identical; create brave/core/repositories/ by PURE extraction of inline queries.',
    scouts: [
      {
        label: 'scout:exceptions',
        topic: 'exception hierarchy',
        prompt: `Map every exception class definition and its importers across brave/. Focus: TransientError, PermanentError (currently defined in brave/tasks/pipeline.py), ComplianceError, CostGuardError, and any Source/session errors in brave/lanes/tripadvisor/client.py (SessionMissingError/SessionExpiredError). For EACH: where defined (file:line), every module that imports it, and the exact import statement. Goal: create a central brave/shared/exceptions.py (BraveError base + TransientError/PermanentError/ComplianceError/CostGuardError/SourceError/SourceSessionError) and leave re-export shims in the OLD locations so existing imports keep working. Report exactly what must be re-exported to avoid breaking imports.`,
      },
      {
        label: 'scout:push_payload',
        topic: 'Pact push payload',
        prompt: `Read brave/tasks/pipeline.py::_build_push_payload (around line 356) AND tests/contract/test_pact_norteia_api.py in full. Report: the EXACT output dict shape of _build_push_payload (every key, value type, how source/source_ref/entity_type/canonical/reliability_score/score_version/provenance are derived), every call site of _build_push_payload in the codebase (file:line), and exactly what the Pact test asserts about the payload (keys, types, nesting). Goal: move the logic to brave/core/mar/service.py::build_push_payload(mar, rio) -> MarPushPayload (pydantic model in brave/shared/dtos.py) such that .model_dump() is byte-identical to today. List every field the pydantic MarPushPayload + FlatProvenance models must have to reproduce the dict exactly.`,
      },
      {
        label: 'scout:repositories',
        topic: 'inline queries to extract',
        prompt: `Enumerate the inline SQLAlchemy queries in: brave/core/rio/routing.py, brave/core/mar/service.py, brave/core/engine.py, brave/core/rio/dedup.py, brave/core/dlq/service.py. For each query report: file:line, the function it lives in, the SELECT/UPDATE/INSERT it runs (models + filters + returns), and the session-handling (who commits). Goal: create brave/core/repositories/{base.py (typing.Protocol interfaces: NascenteRepository, RioRepository, MarRepository, DlqRepository), sqlalchemy.py (one impl each)} by PURE extraction — behavior identical, session still opened/committed by the caller (no UnitOfWork, no async repo). Identify which queries are the cleanest first candidates and any that are risky to move (transactions, flush ordering, pgvector dedup).`,
      },
      {
        label: 'scout:import_rule',
        topic: 'import-rule test',
        prompt: `Read tests/unit/test_no_test_imports_in_brave.py in full and report exactly what it enforces (which packages may/may not import which). Also confirm there is no existing brave/shared/ package. Goal: ensure a new brave/shared/ package (exceptions.py, dtos.py) and brave/core/repositories/ will not violate this test, and note whether the test itself needs a (later-phase) update for the shared/domains rule.`,
      },
    ],
    implementSteps: [
      {
        label: 'impl:phaseA',
        effort: 'max',
        prompt: `Implement Phase A (scaffolding, NO behavior change). Work in ${REPO} on branch refactor/spec-driven-alignment. Use the scout findings below as ground truth; if a finding is incomplete, Read the file yourself before editing.

Do these THREE parts, in order, surgically:

1) brave/shared/exceptions.py — central hierarchy: BraveError(Exception) base; TransientError, PermanentError, ComplianceError, CostGuardError, SourceError, SourceSessionError subclassing BraveError (keep any existing base semantics). Create brave/shared/__init__.py. Then in the OLD locations that currently DEFINE these (e.g. brave/tasks/pipeline.py defines TransientError/PermanentError), replace the local class definitions with re-export shims (import from brave.shared.exceptions) so every existing importer keeps working UNCHANGED. Do NOT change any raise/except behavior. isinstance relationships must be preserved for existing except clauses.

2) brave/shared/dtos.py — pydantic v2 models FlatProvenance and MarPushPayload that reproduce _build_push_payload's dict EXACTLY (same keys, same float coercion). Add brave/core/mar/service.py::build_push_payload(mar_record, rio_record) -> MarPushPayload containing the moved logic. Update brave/tasks/pipeline.py::_build_push_payload to delegate: return build_push_payload(mar, rio).model_dump() — the returned dict MUST be byte-identical to before (verify key set + value types against the Pact test). Keep _build_push_payload as the thin shim (all call sites unchanged).

3) brave/core/repositories/{__init__.py, base.py, sqlalchemy.py} — base.py has typing.Protocol interfaces (NascenteRepository, RioRepository, MarRepository, DlqRepository); sqlalchemy.py has one concrete impl per Protocol. PURE extraction of the inline queries the scout mapped — method bodies contain the SAME query, session passed in, caller still commits. Then refactor the original call sites to call the repository methods so the queries live in ONE place. If any query is risky (flush ordering, pgvector, transaction), extract it but keep the exact same statement and ordering. Do NOT change routing/scoring logic.

Constraints: ${GUARDRAILS}
Run ruff on changed files if available. Do NOT run the full test suite (the workflow does that). Return the structured result.

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
    ],
    reviewers: [
      {
        label: 'review:pact',
        prompt: `Review the working-tree changes (git diff on branch refactor/spec-driven-alignment) for Phase A. FOCUS: is the Mar push payload still byte-identical? Read the new brave/shared/dtos.py MarPushPayload/FlatProvenance, the new brave/core/mar/service.py::build_push_payload, and brave/tasks/pipeline.py::_build_push_payload, and compare against tests/contract/test_pact_norteia_api.py assertions. Report any key/type/derivation drift. verdict=block if the payload could differ.`,
      },
      {
        label: 'review:behavior',
        prompt: `Review Phase A working-tree changes for behavior-preservation of the repository extraction and exception shims. Confirm: (a) extracted queries in brave/core/repositories/sqlalchemy.py are identical statements to the originals and call sites now delegate correctly; (b) exception re-export shims preserve isinstance/except semantics and no importer breaks; (c) no core/shared module now imports brave.tasks or brave.domains. Report regressions.`,
      },
    ],
  },
  B: {
    goal: 'Score binário: >=80 → Mar, <80 → DLQ. Remove descarte band + mar_ready override. Alembic 0008: backfill descarte→dlq (String(32), no ALTER TYPE) + DROP COLUMN mar_ready (+ index). Delete core/promote/. Rewrite descarte/mar_ready tests.',
    scouts: [
      {
        label: 'scout:score',
        topic: 'score engine + schemas',
        prompt: `Read brave/core/score/engine.py and brave/core/score/schemas.py IN FULL. Report: current ScoreResult.routing type/values (Literal?), every ScoreConfig field (threshold_mar, threshold_dlq, mar_ready_atualidade_bar, mar_ready_corrob_bar, weights, any others) with defaults, and the exact routing-decision logic (how score maps to mar/dlq/descarte today). Goal: ScoreResult.routing becomes Literal["mar","dlq"]; routing = "mar" if score >= config.threshold_mar else "dlq"; threshold_mar=80.0; DELETE threshold_dlq, mar_ready_atualidade_bar, mar_ready_corrob_bar. List everything referencing those deleted fields.`,
      },
      {
        label: 'scout:routing',
        topic: 'rio/routing.py route_by_score',
        prompt: `Read brave/core/rio/routing.py IN FULL (note: it was just refactored in phase A to use RioRepository). Report route_by_score in full: the mar_ready block (owner-override borderline promotion), the descarte routing branch, dlq_reason construction, and how routing/sub_state are set. Identify the EXACT lines to delete for the mar_ready block and the descarte branch, and how dlq_reason should reflect threshold 80. Also report any reference to "descarte" as a routing value anywhere in brave/core/.`,
      },
      {
        label: 'scout:promote',
        topic: 'core/promote deletion safety',
        prompt: `Read brave/core/promote/service.py and brave/core/promote/__init__.py IN FULL. Grep the whole repo (brave/ and tests/) for every importer/caller of brave.core.promote (any symbol). Report each call site file:line and what it uses. Goal: DELETE brave/core/promote/ entirely (mar_ready override removed; borderline promotion now flows only through validate_and_promote_rio). Report exactly what breaks if deleted and what each caller must switch to (or whether the caller is itself dead code to remove).`,
      },
      {
        label: 'scout:models_migration',
        topic: 'models + alembic chain',
        prompt: `Read brave/core/models.py RioRecord definition (find the mar_ready column + any index on it). Read alembic/versions/0006_add_rio_mar_ready.py and 0007_add_runs_history.py IN FULL. Report: exact revision and down_revision identifiers of 0006 and 0007 (so 0008 chains from the current head), how mar_ready column + its index were created in 0006 (names, types), and confirm the routing column is String(32) NOT a PG enum (quote its definition in models.py + wherever it was created). Goal: author alembic 0008 with down_revision = <head=0007 rev id>, upgrade = UPDATE rio_records SET routing='dlq' WHERE routing='descarte' THEN drop mar_ready column + its index; downgrade re-adds column+index (no data restore needed) and is a no-op for the backfill.`,
      },
      {
        label: 'scout:tests',
        topic: 'descarte/mar_ready tests',
        prompt: `Grep tests/ for every reference to "descarte" and "mar_ready" (and mar_ready_atualidade_bar/mar_ready_corrob_bar/threshold_dlq). Report each test file:line and what it asserts. Classify each: (a) asserts descarte routing → must be rewritten to assert dlq under binary threshold; (b) asserts mar_ready override behavior → delete (feature removed); (c) asserts threshold_dlq/bars → update/delete. Goal: give the implementer a precise per-test action list so coverage of the binary routing is preserved (add >=80→mar and <80→dlq cases where descarte cases are removed).`,
      },
    ],
    implementSteps: [
      {
        label: 'impl:phaseB',
        effort: 'max',
        prompt: `Implement Phase B (score binary) in ${REPO} on branch refactor/spec-driven-alignment. Ground truth = scout findings below; Read files yourself if a finding is thin.

1) brave/core/score/schemas.py: ScoreResult.routing → Literal["mar","dlq"] (remove "descarte"). Remove ScoreConfig fields threshold_dlq, mar_ready_atualidade_bar, mar_ready_corrob_bar. Set threshold_mar default 80.0. Keep compute_score / the 5-criterion producer intact.
2) brave/core/score/engine.py: routing = "mar" if score >= config.threshold_mar else "dlq". Remove any descarte/mar_ready decision logic and references to deleted fields.
3) brave/core/rio/routing.py::route_by_score: DELETE the mar_ready override block and the descarte branch; dlq_reason reflects threshold 80. Keep compute_score usage + repository calls from phase A unchanged.
4) DELETE brave/core/promote/ entirely (both files). Fix/remove every caller per the promote scout — borderline promotion flows only through validate_and_promote_rio. Do NOT break validate_and_promote_rio.
5) Alembic 0008 (alembic/versions/0008_*.py): down_revision = current head (0007's revision id from scout). upgrade(): op.execute("UPDATE rio_records SET routing='dlq' WHERE routing='descarte'") FIRST, then drop the mar_ready index then drop_column('rio_records','mar_ready'). NO ALTER TYPE (routing is String(32)). downgrade(): re-add mar_ready column + index (mirror 0006); backfill is not reversed. Also remove the mar_ready column from brave/core/models.py RioRecord (and any mar_ready property/usage).
6) Tests: per the tests scout, rewrite descarte-routing tests to assert dlq under binary threshold, ADD explicit >=80→mar and <80→dlq cases, DELETE mar_ready-override tests, update/delete threshold_dlq/bar tests. Do not weaken coverage.

Constraints: ${GUARDRAILS}
Run ruff on changed files. Do NOT run the full suite. Return structured result (list the migration file + deleted files).

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
    ],
    reviewers: [
      {
        label: 'review:migration',
        prompt: `Review Phase B's Alembic 0008 migration. Verify: down_revision equals the prior head (0007) so the chain is linear (run: cd ${REPO} && .venv/bin/alembic history and .venv/bin/alembic heads — must show a single head at 0008). Backfill UPDATE runs BEFORE drop_column and uses routing='descarte'→'dlq'. No ALTER TYPE. downgrade re-creates mar_ready column + index (mirrors 0006). Also confirm brave/core/models.py no longer declares mar_ready. verdict=block on any chain break or destructive irreversibility beyond the intended drop.`,
      },
      {
        label: 'review:routing',
        prompt: `Review Phase B routing/score changes. Confirm: ScoreResult.routing Literal is exactly ["mar","dlq"]; no "descarte" string remains as a routing value anywhere in brave/ (grep); mar_ready override fully gone; threshold_mar=80 drives the binary decision; deleted ScoreConfig fields have no dangling references; core/promote/ deleted with all callers fixed and validate_and_promote_rio intact. Report any leftover.`,
      },
      {
        label: 'review:tests',
        prompt: `Review Phase B test changes for coverage preservation. Confirm binary routing is tested (an explicit >=80→mar case AND a <80→dlq case exist), mar_ready-override tests are removed (not just skipped), and no test still imports core.promote or references deleted ScoreConfig fields. Flag any place where a descarte test was deleted without an equivalent dlq assertion replacing it.`,
      },
    ],
  },
  C: {
    goal: 'Motor Pausado: operator mode LIGADO/PAUSADO/DESLIGADO in Redis brave:engine:mode. PAUSADO breaks the sweep loop (no new UFs / no auto-push) BUT unlocks Kanban editing. Edit-lock: require_editing_unlocked dependency returns HTTP 423 when LIGADO, applied to cms mutation endpoints. Read + gate approve/reject unaffected. (config_settings persistence deferred to Phase D — Redis-only mode here.)',
    scouts: [
      {
        label: 'scout:engine',
        topic: 'engine.py runtime + redis keys',
        prompt: `Read brave/core/engine.py IN FULL. Report: all runtime states (idle|running|stopping) and their Redis keys (brave:engine:*), every helper (set_enabled, is_enabled, mark_idle, mark_running, start_run, get_status, the source key brave:engine:source), the exact get_status() return shape (dict keys), and the orchestrator sweep loop function(s) (name + where it decides to dispatch the next UF / continue vs stop — the exact line to insert a "break when mode != LIGADO" guard). Also report the NASCENTE/NASCENTE_RIO/NASCENTE_RIO_MAR depth constants location. Goal: add operator-mode helpers set_mode(rc,mode)/get_mode(rc)->default 'LIGADO'/is_editing_unlocked(rc)->True if PAUSADO or DESLIGADO; get_status adds mode + editing_unlocked; DESLIGADO path calls mark_idle + set_enabled(False); the sweep loop breaks when get_mode != 'LIGADO'.`,
      },
      {
        label: 'scout:cms_deps',
        topic: 'cms mutation endpoints + deps',
        prompt: `Read brave/api/routers/cms.py and brave/api/deps.py IN FULL. Report: the four card-mutation endpoints edit_destino, edit_atrativo, transition_destino, advance_atrativo_state (exact function signatures, decorators/paths, existing Depends(...) they use, especially any steward-auth dependency), and how the Redis client is obtained inside these routers (dependency or module-level). Also report read endpoints and the DLQ gate approve/reject endpoints so we DO NOT lock them. Goal: add a FastAPI dependency require_editing_unlocked (in deps.py) that raises HTTPException(423) when engine.get_mode(rc)=='LIGADO', and attach it to exactly those four mutation endpoints (Depends), leaving reads + gate approve/reject unaffected.`,
      },
      {
        label: 'scout:engine_router',
        topic: 'engine router start/stop',
        prompt: `Read brave/api/routers/engine.py IN FULL. Report the start/stop/status endpoints, their payloads, and how they call engine helpers (set_enabled, mark_idle, source selection). Goal: add a way for the operator to set mode PAUSADO (and LIGADO/DESLIGADO) — likely a PATCH/POST that calls engine.set_mode — and surface mode + editing_unlocked in the status endpoint response. Report the minimal additive change.`,
      },
      {
        label: 'scout:tests',
        topic: 'affected engine/cms tests',
        prompt: `Find every test that (a) calls the cms mutation endpoints edit_destino/edit_atrativo/transition_destino/advance_atrativo_state, or (b) exercises engine start/stop/status/get_status, or (c) the orchestrator sweep loop. For each report file:line and what it does. CRITICAL: get_mode defaults to 'LIGADO', which will make require_editing_unlocked return 423 for any mutation test that does not first set mode to PAUSADO/DESLIGADO. List exactly which existing tests will start getting 423 and therefore need to set engine.set_mode(rc,'PAUSADO') (or a fixture) to keep passing. Also list tests asserting the get_status shape that must accept the new mode/editing_unlocked keys.`,
      },
    ],
    implementSteps: [
      {
        label: 'impl:phaseC',
        effort: 'max',
        prompt: `Implement Phase C (Motor Pausado + edit-lock) in ${REPO} on branch refactor/spec-driven-alignment. Ground truth = scout findings; Read files yourself if thin.

1) brave/core/engine.py: add operator-mode layer on Redis key brave:engine:mode (values 'LIGADO'|'PAUSADO'|'DESLIGADO'):
   - set_mode(rc, mode): validate mode in the three values, store it. For DESLIGADO also call mark_idle(rc) + set_enabled(rc, False). For PAUSADO leave runtime as-is (drain) but do NOT set_enabled False. For LIGADO just set mode.
   - get_mode(rc) -> str, default 'LIGADO' when unset.
   - is_editing_unlocked(rc) -> bool: True iff mode in ('PAUSADO','DESLIGADO').
   - get_status(rc): ADD keys mode and editing_unlocked (additive — keep all existing keys).
   - The orchestrator sweep loop: BREAK (stop dispatching new UFs / no auto-push) when get_mode(rc) != 'LIGADO'. Keep the existing runtime idle|running|stopping drain contract intact.
   Keep runtime states unchanged; mode is an orthogonal operator layer.
2) brave/api/deps.py: add dependency require_editing_unlocked that resolves the Redis client the same way the other deps do and raises HTTPException(status_code=423, detail=...) when engine.get_mode(rc)=='LIGADO'. (No-op / allow when PAUSADO or DESLIGADO.)
3) brave/api/routers/cms.py: attach Depends(require_editing_unlocked) to edit_destino, edit_atrativo, transition_destino, advance_atrativo_state ONLY. Do NOT add it to reads or to the DLQ gate approve/reject endpoints.
4) brave/api/routers/engine.py: add operator mode control (set mode LIGADO/PAUSADO/DESLIGADO via a small endpoint calling engine.set_mode) and surface mode + editing_unlocked in the status response.
5) Tests: per the tests scout, update mutation tests that will now hit 423 to first set mode PAUSADO (or add a fixture that unlocks editing); update get_status shape assertions to accept mode/editing_unlocked; ADD tests: 423 when LIGADO on each of the 4 endpoints, 200 when PAUSADO, sweep loop breaks when mode!=LIGADO, DESLIGADO sets enabled False + idle, gate approve/reject still work while LIGADO.

Constraints: ${GUARDRAILS}
NOTE: config_settings table does not exist yet (Phase D) — mode is Redis-only for now; do NOT try to persist to config_settings. brave/domains/manual/controllers.py does not exist yet (Phase G) — only wire cms.py here.
Run ruff on changed files. Do NOT run the full suite. Return structured result.

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
    ],
    reviewers: [
      {
        label: 'review:engine_mode',
        prompt: `Review Phase C engine changes. Confirm: set_mode/get_mode/is_editing_unlocked behave per spec (get_mode default 'LIGADO'; is_editing_unlocked True only for PAUSADO/DESLIGADO; DESLIGADO also mark_idle+set_enabled(False); PAUSADO does NOT disable, only stops the loop); get_status is additive (mode + editing_unlocked, all prior keys intact); the sweep loop actually breaks when mode!=LIGADO (find the guard, confirm it prevents new UF dispatch AND auto-push). Runtime idle|running|stopping drain contract unchanged. Flag any regression.`,
      },
      {
        label: 'review:edit_lock',
        prompt: `Review Phase C edit-lock. Confirm require_editing_unlocked returns HTTP 423 when mode=='LIGADO' and is attached to EXACTLY edit_destino, edit_atrativo, transition_destino, advance_atrativo_state — and NOT to any read endpoint nor the DLQ gate approve/reject. Verify the Redis client is resolved consistently with existing deps. Confirm no config_settings / manual-controller references leaked in (those are later phases). Report over- or under-application of the lock.`,
      },
      {
        label: 'review:tests',
        prompt: `Review Phase C tests. Confirm new coverage exists: 423-when-LIGADO on each of the 4 mutation endpoints, success-when-PAUSADO, sweep-loop-breaks-when-paused, DESLIGADO disables+idles, and gate approve/reject unaffected by LIGADO. Confirm previously-passing mutation tests were correctly updated (set PAUSADO) rather than deleted, and get_status assertions accept the new keys. Flag weakened coverage.`,
      },
    ],
  },
  D: {
    goal: 'Config-in-DB: Alembic 0009 config_settings (key PK String(128), value JSON {"v":...}, updated_at, updated_by) + ConfigSetting model; runtime.load_effective_config(session)->AppConfig overlaying DB rows on env bootstrap, cached in Redis brave:config:snapshot (bust-on-write); api/routers/config.py GET(Bearer)/PATCH(steward) with validation (weights sum 100, thresholds in [0,100]) + audit + cache bust; beat_schedule + /engine/start driven by enabled_sources(config); idempotent seed; persist engine mode to config_settings.',
    scouts: [
      {
        label: 'scout:settings',
        topic: 'settings + call-sites',
        prompt: `Read brave/config/settings.py IN FULL (AppConfig, ScoreConfig, and any nested config: llm, nominatim, tripadvisor, run_real_externals). Report every field + default + env_prefix. Then grep for every call-site that instantiates ScoreConfig() or AppConfig() (file:line) and note which of those already hold a DB Session in scope. Goal: build runtime.load_effective_config(session)->AppConfig = env bootstrap then overlay config_settings rows via model_copy(update=...). Map which config keys (score.threshold_mar, score weights, source.<f>.enabled, engine.mode) overlay onto which nested fields. Identify the SAFE minimal set of call-sites to switch to load_effective_config without behavior change (seeded defaults == env defaults → effective identical).`,
      },
      {
        label: 'scout:models_alembic',
        topic: 'models Base + alembic head',
        prompt: `Read brave/core/models.py top (Base/declarative + how a simple table like runs_history/consent is defined incl. JSON columns, server_default, updated_at patterns). Confirm current alembic head is 0008 and read alembic/versions/0008_binary_score_routing.py + 0007 to get exact revision ids for chaining 0009. Goal: add ConfigSetting model (table config_settings: key String(128) PK, value JSON, updated_at DateTime server_default now onupdate now, updated_by String nullable) and Alembic 0009 (down_revision=0008) create_table matching it. Report the exact JSON column type used elsewhere (sa.JSON vs JSONB) and the updated_at idiom to mirror.`,
      },
      {
        label: 'scout:api_router',
        topic: 'router template + auth + audit',
        prompt: `Read brave/api/main.py (router registration list), brave/api/deps.py (Bearer vs steward auth dependencies — the exact dependency names for "any bearer" vs "steward"), and one existing small router (brave/api/routers/engine.py) as a template. Also find the audit-log helper used by mutation endpoints (e.g. how cms/dlq write AuditLog) and how Redis is injected. Goal: author brave/api/routers/config.py with GET /api/v1/config (Bearer) returning the effective snapshot and PATCH /api/v1/config (steward) upserting rows + audit-log + Redis cache bust, and register it in main.py. Report exact dependency + audit + redis idioms to reuse.`,
      },
      {
        label: 'scout:beat_engine_sources',
        topic: 'beat + engine enabled sources',
        prompt: `Read brave/tasks/beat_schedule.py IN FULL and the parts of brave/core/engine.py + brave/api/routers/engine.py dealing with source (_VALID_SOURCES, set_source, /engine/start source validation). Report how beat builds sweep entries per source today and how /engine/start validates the source arg. Goal: beat_schedule builds sweeps only for enabled_sources(config) and /engine/start validates source against "registered AND enabled". Report the minimal change and any test that asserts the current beat schedule shape (so it can be updated). Also confirm the two current sources: 'default' (mtur) and 'tripadvisor'.`,
      },
    ],
    implementSteps: [
      {
        label: 'impl:phaseD-core',
        effort: 'max',
        prompt: `Implement Phase D PART 1 (model + migration + runtime overlay + seed) in ${REPO} on branch refactor/spec-driven-alignment. Ground truth = scout findings; Read files if thin.

1) brave/core/models.py: add ConfigSetting ORM model — table config_settings: key = mapped_column(String(128), primary_key=True); value = mapped_column(JSON) storing {"v": <any>}; updated_at with server_default now + onupdate now (mirror existing idiom); updated_by = mapped_column(String, nullable=True). Match the repo's declarative style.
2) alembic/versions/0009_config_settings.py: down_revision = '0008' (current head). upgrade() create_table config_settings matching the model (same JSON type the repo uses). downgrade() drop_table. Confirm single head 0009 after.
3) brave/config/runtime.py (NEW): load_effective_config(session) -> AppConfig. Bootstrap AppConfig() from env, then read all config_settings rows and overlay onto the nested config via model_copy(update=...) (map dotted keys score.threshold_mar / score weights / source.<f>.enabled / engine.mode to the right fields). Cache the resulting snapshot in Redis key brave:config:snapshot; provide a bust function to delete that key on write. Absent rows → effective == env defaults (behavior-neutral). Provide enabled_sources(config) -> list[str] helper (default 'default','tripadvisor' both enabled unless a source.<f>.enabled row disables it).
4) Idempotent seed: a function (callable from a script/CLI and safe to re-run) that inserts default rows for the known keys ONLY IF absent (INSERT ... ON CONFLICT DO NOTHING or existence check). Defaults MUST equal the env/AppConfig defaults so seeding does not change behavior. Document (in a docstring) that reset-brave-db truncates config_settings and this seed must run after reset.
5) Wire load_effective_config into the SAFE minimal call-sites the scout identified that already hold a session (do NOT churn every ScoreConfig() if it risks behavior; seeded-defaults==env-defaults keeps it neutral).

Constraints: ${GUARDRAILS}
Run ruff on changed files. Do NOT run the full suite. Return structured result (list migration + new files).

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
      {
        label: 'impl:phaseD-api',
        effort: 'max',
        prompt: `Implement Phase D PART 2 (config router + engine/beat wiring + persist mode) in ${REPO} on branch refactor/spec-driven-alignment. PART 1 (ConfigSetting model, alembic 0009, brave/config/runtime.py with load_effective_config/enabled_sources/cache-bust, seed) is ALREADY done — Read those new files first to use their exact API.

1) brave/api/routers/config.py (NEW): GET /api/v1/config (Bearer auth) returns load_effective_config(session) as JSON snapshot. PATCH /api/v1/config (steward auth) accepts a dict of dotted-key→value updates; VALIDATE: score weights (origem+completude+corroboracao+atualidade+validacao_humana) sum to 100 if any weight is touched, and all thresholds ∈ [0,100]; reject invalid with 422. On success: upsert config_settings rows (value wrapped {"v":...}), write an AuditLog row (action like 'config_updated', actor steward), bust the Redis snapshot cache. Register the router in brave/api/main.py.
2) brave/tasks/beat_schedule.py: build sweep entries only for enabled_sources(effective config). Keep the single 'celery' queue (no options.queue). Update any test asserting the beat shape.
3) brave/api/routers/engine.py + brave/core/engine.py set_source: /engine/start validates source against registered AND enabled (enabled_sources). Keep single-source-per-run.
4) Persist engine operator mode to config_settings (Phase C left it Redis-only): set_mode also upserts engine.mode row (and get_mode / load_effective_config can seed Redis from it), so a Redis flush no longer resets mode to LIGADO. Keep Redis as the fast path; config_settings as the durable store. Do NOT break the Phase C 423 edit-lock tests.
5) Tests: config GET returns effective snapshot; PATCH overlays + weight-sum-100 validation (422 on bad) + threshold bounds + audit row + cache bust; enabled_sources drives beat + /engine/start rejects a disabled source; mode persists across a simulated Redis flush.

Constraints: ${GUARDRAILS}
Run ruff on changed files. Do NOT run the full suite. Return structured result.

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
    ],
    reviewers: [
      {
        label: 'review:migration_model',
        prompt: `Review Phase D migration + model. Confirm: alembic 0009 down_revision='0008', single head 0009 (run alembic heads/history), config_settings schema matches ConfigSetting model (key String(128) PK, value JSON, updated_at server_default+onupdate, updated_by nullable), downgrade drops the table. Seed is idempotent (re-run safe, no duplicate/override) and its defaults equal env/AppConfig defaults (so seeding is behavior-neutral). Flag any chain break or non-idempotent seed.`,
      },
      {
        label: 'review:overlay_wiring',
        prompt: `Review Phase D runtime overlay + wiring. Confirm load_effective_config bootstraps env then overlays config_settings via model_copy, absent rows → env defaults (behavior-neutral), Redis brave:config:snapshot cached + busted on PATCH. enabled_sources drives beat_schedule and /engine/start (disabled source rejected). Engine mode now persists to config_settings (survives Redis flush) without breaking the Phase C 423 lock. PATCH validation enforces weights-sum-100 and thresholds∈[0,100] (422 otherwise) and writes an audit row. Single 'celery' queue intact. Report gaps.`,
      },
      {
        label: 'review:guardrails',
        prompt: `Review Phase D against guardrails: Pact contract still byte-identical (config work must not touch the Mar push payload); single Celery queue (no task_routes/options.queue introduced in beat_schedule); offline posture (config GET/PATCH work without external keys; no external call on the config path); import rule (brave.config.runtime must not import brave.domains/brave.tasks; brave.core must not import brave.tasks). Report any violation.`,
      },
    ],
  },
  E: {
    frontend: true,
    goal: 'Remove Desmembramento (LLM), NotebookLM, Apify (backend) and the mar-ready screen (frontend + any residual backend). Full Apify removal: client files + base protocols + SignalAgent apify_client ctor + _compute_corroboracao (IG) + gather_signals_task Apify branch. sweep_uf stops calling Desmembramento. No migration (these features have no tables).',
    scouts: [
      {
        label: 'scout:backend_deletions',
        topic: 'importers of desmembramento/notebooklm/apify',
        prompt: `Map EVERY importer/caller across brave/ and tests/ of: brave/lanes/destinos/desmembramento.py, brave/lanes/destinos/notebooklm.py, brave/clients/apify.py, brave/clients/null_apify.py, brave/clients/notebooklm.py, brave/clients/null_notebooklm.py, and the ApifyClientProtocol / NotebookLMClientProtocol in brave/clients/base.py. For each report file:line + the import/usage. Specifically cover: brave/tasks/pipeline.py (sweep_uf DesmembramentoAgent branch ~line 835-868; gather_signals_task Apify branch ~1321-1341), brave/tasks/beat_schedule.py, brave/lanes/destinos/schemas.py (Desmembramento/NotebookLM schemas), brave/lanes/destinos/__init__.py, brave/clients/__init__.py. Goal: delete those 6 client/lane files + the 2 protocols and every dangling import cleanly.`,
      },
      {
        label: 'scout:signal_agent',
        topic: 'SignalAgent Apify + corroboracao',
        prompt: `Read brave/lanes/atrativos/signal_agent.py IN FULL. Report: the apify_client constructor arg + how it is stored/used; the _compute_corroboracao method (how corroboracao is derived — Apify IG signals and/or anything else); every other place corroboracao_value is set; and what SignalAgent writes to routing/sub_state. CRITICAL: after removing Apify, corroboracao must still be computed deterministically (from Places or a constant) — report exactly what remains and what the corroboracao value becomes without Apify, so scores/routing don't silently break. Also list all tests that construct SignalAgent(...) passing apify_client and what corroboracao they assert.`,
      },
      {
        label: 'scout:backend_tests',
        topic: 'tests referencing removed backend',
        prompt: `Grep tests/ for every reference to apify, Apify, desmembramento, Desmembramento, notebooklm, NotebookLM (imports, fakes, fixtures, assertions). Report file:line + purpose. Classify each: delete (whole file is about the removed feature) vs rewrite (a shared test that just needs the removed arg/branch dropped). Note tests/fakes/ Apify/NotebookLM fakes. Give a precise per-file action list.`,
      },
      {
        label: 'scout:frontend_marready',
        topic: 'dashboard mar-ready surface',
        prompt: `In ${REPO}/dashboard, map the ENTIRE mar-ready surface: app/mar-ready/ (pages), components/mar-ready/ (or components/**/mar-ready*), lib/mar-ready-api.ts, any mocks/handlers/* referencing mar-ready or the removed endpoints (GET /api/v1/atrativos/mar-ready, PATCH /{id}/promote, POST /promote-batch), any __tests__ for mar-ready, and EVERY other file that imports mar-ready-api or links to /mar-ready (hub page, painel nav, app router, any <Link href="/mar-ready">). Report file:line for each. Goal: delete the mar-ready screen entirely and remove all dangling references/links/mocks so bun test stays green with onUnhandledRequest:"error".`,
      },
    ],
    implementSteps: [
      {
        label: 'impl:phaseE-backend',
        effort: 'max',
        prompt: `Implement Phase E BACKEND removals in ${REPO} on branch refactor/spec-driven-alignment. Ground truth = scout findings.

DELETE files: brave/lanes/destinos/desmembramento.py, brave/lanes/destinos/notebooklm.py, brave/clients/apify.py, brave/clients/null_apify.py, brave/clients/notebooklm.py, brave/clients/null_notebooklm.py.
EDIT:
- brave/clients/base.py: remove ApifyClientProtocol and NotebookLMClientProtocol (+ any references in __all__).
- brave/clients/__init__.py, brave/lanes/destinos/__init__.py: drop removed exports.
- brave/lanes/destinos/schemas.py: remove Desmembramento/NotebookLM-only schemas IF unused after deletion (keep anything still referenced).
- brave/tasks/pipeline.py::sweep_uf: remove the DesmembramentoAgent branch (run_desmembramento, the LLM client selection for it) — sweep_uf now runs ONLY the Mtur seed. Keep depth/run_rio + quarantine behavior intact.
- brave/tasks/pipeline.py::gather_signals_task: remove the Apify branch (RealApifyClient/NullApifyClient import + apify_client=...) — SignalAgent is constructed without apify_client.
- brave/lanes/atrativos/signal_agent.py: remove the apify_client ctor arg and _compute_corroboracao's Apify/IG path. Corroboracao must remain deterministic from the remaining (Places) signals or a documented constant — do NOT let routing silently shift; keep the offline (Null) behavior equivalent. Document the new corroboracao derivation.
- brave/tasks/beat_schedule.py: drop any Desmembramento/NotebookLM entry.
TESTS: per the backend_tests scout — delete feature-only test files (apify/desmembramento/notebooklm) and their tests/fakes/ fakes; rewrite shared tests to drop the removed arg/branch; update any SignalAgent test that passed apify_client. Keep coverage of the SignalAgent corroboracao + routing path.
NO migration (no tables for these features).

Constraints: ${GUARDRAILS}
Run ruff on changed files. Do NOT run the full suite. Return structured result (list deleted files).

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
      {
        label: 'impl:phaseE-frontend',
        effort: 'max',
        prompt: `Implement Phase E FRONTEND removal (mar-ready screen) in ${REPO}/dashboard on branch refactor/spec-driven-alignment. Backend removals are ALREADY done. Ground truth = the frontend_marready scout findings.

DELETE the entire mar-ready surface: app/mar-ready/, components/mar-ready/ (or the mar-ready component files), lib/mar-ready-api.ts, its __tests__, and any mocks/handlers entries for mar-ready / the removed endpoints (GET /api/v1/atrativos/mar-ready, PATCH /{id}/promote, POST /promote-batch).
REMOVE every dangling reference: links to /mar-ready (hub page, nav, any <Link>), imports of mar-ready-api, and MSW handlers for those endpoints. The suite runs with onUnhandledRequest:"error", so no test may call a removed handler.
Do NOT touch the /painel consolidation (that is Phase H) — only remove mar-ready here; leave other dark routes in place for now.
Run the dashboard lint if quick. Do NOT run the full suite yourself (the workflow does). Return structured result (list deleted files + edited files).

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
    ],
    reviewers: [
      {
        label: 'review:backend_removal',
        prompt: `Review Phase E backend removals. Confirm: the 6 files are deleted; ApifyClientProtocol/NotebookLMClientProtocol gone from clients/base.py; NO dangling import of apify/desmembramento/notebooklm anywhere in brave/ (grep, incl. __init__ exports and beat_schedule); sweep_uf now runs only Mtur seed (no Desmembramento) with quarantine/depth intact; gather_signals_task constructs SignalAgent without apify_client. Report any dangling reference or broken import.`,
      },
      {
        label: 'review:scoring_behavior',
        prompt: `Review the SignalAgent corroboracao change after Apify removal. Confirm corroboracao_value is still computed deterministically and the OFFLINE (Null client) routing outcomes are unchanged vs before (a record that scored X and routed mar/dlq before must still do so). Read signal_agent.py + its tests. If corroboracao derivation changed in a way that shifts scores/routing, flag it HIGH with the specific case. Confirm no descarte/closed-place behavior regressed.`,
      },
      {
        label: 'review:frontend_removal',
        prompt: `Review Phase E frontend mar-ready removal in ${REPO}/dashboard. Confirm the mar-ready page/components/lib/tests are deleted, NO file still imports mar-ready-api or links to /mar-ready, and NO MSW handler references the removed endpoints (grep). With onUnhandledRequest:"error" any residual call would fail — confirm none remain. Confirm other dark routes are untouched (Phase H owns those). Report dangling references.`,
      },
    ],
  },
  F: {
    goal: 'No-reviews/>90d rule + MANUAL WhatsApp-from-DLQ flow (spec 2026-07-02). SignalAgent: no reviews OR most-recent review >90d → routing=dlq, dlq_reason=no_recent_reviews (NOT auto-gate). Backstop recency assert in promote_to_mar (TA atrativos bypass SignalAgent). Capture whatsapp_candidate (celular+DDD, masked LGPD) in Places+TA enrichment. Batch endpoint DLQ→WhatsApp: eligibility = no horário AND no preço (else 422); per-atrativo branch = LLM number-discovery (no celular+DDD) vs outreach_task (has celular+DDD). state_machine edges dlq↔aguardando_consulta_whatsapp. Substitutes old /gate approve-reject. Keep LangGraph/consent/ramp/quality-rating/Twilio. No migration (existing columns/sub_state).',
    scouts: [
      {
        label: 'scout:signal_reviews',
        topic: 'SignalAgent reviews + horário/preço',
        prompt: `Read brave/lanes/atrativos/signal_agent.py IN FULL (post-Phase-E). Report: exactly what place_details returns and which fields SignalAgent reads (business_status/CLOSED handling, reviews[] with publishTime, opening_hours/weekday_text = horário, price_level/priceRange = preço, phone). Where does routing get set and where to insert the rule "if attraction AND (no reviews OR most-recent review publishTime > 90 days old) → routing='dlq', dlq_reason='no_recent_reviews'" (do NOT auto-route to WhatsApp). Report the exact reviews timestamp field + how to compute 90-day staleness deterministically (offline-testable — e.g. a reference 'now' injected or from record). Also report whether horário (opening hours) and preço (price) are available on the record for later eligibility, and where they'd live in normalized.`,
      },
      {
        label: 'scout:promote_recency',
        topic: 'promote_to_mar backstop + TA recency',
        prompt: `Read brave/core/mar/service.py::promote_to_mar IN FULL and brave/lanes/tripadvisor/atrativos.py (how TA atrativos produce normalized records + any review/recency field). Report: what data promote_to_mar has (rio.normalized keys), where to add a recency BACKSTOP for attractions (assert most-recent review within 90 days, else route dlq rather than promote), and what recency/review field TA atrativos carry (so the backstop works for TA records that never pass SignalAgent). Report how to make the backstop offline-deterministic and NOT break destino promotion (destinos have no reviews — the rule is attraction-only).`,
      },
      {
        label: 'scout:gate_whatsapp',
        topic: 'current WhatsApp gate trigger path',
        prompt: `Read brave/api/routers/atrativos_gate.py, brave/compliance/gate.py, brave/lanes/atrativos/whatsapp_agent.py, brave/lanes/atrativos/state_machine.py IN FULL. Report the COMPLETE current WhatsApp path: how a record enters the gate today (sub_state aguardando_consulta_whatsapp), the approve/reject endpoints, how outreach_task / resume_conversation_task are dispatched, the consent/ramp/quality-rating checks, phone extraction (_extract_contact_phone), and the state_machine transitions (advance_sub_state edges, valid sub_states). Goal: convert the trigger to MANUAL — a batch endpoint moves DLQ atrativos into sub_state=aguardando_consulta_whatsapp and branches to LLM-number-discovery vs outreach_task; the old approve/reject gate logic becomes the operator's move action. Report exactly what to reuse vs replace, and the sub_state edges to add (dlq→aguardando_consulta_whatsapp, aguardando_consulta_whatsapp→dlq).`,
      },
      {
        label: 'scout:enrichment_contact',
        topic: 'contact capture + LGPD masking',
        prompt: `Read brave/lanes/atrativos/contact_finder_agent.py, brave/clients/places.py (+ null_places.py), brave/lanes/tripadvisor/atrativos.py, and brave/core/models.py mask_phone. Report: where phone/contact is captured today (normalized["contacts"]["phone_e164"] per _extract_contact_phone), the mask_phone helper, and the Places/TA fields that carry a mobile phone + DDD. Goal: capture a "whatsapp_candidate" (celular+DDD) at normalized["contact"]["whatsapp_candidate"] during enrichment (Places details AND TripAdvisor), stored MASKED (never raw phone rendered in the Kanban projection). Report the existing masked-phone projection used by the CMS/board so we keep it, and the normalize/DDD parsing approach.`,
      },
      {
        label: 'scout:dlq_batch',
        topic: 'DLQ endpoints + eligibility fields',
        prompt: `Read brave/api/routers/dlq.py and the DLQ list/validate endpoints, plus how an atrativo's horário (opening hours) and preço (price) are represented in RioRecord.normalized. Report: the existing DLQ router shape + steward auth dep, the routing='dlq' query, and EXACTLY which normalized keys hold horário and preço for an atrativo (so eligibility "no horário AND no preço" can be checked server-side → 422 otherwise). Report where a new batch endpoint POST (DLQ→WhatsApp) should live (dlq.py or a dedicated router) and how it should enqueue tasks per atrativo.`,
      },
      {
        label: 'scout:tests_f',
        topic: 'affected signal/gate tests',
        prompt: `List every test exercising: SignalAgent routing/scoring, the WhatsApp gate approve/reject (atrativos_gate), outreach/resume tasks, and state_machine sub_state edges. For each report file:line + what it asserts. CRITICAL: the no-reviews/>90d→dlq rule will change SignalAgent routing for review-less atrativos (previously scored → now dlq), and the gate becomes manual — list which tests must be rewritten vs which stay. Give a precise per-file action list so coverage of the new manual flow + no-reviews rule is added, not lost.`,
      },
    ],
    implementSteps: [
      {
        label: 'impl:phaseF-rule',
        effort: 'max',
        prompt: `Implement Phase F PART 1 (no-reviews rule + backstop + whatsapp_candidate capture + sub_state edges) in ${REPO} on branch refactor/spec-driven-alignment. Ground truth = scout findings; Read files if thin. No new migration.

1) brave/lanes/atrativos/signal_agent.py: after place_details, for an ATTRACTION, if there are NO reviews OR the most-recent review is older than 90 days → set routing='dlq', dlq_reason='no_recent_reviews', sub_state=None. Do NOT auto-route to the WhatsApp gate (manual now). Keep the CLOSED/hard-descarte path. Make the 90-day check deterministic/offline-testable (inject or derive 'now' consistently with the codebase). Keep corroboracao=0.0 from Phase E.
2) brave/core/mar/service.py::promote_to_mar: add an attraction-only recency BACKSTOP — if promoting an attraction whose most-recent review is missing or >90d, do NOT promote to Mar; route to dlq (dlq_reason='no_recent_reviews') instead. Destinos (no reviews) are unaffected. Deterministic + offline.
3) Enrichment capture: in the Places details path (contact_finder_agent.py / signal_agent.py as appropriate) AND brave/lanes/tripadvisor/atrativos.py, capture a whatsapp_candidate (celular+DDD) at normalized["contact"]["whatsapp_candidate"], stored via the existing mask_phone (MASKED — never store/return raw phone in the board projection). Reuse the existing masked-phone projection.
4) brave/lanes/atrativos/state_machine.py: add advance_sub_state edges dlq→aguardando_consulta_whatsapp (manual move in) and aguardando_consulta_whatsapp→dlq (no contact found / bounce back). Keep existing edges + whatsapp_in_progress semantics.
5) Tests: update SignalAgent tests for the no-reviews/>90d→dlq rule (add a with-recent-reviews→scored case AND a no-reviews→dlq case AND a >90d→dlq case); add promote_to_mar backstop tests; add whatsapp_candidate capture + masking tests; add state_machine edge tests.

Constraints: ${GUARDRAILS} — especially: WhatsApp/LangGraph/consent/ramp/quality-rating/Twilio preserved; LGPD (no raw phone rendered). Pact byte-identical.
Run ruff on changed files. Do NOT run the full suite. Return structured result.

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
      {
        label: 'impl:phaseF-flow',
        effort: 'max',
        prompt: `Implement Phase F PART 2 (batch DLQ→WhatsApp endpoint + LLM number-discovery task + gate substitution) in ${REPO} on branch refactor/spec-driven-alignment. PART 1 (no-reviews rule, backstop, whatsapp_candidate capture, sub_state edges) is DONE — Read the updated state_machine.py + signal_agent.py first.

1) Batch endpoint (in brave/api/routers/dlq.py or a dedicated router, steward auth + require_editing_unlocked from Phase C): POST that accepts a list of atrativo rio_ids currently in DLQ and moves them to the WhatsApp column. Server-side ELIGIBILITY: only atrativos with NO horário AND NO preço are eligible → return 422 (per-item or whole-batch, clearly) for any that already have horário or preço. For each eligible atrativo set sub_state=aguardando_consulta_whatsapp and BRANCH:
   - has celular+DDD (normalized["contact"]["whatsapp_candidate"] present) → dispatch outreach_task (LangGraph conversation, existing consent/ramp/quality/Twilio path) via the dispatch-then-inline-fallback idiom used elsewhere.
   - no celular+DDD → dispatch the new LLM number-discovery task.
2) LLM number-discovery task (new @shared_task on the single 'celery' queue, name like brave.discover_whatsapp_number, or a branch of reprocess/resume): tries to discover a WhatsApp number for the atrativo (offline: Null LLM → no number → route back to sub_state or dlq with reason no_contact_found; real: opt-in). On found → populate whatsapp_candidate + proceed to outreach; on not found → sub_state back to dlq (aguardando_consulta_whatsapp→dlq) with dlq_reason no_contact_found.
3) Gate substitution: the old atrativos_gate approve/reject flow is REPLACED by this manual move. Keep the WhatsApp subsystem (outreach_task/resume_conversation_task/LangGraph/consent/ramp/quality/Twilio) intact; repurpose or remove the old approve/reject endpoints so the manual move is the single entry. The owner-confirmation still injects validacao_humana_value=100 → reprocess_record → ≥80 → Mar (preserve validate_and_promote_rio mechanics). Do NOT break transition_atrativo's whatsapp edge (Phase C).
4) Tests: batch endpoint — eligible (no horário+preço) moves + dispatches correct branch; ineligible (has horário or preço) → 422; no-celular → LLM task path; with-celular → outreach; owner-confirm → validacao_humana=100 → re-score ≥80 → Mar; phone masked in projections; edit-lock 423 when LIGADO on the batch move.

Constraints: ${GUARDRAILS}. Single 'celery' queue (no task_routes). Offline default. LGPD masking.
Run ruff on changed files. Do NOT run the full suite. Return structured result.

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
    ],
    reviewers: [
      {
        label: 'review:no_reviews_rule',
        prompt: `Review Phase F no-reviews/>90d rule + backstop. Confirm: SignalAgent routes attractions with no reviews OR most-recent review >90d to dlq (dlq_reason=no_recent_reviews), NOT to any auto-gate; the 90-day check is deterministic/offline; CLOSED hard-descarte path intact. promote_to_mar has an attraction-only recency backstop that stops promoting stale/reviewless attractions (routes dlq) while destinos are unaffected. TA atrativos (which bypass SignalAgent) are covered by the backstop. Flag any case where a stale attraction could still reach Mar, or where destinos regressed.`,
      },
      {
        label: 'review:whatsapp_flow',
        prompt: `Review Phase F manual WhatsApp flow. Confirm: the batch DLQ→WhatsApp endpoint is steward-auth + edit-locked (423 when LIGADO); eligibility enforced server-side (only no-horário AND no-preço; 422 otherwise); per-atrativo branch correct (whatsapp_candidate present → outreach_task; absent → LLM number-discovery task); sub_state edges dlq↔aguardando_consulta_whatsapp wired; owner-confirm → validacao_humana=100 → reprocess → ≥80 → Mar preserved. The WhatsApp subsystem (LangGraph/consent/ramp/quality/Twilio) is preserved, not reimplemented. Old approve/reject gate correctly substituted (no dead dual path). Single 'celery' queue. Flag gaps.`,
      },
      {
        label: 'review:lgpd',
        prompt: `Review Phase F LGPD posture. Confirm whatsapp_candidate (celular+DDD) is stored MASKED via mask_phone and NEVER rendered raw in the Kanban/board projection or API responses; the existing masked-phone projection is reused; no raw phone leaks into audit logs or conversation_message (mask_phone at write time). Flag any raw-phone exposure introduced by the enrichment capture or the batch endpoint.`,
      },
      {
        label: 'review:tests_f',
        prompt: `Review Phase F tests. Confirm added coverage: no-reviews→dlq, >90d→dlq, with-recent-review→scored; promote backstop; whatsapp_candidate capture+masking; state_machine new edges; batch endpoint eligible-move + 422-ineligible + branch selection (LLM vs outreach) + edit-lock 423; owner-confirm→Mar. Confirm old gate approve/reject tests were rewritten to the manual flow (not left asserting removed behavior). Flag weakened coverage.`,
      },
    ],
  },
  G: {
    goal: 'Domainização: create domains/{mtur,tripadvisor,manual}/ (controllers/services/repositories/models/dtos/exceptions); move lane files with import updates; state_machine→core/atrativos/; whatsapp_agent+conversation→shared/whatsapp/; domains/base.py SourceDomain Protocol + registry (get_domain/enabled_sources); pipeline.py resolves Domain via registry (never names a source). Manual domain uses source="manual". Adapt import-rule test: core/shared NEVER import domains/tasks; domains never import each other. Keep the suite GREEN — use thin re-export shims at old module paths for high-fanout modules where a full import sweep is risky, and document them.',
    scouts: [
      {
        label: 'scout:lane_inventory',
        topic: 'lane files + every importer',
        prompt: `Inventory brave/lanes/ COMPLETELY (post Phase E/F): list every file under lanes/{atrativos,destinos,tripadvisor}/ + lanes/base.py, and for EACH module every importer across brave/ AND tests/ (file:line + exact import). Focus on the high-fanout ones: lanes/atrativos/{signal_agent,contact_finder_agent,discovery_agent,state_machine,whatsapp_agent,schemas}.py, lanes/destinos/{mtur,schemas}.py, lanes/tripadvisor/{atrativos,client,destinos,geo,ibge,schemas,scoring,session,sweep_progress,uf_names}.py. Goal: plan a move to domains/{mtur,tripadvisor,manual}/ + core/atrativos/ + shared/whatsapp/ with import updates. Flag the modules with the most importers (shim candidates) and any test that monkeypatches a module PATH string (those break on move unless a shim keeps the old path importable).`,
      },
      {
        label: 'scout:pipeline_registry',
        topic: 'pipeline source branching',
        prompt: `Read brave/tasks/pipeline.py + brave/tasks/beat_schedule.py + brave/core/engine.py (_VALID_SOURCES, set_source) and brave/config/runtime.py (enabled_sources). Report every place that NAMES a specific source ("mtur"/"default"/"tripadvisor") or imports a specific lane (sweep_uf → MturSeedIngest; sweep_tripadvisor → TripAdvisorAtrativosIngest; discover/find_contacts/gather_signals → atrativos agents). Goal: introduce domains/base.py Protocol SourceDomain(name, produces, discover(uf,run_rio), enrich(rio)->dict, score_input(payload)->ScoreInput) + a registry (get_domain, enabled_sources) so pipeline/beat/engine iterate the registry instead of naming sources. Report the minimal seam to route through the registry while keeping single-source-per-run (brave:engine:source) and the single celery queue.`,
      },
      {
        label: 'scout:whatsapp_statemachine',
        topic: 'state_machine + whatsapp move targets',
        prompt: `Read brave/lanes/atrativos/state_machine.py and brave/lanes/atrativos/whatsapp_agent.py IN FULL + list all their importers (brave/ + tests/). Also check brave/lanes/atrativos/schemas.py for which schemas are WhatsApp-conversation-related (move to shared/whatsapp) vs signal/atrativo-related (stay with the mtur domain). Goal: move state_machine.py → brave/core/atrativos/state_machine.py and whatsapp_agent.py (+ conversation graph) → brave/shared/whatsapp/agent.py (+ conversation.py). Report exact importers to update (or shim) and confirm shared/whatsapp will not violate the import rule (shared must not import domains/tasks — check whatsapp_agent's current imports).`,
      },
      {
        label: 'scout:import_rule_test',
        topic: 'import-rule test to extend',
        prompt: `Read tests/unit/test_no_test_imports_in_brave.py IN FULL. Report exactly how it walks brave/ and matches imports. Goal: EXTEND it (or add a sibling test) to also enforce D-18 generalized: brave.core and brave.shared must NOT import brave.domains or brave.tasks; brave.domains.<x> must NOT import brave.domains.<y> (no cross-domain imports). Report how to implement these checks in the same style (AST or regex over rglob) without false-positives on the new shims.`,
      },
    ],
    implementSteps: [
      {
        label: 'impl:phaseG-domains',
        effort: 'max',
        prompt: `Implement Phase G STEP 2 (domain packages + registry) in ${REPO} on branch refactor/spec-driven-alignment. STEP 1 (state_machine->core/atrativos, whatsapp->shared/whatsapp) is done — Read those first. KEEP THE SUITE GREEN (use re-export shims at old lane paths for high-fanout modules; document them).

1) brave/domains/base.py: Protocol SourceDomain (name, produces, discover(uf, run_rio), enrich(rio)->dict, score_input(payload)->ScoreInput). brave/domains/__init__.py: registry with get_domain(name) and enabled_sources(config); register mtur + tripadvisor + manual. Adding a source = new package + 1 registry line.
2) brave/domains/mtur/{__init__,controllers,services,repositories,models,dtos,exceptions}.py: move brave/lanes/destinos/mtur.py (MturSeedIngest) + brave/lanes/atrativos/{discovery_agent,contact_finder_agent,signal_agent}.py (Places default track) into services.py (or submodules). MturRepository (build_destino_rio_map(uf), CSV seed path). Implement SourceDomain for mtur/default.
3) brave/domains/tripadvisor/{__init__,controllers,services,repositories,client,models,dtos,exceptions}.py: move brave/lanes/tripadvisor/* here. TripAdvisorRepository (sweep_progress + Redis session + geo/ibge caches). Implement SourceDomain for tripadvisor.
4) brave/domains/manual/{__init__,controllers,services,repositories,models,dtos,exceptions}.py (NEW): CRUD facade over Nascente/Rio using source="manual" with origem_value/validacao_humana_value=100 in the payload. Wire require_editing_unlocked (Phase C) on mutations. Minimal but functional + tests.
5) Leave thin re-export shims at old lane module paths so existing imports/tests keep working; domains must never import each other (only kernel + clients).

Do NOT run the full suite. Run ruff. Return structured result.

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
      {
        label: 'impl:phaseG-wire',
        effort: 'max',
        prompt: `Implement Phase G STEP 3 (registry wiring + import-rule test) in ${REPO} on branch refactor/spec-driven-alignment. STEPS 1-2 done — Read domains/base.py + domains/__init__.py registry first.

1) brave/tasks/pipeline.py + beat_schedule.py: refactor so tasks resolve the Domain via the registry (get_domain / enabled_sources) instead of naming a source or importing a specific lane. Keep single-source-per-run (brave:engine:source), the single 'celery' queue, quarantine/idempotency/depth-gate behavior, and the Pact push path byte-identical.
2) brave/core/engine.py set_source + brave/api/routers/engine.py /engine/start: validate source against registered AND enabled (enabled_sources).
3) Extend tests/unit/test_no_test_imports_in_brave.py (or a sibling) to enforce: brave.core/brave.shared NEVER import brave.domains/brave.tasks; brave.domains.<x> never imports brave.domains.<y>. Make it pass against the new layout (shims at brave.lanes are allowed since lanes is neither core nor shared nor a domain).
4) Add domains/*/tests/ smoke tests for the SourceDomain contract + manual CRUD.

Do NOT run the full suite. Run ruff. Return structured result including any module still needing a shim.

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
    ],
    reviewers: [
      {
        label: 'review:import_rule',
        prompt: `Review Phase G against the generalized import rule (D-18). Grep to CONFIRM: no brave.core or brave.shared module imports brave.domains or brave.tasks; no brave.domains.<x> imports brave.domains.<y> (cross-domain); shared/whatsapp does not import domains/tasks. Confirm the extended import-rule test actually encodes these and would FAIL if violated (not a no-op). Report any violation with file:line.`,
      },
      {
        label: 'review:registry',
        prompt: `Review the SourceDomain registry + pipeline wiring. Confirm domains/base.py Protocol + domains/__init__ registry (get_domain/enabled_sources) exist and that pipeline.py/beat_schedule.py/engine resolve sources through the registry rather than hardcoding source names (a new source = new package + 1 registry line). Confirm single-source-per-run + single 'celery' queue preserved, and mtur/tripadvisor/manual each implement the contract. Report gaps.`,
      },
      {
        label: 'review:no_breakage',
        prompt: `Review Phase G for breakage. Confirm moved modules (state_machine->core/atrativos, whatsapp->shared/whatsapp, lanes->domains) either updated every importer or left a working re-export shim at the old path (no dangling import). Check that test monkeypatch PATH strings still resolve (a moved module referenced by string in setattr breaks unless shimmed). Confirm the Pact push path + build_push_payload untouched. List any import that will fail at collection time.`,
      },
    ],
  },
  H: {
    frontend: true,
    skipBackendTest: true,
    goal: 'Frontend painel único (dashboard/, Next.js): redirect / → /painel; remove standalone dark routes + hub (keep /login + /painel; mar-ready already gone); add /painel views DLQ/Revisão, Monitor/Funis, Config (fontes on/off, thresholds/pesos, modo do motor); Kanban maps descarte→Falha column (no descarte column); WhatsApp column fed by multi-select of DLQ atrativo cards + batch "Mover para WhatsApp" (disable ineligible = has horário+preço, handle 422 toast, masked phone LGPD); edit-lock (cards editable only when motor Pausado/Desligado; 423 → toast + revert; wire engine status mode/editing_unlocked); topbar tri-state Pausar. Reuse gate-api/conversations-api in views. Update MSW mocks/handlers + Vitest tests; keep offline posture (onUnhandledRequest:"error").',
    scouts: [
      {
        label: 'scout:routes',
        topic: 'dashboard routes + redirect',
        prompt: `In ${REPO}/dashboard map app/ COMPLETELY: every route dir (app/{processo,monitor,cost,funnels,dlq,gate,conversations,destinos,atrativos,painel,login}/ + the hub/root page.tsx). For each dark standalone route report its page + any component/lib it uniquely owns. Report how / (root) currently renders (hub?) and how to redirect / → /painel (Next.js App Router redirect). Report app/layout.tsx nav entries linking to dark routes. Goal: remove the standalone dark routes + hub, keep /login + /painel, redirect / → /painel, and confirm which lib/*-api.ts + components are reused by /painel views (do NOT delete those) vs only by dark routes (safe to delete).`,
      },
      {
        label: 'scout:painel_views',
        topic: 'painel views + nav',
        prompt: `In ${REPO}/dashboard read components/painel/{nav.ts,PainelShell,PainelSidebar,PainelView,PainelBoard,PainelConversas,PainelCusto,PainelLogs,PainelMetrics,PainelDuplicados,PainelMapeamento,PainelVarreduras}.tsx + app/painel/page.tsx. Report the current 6 views, how nav.ts registers them, how PainelView switches, and the shared shell. Goal: ADD views DLQ/Revisão (reuse dlq-api/gate-api), Monitor/Funis (reuse monitor-api/funnels-api), Config (fontes on/off + thresholds/pesos + modo do motor via a new config-api against GET/PATCH /api/v1/config). PainelLogs already exists — register it as a view too. Report exactly how to add a view + nav entry in the existing pattern.`,
      },
      {
        label: 'scout:kanban_whatsapp',
        topic: 'kanban columns + WhatsApp + edit-lock',
        prompt: `In ${REPO}/dashboard read lib/painel-data.ts (COLUMN_DEFS + record mapping), lib/painel-actions.ts (drag allow-list), components/painel/{PainelBoard,RecordCard,PainelTopbar}.tsx, and lib/engine-api.ts. Report: the current column set (incl. any 'descarte' column) + how routing maps to columns + windowing; the drag allow-list edges; how the topbar toggles the motor (on/off) and reads engine status. Goal: (a) map descarte routing → a 'Falha' column (remove a standalone descarte column; keep Nascente/Rio/WhatsApp/Mar/DLQ/Falha); (b) WhatsApp column = multi-select of DLQ atrativo cards + a batch "Mover para WhatsApp" button hitting POST /api/v1/dlq/whatsapp-batch, disabling ineligible cards (has horário AND preço) and toasting the 422 per-item reasons, phone masked; (c) edit-lock: cards editable/draggable only when engine mode is PAUSADO/DESLIGADO, else block + toast on 423 and revert; wire status.mode/status.editing_unlocked; (d) topbar tri-state Ligar/Pausar/Desligar. Report the exact seams.`,
      },
      {
        label: 'scout:mocks_tests',
        topic: 'MSW mocks + vitest',
        prompt: `In ${REPO}/dashboard read mocks/{server.ts,browser.ts,handlers/index.ts + handlers/*}, the vitest setup (onUnhandledRequest:"error"), and lib/api-client.ts. Report how handlers are registered, the Bearer-header auth mock, and which backend endpoints already have handlers. Goal: add MSW handlers for GET/PATCH /api/v1/config, POST /api/v1/dlq/whatsapp-batch (200 + 422 ineligible cases), and the engine mode/status (mode/editing_unlocked) — matching the real FastAPI shapes — and update Vitest tests for the new views + WhatsApp multi-select + edit-lock, keeping onUnhandledRequest:"error" (no unhandled calls). Report the exact handler + test patterns to follow.`,
      },
    ],
    implementSteps: [
      {
        label: 'impl:phaseH-routes-views',
        effort: 'max',
        prompt: `Implement Phase H PART 1 (route consolidation + new views) in ${REPO}/dashboard on branch refactor/spec-driven-alignment. Ground truth = scout findings. KEEP bun test green + offline posture.

1) Redirect / → /painel (App Router). Remove the standalone dark route dirs (app/{processo,monitor,cost,funnels,dlq,gate,conversations,destinos,atrativos}/) + the hub, keeping app/login + app/painel. Remove dark-route nav links from app/layout.tsx. Delete lib/*-api.ts + components ONLY used by removed dark routes; REUSE (do not delete) any consumed by /painel views.
2) Add /painel views + register in components/painel/nav.ts: DLQ/Revisão (reuse dlq-api + gate-api), Monitor/Funis (reuse monitor-api + funnels-api), Logs (PainelLogs, register it), Config (new lib/config-api.ts → GET/PATCH /api/v1/config: fontes on/off, thresholds/pesos with weight-sum-100 client validation, modo do motor). Follow the existing PainelView/nav pattern.
3) Update mocks/handlers for any endpoint the new views call, and Vitest tests, keeping onUnhandledRequest:"error".

Do NOT run the full suite. Run the dashboard lint if quick. Return a concise plain-text summary (files removed/added/edited).

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
      {
        label: 'impl:phaseH-kanban',
        effort: 'max',
        prompt: `Implement Phase H PART 2 (Kanban WhatsApp flow + edit-lock) in ${REPO}/dashboard on branch refactor/spec-driven-alignment. PART 1 (routes + views) done — Read nav.ts + PainelView first. KEEP bun test green + offline.

1) Kanban columns (lib/painel-data.ts): map routing 'descarte' → a 'Falha' column; column set = Nascente/Rio/WhatsApp/Mar/DLQ/Falha (no standalone descarte column). Keep windowing + drag allow-list (lib/painel-actions.ts) adjusted to the new routing (no →descarte edge).
2) WhatsApp column MANUAL flow: multi-select of DLQ atrativo cards + a batch "Mover para WhatsApp" button → POST /api/v1/dlq/whatsapp-batch. Disable selection of ineligible atrativos (those that already have horário AND preço). Handle the 422 (per-item ineligibility) with a toast. Show the branch feedback (LLM number-discovery vs conversa iniciada). Phone stays masked (LGPD) on cards; transcripts remain in the Conversas view.
3) Edit-lock: cards editable/draggable ONLY when engine mode is PAUSADO/DESLIGADO; when LIGADO block the mutation and, on a 423 from the backend, toast + revert the optimistic move. Wire engine status.mode/status.editing_unlocked (engine-api). Topbar: tri-state Ligar/Pausar/Desligar (POST engine mode).
4) Update mocks/handlers (whatsapp-batch 200 + 422, engine mode/status) + Vitest tests for multi-select, 422 toast, edit-lock 423 revert, descarte→Falha mapping. onUnhandledRequest:"error".

Do NOT run the full suite. Run lint if quick. Return a concise plain-text summary.

--- SCOUT FINDINGS ---
{{SCOUTS}}`,
      },
    ],
    reviewers: [
      {
        label: 'review:routes',
        prompt: `Review Phase H route consolidation in ${REPO}/dashboard. Confirm: / redirects to /painel; the standalone dark routes (processo/monitor/cost/funnels/dlq/gate/conversations/destinos/atrativos) + hub are removed; /login + /painel remain; no dead <Link>/import to a removed route or deleted lib/*-api (grep). New views (DLQ, Monitor/Funis, Logs, Config) are registered in nav.ts and render. Report any dangling reference.`,
      },
      {
        label: 'review:whatsapp_editlock',
        prompt: `Review Phase H Kanban/WhatsApp/edit-lock. Confirm: descarte routing maps to a 'Falha' column (no standalone descarte column) and the drag allow-list has no →descarte edge; WhatsApp column is fed by multi-select of DLQ atrativo cards + a batch move to POST /api/v1/dlq/whatsapp-batch, ineligible cards (horário+preço) disabled, 422 handled with a toast, phone masked; edit-lock blocks mutations when engine LIGADO and handles 423 (toast + revert) using status.mode/editing_unlocked; topbar is tri-state. Flag gaps or LGPD leaks (raw phone).`,
      },
      {
        label: 'review:offline_tests',
        prompt: `Review Phase H mocks/tests in ${REPO}/dashboard. Confirm MSW handlers exist for GET/PATCH /api/v1/config, POST /api/v1/dlq/whatsapp-batch (200 + 422), and engine mode/status, matching the real FastAPI shapes; onUnhandledRequest:"error" is intact and no test triggers an unhandled request; new Vitest coverage exists for the new views + WhatsApp multi-select + edit-lock. Flag any unhandled-request risk or missing coverage.`,
      },
    ],
  },
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------
let _args = args
if (typeof _args === 'string') {
  try { _args = JSON.parse(_args) } catch (e) { _args = { phase: _args } }
}
const PHASE = (_args && _args.phase) || 'A'
const spec = PHASES[PHASE]
if (!spec) throw new Error(`Unknown phase: ${PHASE}`)
if (spec.stub) {
  throw new Error(
    `Phase ${PHASE} not yet authored. Edit the script (fill PHASES.${PHASE}) before invoking — later phases depend on earlier outcomes.`
  )
}

log(`Phase ${PHASE}: ${spec.goal}`)

phase('Scout')
const scouts = await parallel(
  spec.scouts.map((s) => () =>
    agent(`You are a READ-ONLY scout for the Brave refactor (phase ${PHASE}). Repo: ${REPO}.\n${s.prompt}`, {
      label: s.label,
      phase: 'Scout',
      schema: SCOUT_SCHEMA,
    })
  )
)
const scoutBlob = JSON.stringify(scouts.filter(Boolean), null, 2)

phase('Implement')
const implResults = []
for (const step of spec.implementSteps) {
  const prompt = step.prompt.replace('{{SCOUTS}}', scoutBlob)
  // NOTE: implement steps return PLAIN TEXT (no schema). A strict StructuredOutput
  // schema on a long, heavy implement agent can exceed the retry cap and abort the
  // whole run after hours of real edits. The orchestrator reads the git diff for truth.
  const res = await agent(prompt + '\n\nReturn a concise plain-text summary: files moved/created/edited, any re-export shims left, and anything still needing a follow-up.', {
    label: step.label,
    phase: 'Implement',
    effort: step.effort || 'high',
  })
  implResults.push(res)
}

phase('Review')
const reviews = await parallel(
  spec.reviewers.map((r) => () =>
    agent(
      `You are a strict READ-ONLY reviewer for the Brave refactor (phase ${PHASE}). Repo: ${REPO}. Inspect the actual working tree / git diff.\n${r.prompt}\n\n${GUARDRAILS}`,
      { label: r.label, phase: 'Review', effort: 'high', schema: REVIEW_SCHEMA }
    )
  )
)

phase('Self-test')
const test = spec.skipBackendTest
  ? null
  : await agent(
      `Run the backend test suite and report results. Run EXACTLY: ${TEST_CMD}\nThen report passed=true only if pytest exit status is 0 (look for the summary line like "N passed"). List any failing test node IDs and paste the last ~40 lines. Do not fix anything.`,
      { label: 'self-test', phase: 'Self-test', schema: TEST_SCHEMA }
    )

let frontendTest = null
if (spec.frontend) {
  frontendTest = await agent(
    `Run the dashboard test suite and report results. Run EXACTLY: cd ${REPO}/dashboard && bun run test 2>&1 | tail -40\nReport passed=true only if the vitest run exits 0 (look for "Test Files N passed" with no failures). List any failing test files and paste the last ~30 lines. Do not fix anything.`,
    { label: 'self-test:frontend', phase: 'Self-test', schema: TEST_SCHEMA }
  )
}

return {
  phase: PHASE,
  goal: spec.goal,
  scouts: scouts.filter(Boolean),
  implementation: implResults,
  reviews: reviews.filter(Boolean),
  selfTest: test,
  frontendTest,
}
