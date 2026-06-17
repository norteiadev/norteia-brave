# Phase 5: Auto-Discovery Orchestration - Pattern Map

**Mapped:** 2026-06-17
**Files analyzed:** 7 source files to modify/create + 3 test files
**Analogs found:** 7 / 7 (every new/modified file has an in-repo analog — this is gap-closure wiring, not greenfield)

> This is a **backend Celery orchestration phase, no frontend**. Every pattern below is a concrete excerpt with `file:line`. The planner should have each plan's action section reference the analog file + line range and copy the structure verbatim, changing only the task name / producer composition.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `brave/tasks/pipeline.py` → ADD `sweep_uf(uf)` | task (Celery producer) | batch / event-driven | `discover_atrativo_task` (same file, `pipeline.py:635`) | **exact** (same module, same quarantine+session shape, both run `agent.produce(uf)`) |
| `brave/tasks/pipeline.py` → MODIFY `discover_atrativo_task` (add per-record enqueue tail) | task | event-driven (fan-out) | gate dispatch `atrativos_gate.py:376-396` + the orphaned `find_contacts_task` (`pipeline.py:732`) | **role-match** (no existing in-task `.delay` fan-out; closest is the gate's single dispatch) |
| `brave/tasks/pipeline.py` → MODIFY `find_contacts_task` / `gather_signals_task` (add enqueue-next tail) | task | event-driven (chain) | `gate.py:376` dispatch + `find_contacts_task` body itself (`pipeline.py:732`/`819`) | **exact** body, **role-match** for the new enqueue tail |
| `brave/cli.py` → ADD `sweep` subcommand | CLI / utility | request-response (sync trigger) | `run-fixture` cmd (`cli.py:17-122`, dispatch `main()` `cli.py:125-143`) | **role-match** (extends the same argv parser; dispatch-or-inline borrowed from routers) |
| `brave/api/routers/sweep.py` (OPTIONAL `/api/v1/sweep`) | route (controller) | request-response | `dlq.py:104-114` reprocess endpoint (dispatch+inline-fallback) | **exact** (Bearer dep + try `.delay` except inline) |
| `brave/tasks/beat_schedule.py` | config | scheduled (cron) | **no change** — entry `sweep-{uf}-daily → brave.sweep_uf` already present (`beat_schedule.py:43-49`) | n/a (must resolve, do NOT rename) |
| `tests/integration/test_celery_tasks.py` (or new `tests/unit/test_sweep_uf.py`) | test | — | `test_celery_tasks.py:16-71` (idempotency + quarantine) + `test_state_machine.py` + `test_mtur_lane.py` | **exact** |

---

## Pattern Assignments

### `sweep_uf(uf)` — NEW task in `brave/tasks/pipeline.py` (ORCH-01, D-01/D-02)

**Analog:** `discover_atrativo_task` — `brave/tasks/pipeline.py:626-720`. Copy its decorator, session lifecycle, quarantine wrapper verbatim; swap the agent body to compose the two destino producers.

**Decorator + signature pattern** (`pipeline.py:626-635`):
```python
@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.sweep_uf",          # MUST be exactly this — beat_schedule.py:45 expects it
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=600,                 # producers fan out per-município; allow headroom
)
def sweep_uf(self, uf: str) -> None:
```

**Session lifecycle** (`pipeline.py:651`, `718-720`) — every task uses `_get_session()` (`pipeline.py:220-230`, reads `BRAVE_DB_URL`) then closes in `finally`:
```python
session, engine = _get_session()
try:
    ...
finally:
    session.close()
    engine.dispose()
```

**Body to compose** — instantiate the two producers exactly as the gate-free agents are built, then `asyncio.run(...)` each (the `asyncio.run` per-task event-loop pattern is `pipeline.py:680`, also `push_mar` `pipeline.py:461`):
```python
config = ScoreConfig()
app_config = AppConfig()

# Mtur seed re-ingest (idempotent — store_raw dedups by content_hash; mtur.py:112)
from brave.clients.mtur import MturClient
seed = MturSeedIngest(MturClient(), session, config)         # ctor: mtur.py:95-100
asyncio.run(seed.produce(uf))                                # produce: mtur.py:105

# Desmembramento — the real recurring LLM discovery (origem=40 firewall, desmembramento.py:140)
# LLM client selection MUST mirror discover_atrativo_task:660-671 (real vs fake by run_real_externals)
if app_config.run_real_externals:
    from brave.clients.llm import RealLLMClient
    llm_client = RealLLMClient(config=app_config.llm)
else:
    from tests.fakes.fake_llm import FakeLLMClient
    llm_client = FakeLLMClient()
desm = DesmembramentoAgent(llm_client, MturClient(), session, config)  # ctor: desmembramento.py:128-134
asyncio.run(desm.produce(uf))                                # produce: desmembramento.py:140
session.commit()
```

**Quarantine wrapper** (copy verbatim from `pipeline.py:683-716`) — `PermanentError → quarantine_poison` on a fresh session; generic `except → self.retry(...)` then `MaxRetriesExceededError → quarantine_poison`. Use `task_name="brave.sweep_uf"`, `payload={"uf": uf}` (exact shape at `pipeline.py:688-693`).

**Producer constructor signatures (verified):**
- `MturSeedIngest(mtur_client, session, config)` — `mtur.py:95-100`. `produce(uf)` is async, returns `None`.
- `DesmembramentoAgent(llm_client, mtur_client, session, config)` — `desmembramento.py:128-134`. Note arg order: **llm first, then mtur**. `produce(uf)` async, returns `None`, only acts on `categoria == "Oferta Principal"` municipalities (`desmembramento.py:157`).
- `MturClient()` — no-arg, offline CSV reader (`mtur.py:86-129`). Raises `FileNotFoundError` if no `data/mtur/municipios_mtur_*.csv` — wrap in the quarantine path.

**D-02 note:** Both producers already call `store_raw` + `process_nascente_record` internally (`mtur.py:149-162`); `sweep_uf` adds NO scoring branch — it is producer-only. Records land in DLQ/Mar/descarte by §7.6 automatically.

---

### `discover_atrativo_task` MODIFY — add the per-record enqueue tail (ORCH-02, D-03)

**Analog for the body:** unchanged (`pipeline.py:635-720`). **Analog for the NEW tail:** the dispatch-or-inline pattern at `atrativos_gate.py:376-396` (there is NO existing in-task `.delay` fan-out — this is the gap being closed; the gate's single dispatch is the closest shape).

**Critical gap discovered (planner MUST resolve):** No production code currently sets `sub_state="discovered"`. `DiscoveryAgent.produce()` ends after `store_raw` + audit (`discovery_agent.py:314-339`) and `route_by_score` (`routing.py:194`) does **not** set `sub_state`. The chain in D-03 queries `RioRecord ... sub_state='discovered'`, so **either** DiscoveryAgent must set `sub_state='discovered'` on the created Rio, **or** the enqueue query must key off a different signal (e.g. `entity_type='attraction' AND sub_state IS NULL AND uf=uf`). The contact_finder guard (`contact_finder_agent.py:72`) already expects `sub_state == "discovered"`, so the consistent fix is to set `discovered` at discovery time. Flag this explicitly in the plan.

**Enqueue tail to ADD** (after `asyncio.run(agent.produce(uf))` + commit, `pipeline.py:680-681`), keyed by `sub_state` not return value (D-03 — `produce` returns `None`):
```python
from sqlalchemy import select
from brave.core.models import RioRecord

discovered = session.scalars(
    select(RioRecord).where(
        RioRecord.entity_type == "attraction",
        RioRecord.uf == uf,
        RioRecord.sub_state == "discovered",
    )
).all()
for rio in discovered:
    try:
        find_contacts_task.delay(str(rio.id))      # task name "brave.find_contacts" (pipeline.py:727)
    except Exception:
        # Sync fallback (no broker) — mirror atrativos_gate.py:379 / dlq.py:109
        find_contacts_task.run(str(rio.id))         # or call the task fn directly
```

**Dispatch-or-inline reference** (`atrativos_gate.py:376-396` — the canonical pattern; note prod-vs-offline split at `:382`, and `dlq.py:104-114` for the simpler swallow-all variant):
```python
try:
    outreach_task.delay(str(rio_id))
except Exception as exc:
    if AppConfig().run_real_externals:
        logger.error("outreach_dispatch_failed", ...); raise HTTPException(503, ...)
    # offline: no broker expected — test invokes task directly
```

---

### `find_contacts_task` / `gather_signals_task` MODIFY — chain to next step (ORCH-02, D-03/D-04)

**Analog body:** unchanged (`find_contacts_task` `pipeline.py:723-807`; `gather_signals_task` `pipeline.py:810-904`). Both already run their agent via `asyncio.run(agent.run(rio))` and commit. **Add only the enqueue-next tail.**

- After `find_contacts_task` succeeds (rio now `contacts_found` — set by `contact_finder_agent.py:109`), enqueue `gather_signals_task.delay(str(rio_id))` with the same try/except-inline fallback.
- `gather_signals_task` is the **terminal** auto-step. `SignalAgent.run()` already advances `contacts_found → signals_gathered` (`signal_agent.py:262`) then `route_by_score`; a borderline (`routing == "dlq"`) becomes `sub_state="aguardando_consulta_whatsapp"` (`signal_agent.py:281-282`) and **STOPS** (human gate, D-07). **Do NOT enqueue anything after `gather_signals_task`** — `outreach_task` is dispatched ONLY by the gate approve (`atrativos_gate.py:378`, leave unchanged).

**D-04 idempotency note (important nuance for the planner):** The CONTEXT says "every advancing task MUST gate behind `advance_sub_state`." Today the agents do NOT call `advance_sub_state` (`state_machine.py:28`) — they inline their own guard (`contact_finder_agent.py:72`: `if rio.sub_state != "discovered": return`; `signal_agent.py:172`). That inline guard already provides the replay-safety D-04 relies on (a duplicate dispatch re-reads the advanced state and no-ops). The planner should decide: (a) leave the agents' inline guards as-is (they are functionally equivalent and unit-tested), or (b) refactor them to call `advance_sub_state` for the with_for_update lock + audit-row guarantee. Option (a) is lower-risk for pure wiring; option (b) better matches D-04's letter. The `advance_sub_state` guard contract:

**`advance_sub_state` guard** (`state_machine.py:66-90`):
```python
if lock:  # re-fetch under SELECT ... FOR UPDATE before the guard (CR-04)
    locked = session.get(RioRecord, rio.id, with_for_update=True)
    if locked is not None: rio = locked
if rio.sub_state != expected_state:
    return False                      # idempotent no-op — already advanced
write_audit(session, action="sub_state_advanced", before_state=..., after_state=..., actor=...)
rio.sub_state = next_state
session.flush()
return True
```

---

### `brave/cli.py` — ADD `sweep <UF> [--lane destinos|atrativos|both]` (ORCH-03, D-05)

**Analog:** `run-fixture` command — arg dispatch `cli.py:125-143`, body `cli.py:17-122`. Extend the same `sys.argv`-based `main()`; the file uses plain `sys.argv` parsing (no argparse), so `--lane` parsing is the planner's discretion (D-05 says "precise CLI arg parsing" is open).

**Dispatch-then-inline-fallback** — mirror `dlq.py:104-114` / `atrativos_gate.py:376-396` so an operator can run a real sweep with no worker:
```python
def _run_sweep(uf: str, lane: str = "both") -> None:
    if lane in ("destinos", "both"):
        try:
            from brave.tasks.pipeline import sweep_uf
            sweep_uf.delay(uf)
        except Exception:
            from brave.tasks.pipeline import sweep_uf
            sweep_uf.run(uf)            # inline — no broker
    if lane in ("atrativos", "both"):
        try:
            from brave.tasks.pipeline import discover_atrativo_task
            discover_atrativo_task.delay(uf)
        except Exception:
            from brave.tasks.pipeline import discover_atrativo_task
            discover_atrativo_task.run(uf)
```

**Dispatch in `main()`** — add `elif command == "sweep":` alongside `cli.py:139` `if command == "run-fixture":`. Update the usage text (`cli.py:133-135`).

**`_get_session()` env note:** the inline path runs the real task which calls `_get_session()` (`pipeline.py:220-230`) → needs `BRAVE_DB_URL`. `run-fixture` already degrades gracefully when it's unset (`cli.py:28-32`) — mirror that messaging.

---

### `/api/v1/sweep` endpoint (OPTIONAL — D-05 nice-to-have, planner discretion)

**Analog:** `dlq.py:96-124` reprocess endpoint — Bearer dep + dispatch + inline fallback. If added, copy:
```python
@router.post("/api/v1/sweep", status_code=202, dependencies=[Depends(require_steward_or_bearer)])
def sweep_endpoint(uf: str, lane: str = "both", db: Session = Depends(get_db)) -> dict:
    try:
        sweep_uf.delay(uf)
    except Exception:
        sweep_uf.run(uf)        # offline inline
    return {"status": "accepted", "uf": uf}
```
Bearer dep is `require_steward_or_bearer` (used throughout `dlq.py:130`, `atrativos_gate.py`). The CLI is the required surface; this endpoint is optional.

---

### `brave/tasks/beat_schedule.py` — NO CHANGE (must resolve)

The entry already exists (`beat_schedule.py:42-49`): `sweep-{uf}-daily → task "brave.sweep_uf" @ crontab(hour=2)` on queue `brave.sweep`. **Do NOT rename** — implementing `sweep_uf` with `name="brave.sweep_uf"` (D-01) makes it resolve. The atrativos entry (`beat_schedule.py:56-63`, `→ brave.discover_atrativo @ 3 AM`) is already real. Tasks are auto-discovered via `app.autodiscover_tasks(["brave.tasks"])` (`celery_app.py:56`).

---

## Shared Patterns

### Quarantine wrapper (every producer/FSM task)
**Source:** `brave/tasks/pipeline.py:683-716` (the `discover_atrativo_task` except blocks)
**Apply to:** `sweep_uf` (and any new task)
```python
except PermanentError as exc:
    session.rollback()
    q_session, q_engine = _get_session()           # fresh session for quarantine write
    try:
        quarantine_poison(session=q_session, nascente_id=None,
                          task_name="brave.<name>", error=str(exc), payload={"uf": uf})
        q_session.commit()
    finally:
        q_session.close(); q_engine.dispose()
except Exception as exc:
    session.rollback()
    try:
        raise self.retry(exc=exc, max_retries=3)
    except self.MaxRetriesExceededError:
        # ... same quarantine_poison block ...
```
`quarantine_poison` is re-exported from `brave.core.quarantine` (`pipeline.py:241`) — import from core to respect the D-18 boundary.

### Session lifecycle
**Source:** `pipeline.py:220-230` (`_get_session()` reads `BRAVE_DB_URL`), `pipeline.py:651` + `718-720` (open/finally close)
**Apply to:** every task and the CLI inline path.

### Real-vs-fake client selection (offline-testability, D-06)
**Source:** `pipeline.py:660-671` (Places + LLM), `pipeline.py:844-855` (Places + Apify)
**Apply to:** `sweep_uf` LLM client selection. Gate on `AppConfig().run_real_externals`; real clients from `brave.clients.*`, fakes from `tests.fakes.*`. NOTE: production tasks import test fakes only in the `else` branch (e.g. `pipeline.py:670`, `:759`) — this is the established pattern (test fakes are NOT banned from production task bodies for the offline path; only `outreach_task` hard-bans them, T-03-04-07, `pipeline.py:920`).

### Celery reliability semantics (D-04 replay-safety)
**Source:** `celery_app.py:43-45` — `task_acks_late=True`, `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`. New tasks inherit this; the per-task decorators also set `acks_late=True, reject_on_worker_lost=True` (e.g. `pipeline.py:631-632`). Mirror on `sweep_uf`.

### Dispatch-Celery-then-inline-fallback (CLI + endpoint)
**Source:** `dlq.py:104-114` (swallow-all), `atrativos_gate.py:376-396` (prod-vs-offline split). Use the swallow-all variant for the CLI (operator convenience); the prod-vs-offline variant if a real endpoint is added.

---

## Test Patterns

### Task idempotency + quarantine (analog for `sweep_uf` tests)
**Source:** `tests/integration/test_celery_tasks.py:15-71` — `@pytest.mark.integration`, `db_session` fixture, "call twice → exactly one record" assertion (`:34-47`), `quarantine_poison` row assertion (`:50-71`).
**Apply to:** assert `sweep_uf` re-run is a no-op (store_raw dedup), and a poison producer lands in `PoisonQuarantine` not the §7.6 DLQ.

### FSM guard unit test (analog for chain replay-safety)
**Source:** `tests/unit/lanes/test_state_machine.py:21-73` — `SimpleNamespace` rio + `MagicMock` session, `lock=False` for the guard semantics, `with_for_update` assertion for `lock=True`.
**Apply to:** assert duplicate `find_contacts_task`/`gather_signals_task` dispatch is a no-op (guard returns on wrong state).

### Producer lane test (analog for `sweep_uf` composition)
**Source:** `tests/unit/test_mtur_lane.py:23-38` (importable + `produce(uf)` async signature via `inspect`), `tests/unit/test_desmembramento.py`, `tests/integration/test_destinos_lane.py`.
**Apply to:** assert `sweep_uf` ingests destinos via fake Mtur + fake LLM idempotently.

### Fakes to reuse (offline suite, D-06)
**Source:** `tests/fakes/` — `FakeMturClient(fixtures=[...])` returns municipalities filtered by uf, records `.calls` (`fake_mtur.py:22-59`); `FakeLLMClient(fixture_result=...)` with async `extract()` (`fake_llm.py:19-95`); `FakePlacesClient` (`fake_places.py`), `FakeApifyClient` (`fake_apify.py`). `db_session` + `fake_redis` fixtures in `tests/conftest.py:89-112`.
**Apply to:** all new orchestration tests — CI stays keyless.

### Chain end-to-end assertion (the ORCH-02 core test)
Assert the atrativos chain advances `discovered → contacts_found → signals_gathered → aguardando_consulta_whatsapp` and **STOPS** (no `outreach_task` triggered by the auto chain — D-06). Closest existing e2e analog: `tests/integration/test_end_to_end_pipeline.py`.

---

## No Analog Found

None. Every file in scope has a concrete in-repo analog — this phase is wiring + composition of existing primitives, not new architecture. The two soft gaps the planner must explicitly resolve (not missing analogs, but missing wiring decisions):

| Decision needed | Where | Why |
|-----------------|-------|-----|
| Who sets `sub_state="discovered"` | `discovery_agent.py:314-339` / chain query in modified `discover_atrativo_task` | No production code sets it today; D-03 query depends on it. Either set it at discovery or change the enqueue query key. |
| `advance_sub_state` adoption vs inline guards | `contact_finder_agent.py:72`, `signal_agent.py:172` vs `state_machine.py:28` | D-04 says "MUST gate behind `advance_sub_state`"; agents currently inline-guard. Equivalent today; planner picks letter-of-D-04 (refactor) or lower-risk (keep). |

---

## Metadata

**Analog search scope:** `brave/tasks/`, `brave/lanes/destinos/`, `brave/lanes/atrativos/`, `brave/clients/`, `brave/api/routers/`, `brave/core/rio/`, `brave/cli.py`, `tests/` (unit, integration, fakes, conftest)
**Files scanned:** ~22 source + test files; primary analog `pipeline.py` (1388 lines) read in full
**Pattern extraction date:** 2026-06-17

## PATTERN MAPPING COMPLETE
