---
phase: 02-destinos-lane
reviewed: 2026-06-12T15:00:00Z
depth: standard
files_reviewed: 23
files_reviewed_list:
  - brave/api/routers/dlq.py
  - brave/clients/mtur.py
  - brave/clients/notebooklm.py
  - brave/clients/null_mtur.py
  - brave/clients/null_notebooklm.py
  - brave/config/settings.py
  - brave/core/quarantine.py
  - brave/lanes/destinos/__init__.py
  - brave/lanes/destinos/desmembramento.py
  - brave/lanes/destinos/mtur.py
  - brave/lanes/destinos/notebooklm.py
  - brave/lanes/destinos/schemas.py
  - brave/tasks/pipeline.py
  - scripts/calibrate_destinos.py
  - tests/contract/test_pact_norteia_api.py
  - tests/fakes/fake_mtur.py
  - tests/fakes/fake_notebooklm.py
  - tests/integration/test_destinos_lane.py
  - tests/integration/test_push_destination_task.py
  - tests/unit/test_desmembramento.py
  - tests/unit/test_mtur_lane.py
  - tests/unit/test_scaffold_smoke.py
  - tests/unit/test_score_engine.py
findings:
  critical: 5
  warning: 5
  info: 4
  total: 14
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-06-12T15:00:00Z
**Depth:** standard
**Files Reviewed:** 23
**Status:** issues_found

## Summary

Phase 2 delivers the Destinos lane (Mtur seed, NotebookLM ingest, DesmembramentoAgent) with the
DLQ steward validate endpoints (single + batch). The D-18 boundary is respected throughout — lane
code imports from `brave.core.quarantine`, not `brave.tasks`. The `flag_modified` pattern is
correctly applied in all JSON mutation sites. The D-06 origin=40 firewall and the Phase 2
`threshold_dlq=40` calibration are correctly wired.

Five BLOCKER-class issues were found. Three concern security (unauthenticated steward trust
boundary, secret shadowing via alias, bare `except Exception` masking broker errors), one is a
data correctness defect (audit `before_state` score captured after JSON mutation), and one is a
functional divergence (docstring promises a batch-summary audit row that is never written). Five
warnings concern code quality and robustness. Four info items are low-risk style/test notes.

---

## Critical Issues

### CR-01: DLQ steward endpoints have no authentication

**File:** `brave/api/routers/dlq.py:23-246`
**Issue:** All five DLQ endpoints (`/api/v1/dlq` GET, `/reprocess`, `/validate`, `/validate-batch`,
`/descarte`) have no authentication dependency. The project notes say "steward validate endpoints
as a trust boundary (who can set validação humana=100 → Mar → push)". Setting
`validacao_humana_value=100` on any DLQ record then pushing to norteia-api is a privileged write —
any unauthenticated caller can exercise it. The webhook endpoint (`/webhook/error-report`) _does_
implement `X-Webhook-Secret` authentication in `brave/api/routers/webhook.py`, but that pattern
was not carried over to the DLQ router. The `get_db` dependency is the only `Depends` injected;
there is no Bearer-token or API-key guard.

**Fix:** Introduce a `require_steward` dependency in `brave/api/deps.py` (Bearer token or shared
secret checked with `hmac.compare_digest`) and apply it to all mutating DLQ endpoints:
```python
# brave/api/deps.py
from fastapi import Header, HTTPException, status
from brave.config.settings import WebhookConfig

def require_steward(
    authorization: str | None = Header(None),
    config: WebhookConfig = Depends(get_webhook_config),
) -> None:
    """Require 'Authorization: Bearer <secret>' on steward endpoints."""
    expected = f"Bearer {config.secret}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")

# brave/api/routers/dlq.py — add to every mutating route:
@router.patch("/api/v1/dlq/{rio_id}/validate", ...)
def validate_dlq_record(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: None = Depends(require_steward),  # add this
) -> dict:
```

---

### CR-02: `LLMConfig` `alias` + `env_prefix` — unintended env var shadows prefixed key

**File:** `brave/config/settings.py:59,72`
**Issue:** Both sensitive fields use an `alias` that equals the bare field name without the
`BRAVE_LLM_` prefix:
```python
openrouter_api_key: str = Field(default="", alias="openrouter_api_key")
anthropic_api_key: str = Field(default="", alias="anthropic_api_key")
```
pydantic-settings v2 resolves aliases _first_ (without applying the `env_prefix`). If the
environment contains a bare `openrouter_api_key` variable (set by any other tool or shell config),
it **shadows** `BRAVE_LLM_OPENROUTER_API_KEY` and populates the field with the wrong value. This
was confirmed by live testing: setting both env vars, the alias (bare name) wins over the
prefixed name. In a CI or Kubernetes environment where secrets are injected as `openrouter_api_key`
(e.g., by a secret-manager that uses field names, not service prefixes), the expected prefixed key
is silently ignored.

**Fix:** Remove the aliases. The field names already match what pydantic-settings will resolve from
`BRAVE_LLM_OPENROUTER_API_KEY` and `BRAVE_LLM_ANTHROPIC_API_KEY`:
```python
# brave/config/settings.py
openrouter_api_key: str = ""   # reads BRAVE_LLM_OPENROUTER_API_KEY
anthropic_api_key: str = ""    # reads BRAVE_LLM_ANTHROPIC_API_KEY
# Remove alias= and keep populate_by_name=True only if you need python-name access
```

---

### CR-03: Bare `except Exception` in DLQ endpoints swallows Celery task enqueue errors silently

**File:** `brave/api/routers/dlq.py:74-81, 138-141, 198-203`
**Issue:** All three dispatch-then-fallback blocks use an unconditional `except Exception:` to
detect "no Celery broker available":
```python
try:
    from brave.tasks.pipeline import reprocess_record_task
    reprocess_record_task.delay(str(rio_id))
except Exception:
    # synchronous fallback
    reprocess_record(db, rio_id, ScoreConfig())
```
This catches _any_ exception — including `OperationalError` from the DB inside `delay()`,
`PermanentError`, validation errors, and runtime bugs inside the Celery serialization path.
In production, a transient Redis connection error during `delay()` will silently fall back to the
synchronous reprocess path instead of retrying or surfacing the failure. The same flaw exists in
the `validate_batch` loop (line 198-203), where a failed Celery dispatch silently promotes to Mar
via the sync `promote_to_mar` call — bypassing the Celery idempotency guarantees.

**Fix:** Catch only the specific exception types that indicate "no broker":
```python
from celery.exceptions import OperationalError as CeleryOperationalError

try:
    from brave.tasks.pipeline import reprocess_record_task
    reprocess_record_task.delay(str(rio_id))
except (CeleryOperationalError, kombu.exceptions.OperationalError):
    # Celery broker unavailable — sync fallback (dev/test only)
    reprocess_record(db, rio_id, ScoreConfig())
```
Alternatively, use a flag (`AppConfig.run_real_externals`) to select the code path explicitly
rather than exception-based broker detection.

---

### CR-04: Audit `before_state.score` in `validate_batch` is captured after JSON mutation, not before

**File:** `brave/api/routers/dlq.py:184-210`
**Issue:** In `validate_batch`, the `before_state` score is taken from `rio.score` **after**
`flag_modified` and `db.flush()` have been called (lines 186-190). The flush writes the updated
`normalized` (with `validacao_humana_value=100.0`) to the DB. While `rio.score` itself is not yet
updated (scoring runs later in `reprocess_record`), the flush means the "before" state in the
audit row captures the record with `validacao_humana` already mutated. This is inconsistent with
the single-record `validate_dlq_record`, which captures `before_state` before any mutation:
```python
# Single-record validate (correct — line 114):
before_state = {"routing": rio.routing, "score": float(rio.score or 0)}
# ... mutation happens after ...
```
In `validate_batch`, the equivalent snapshot is omitted; the before_state at line 210 hardcodes
`"routing": "dlq"` and uses `rio.score` which reflects the pre-reprocess value — so the score is
technically correct, but the pattern is fragile and inconsistent.

**Fix:** Mirror the single-record pattern: capture `before_state` before the loop body mutates
the record:
```python
for rio in rows:
    before_score = float(rio.score or 0)   # capture before any mutation
    before_routing = rio.routing            # always "dlq" here, but explicit is safer
    normalized = dict(rio.normalized or {})
    ...
    write_audit(
        ...
        before_state={"routing": before_routing, "score": before_score},
        after_state={"routing": rio.routing, "score": float(rio.score or 0)},
    )
```

---

### CR-05: Slug construction for `source_ref` does not sanitize apostrophes or other special characters — potential `canonical_key` uniqueness collision

**File:** `brave/lanes/destinos/desmembramento.py:191-196`
**Issue:** The slug used as the terminal segment of `source_ref` only replaces spaces and `/`:
```python
slug = (
    destino.nome.lower()
    .replace(" ", "-")
    .replace("/", "-")
)
source_ref = f"desm:{uf}:{ibge_code}:{slug}"
```
Portuguese destination names can contain apostrophes (e.g., "Arraial d'Ajuda"), accented
characters (e.g., "Lençóis"), dots, and parentheses. The resulting `source_ref` such as
`desm:BA:2927408:arraial-d'ajuda` is stored verbatim as `canonical_key` in `RioRecord`, which
has a `unique=True` constraint (line 148 in models.py). While this doesn't cause an integrity
error for distinct municipalities, it produces non-canonical slugs that break URL safety in any
downstream consumer that treats `canonical_key` as a URL segment, and it means two LLM runs
returning "Arraial d'Ajuda" vs "Arraial D'Ajuda" generate different slugs — defeating idempotency.

**Fix:** Normalize the slug more aggressively:
```python
import re
import unicodedata

def _slugify(text: str) -> str:
    # NFD decompose, strip combining marks (accents)
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    # Replace any non-alphanumeric run with a single hyphen
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text
```

---

## Warnings

### WR-01: `validate_batch` docstring promises a batch-summary audit row that is never written

**File:** `brave/api/routers/dlq.py:168`
**Issue:** The docstring states: *"Writes individual audit rows per record and one batch summary
row after."* The implementation only writes per-record rows; the batch summary row is absent.
This is a documentation-code mismatch that will mislead operators querying the audit log for
bulk operations and any downstream monitoring built on the audit trail.

**Fix:** Either add a batch-summary row:
```python
write_audit(
    session=db,
    action="dlq_batch_validated",
    entity_type=entity_type,
    record_id=None,  # no single record
    before_state={"uf": uf, "entity_type": entity_type},
    after_state={"validated": validated},
    actor="steward",
)
```
or remove the claim from the docstring.

---

### WR-02: `reprocess_record_task` is missing `max_retries=3` in its `@shared_task` decorator

**File:** `brave/tasks/pipeline.py:323-329`
**Issue:** Unlike `process_nascente`, `push_mar`, and `push_destination_task`, the
`reprocess_record_task` decorator does not set `max_retries`:
```python
@shared_task(
    bind=True,
    name="brave.reprocess_record",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
    # max_retries is MISSING
)
```
The task body still calls `self.retry(exc=exc, max_retries=3)` (line 346), which overrides
Celery's default. The missing decorator-level `max_retries` means the Celery worker does not
enforce the cap; it relies solely on the body's `self.retry` call-site argument. This is
inconsistent with the other tasks and fragile: if the retry call is ever refactored, the cap
vanishes.

**Fix:** Add `max_retries=3` to the decorator:
```python
@shared_task(
    bind=True,
    max_retries=3,
    name="brave.reprocess_record",
    ...
)
```

---

### WR-03: `NotebookLMClient.fetch_report` catches only `OSError` — `json.JSONDecodeError` propagates uncaught

**File:** `brave/clients/notebooklm.py:64-69`
**Issue:** The file-read block catches only `OSError`:
```python
try:
    with open(report_path, encoding="utf-8") as f:
        return json.load(f)
except OSError:
    return {}
```
`json.JSONDecodeError` (a subclass of `ValueError`, not `OSError`) is not caught. A malformed
JSON report file will propagate up through `NotebookLMIngest.produce()`, which has no
exception handler around the `fetch_report` call (unlike `DesmembramentoAgent`, which quarantines
on any exception). This means a single corrupt local file will abort the entire UF sweep for
NotebookLM, silently losing all subsequent municipalities in the loop.

**Fix:**
```python
except (OSError, json.JSONDecodeError):
    # File not found, unreadable, or malformed JSON — graceful degradation
    return {}
```

---

### WR-04: Corroboration boost query in `NotebookLMIngest` uses `scalar()` — silently drops corroboration if multiple destinations share the same IBGE code

**File:** `brave/lanes/destinos/notebooklm.py:206-222`
**Issue:** The corroboration query uses `session.scalar()`, which returns only one row when
multiple `RioRecord` rows share `municipio_id == ibge_code` (e.g., if both a Mtur and a Desm
record for the same municipality are in `dlq` or `mar` routing). Only the first matching record
is boosted; additional records for the same IBGE code are silently ignored. Since the query has
no `source` filter (RioRecord has no source column — source lives in NascenteRecord), any
record type for that IBGE could be the "first" returned depending on DB ordering. The intended
behavior (boost the surviving Mtur record) is non-deterministic.

**Fix:** Add an `ORDER BY` to make the selection deterministic (or fetch all and boost each):
```python
existing = self._session.scalar(
    select(RioRecord)
    .where(
        RioRecord.municipio_id == ibge_code,
        RioRecord.uf == uf_upper,
        RioRecord.entity_type == "destination",
        RioRecord.routing.in_(["dlq", "mar"]),
    )
    .order_by(RioRecord.processed_at.asc())  # deterministic: oldest surviving record
)
```
Or boost all matching records with `scalars().all()` if multiple corroborations are valid.

---

### WR-05: Pact `test_pact_file_written_and_valid` asserts `>= 2` interactions but 3 are defined — weak assertion

**File:** `tests/contract/test_pact_norteia_api.py:219`
**Issue:** The contract verification test asserts `len(interactions) >= 2`, but three distinct
interactions are defined (destination push, idempotent re-push, attraction push). The `>= 2`
check would pass even if the third interaction (attraction push) was never written to the file.
Each test writes to the same JSON file using a separate `Pact()` instance, meaning each
`pact.write_file()` call may overwrite the previous file (depending on pact-python 3.x
accumulation behavior). If tests run in an unexpected order or the file is overwritten, the
verification step is not a reliable guard.

**Fix:** Tighten the assertion to exactly 3:
```python
assert len(interactions) == 3, (
    f"Expected 3 Pact interactions (destination, idempotent re-push, attraction), "
    f"got {len(interactions)}"
)
```
And investigate whether each `pact.write_file()` accumulates or overwrites; if it overwrites,
redesign to use a single `Pact` object for all interactions.

---

## Info

### IN-01: Imports of `ScoreConfig`, `flag_modified`, `reprocess_record` inside function bodies in `dlq.py`

**File:** `brave/api/routers/dlq.py:79-81, 118-129, 185-194`
**Issue:** Several imports are placed inside route handler bodies (possibly to avoid circular
imports at startup). While not incorrect, moving them to the module top level is more idiomatic
and makes dependency relationships visible. If the circular-import concern is real, it should be
resolved at the architecture level rather than hidden inside function bodies.

**Fix:** Move to module-level imports or, if the circular-import concern is genuine, document
why in a comment.

---

### IN-02: `deps.py` imports `fakeredis` unconditionally at module load

**File:** `brave/api/deps.py:14`
**Issue:** `import fakeredis` is a top-level module import in `deps.py`. This loads the
`fakeredis` package on every application start, including production. `fakeredis` is a dev
dependency and should not be imported in production module scope.

**Fix:** Move the import inside the `except` block where it is used:
```python
try:
    client = Redis.from_url(redis_url, socket_connect_timeout=1)
    client.ping()
    _redis_client = client
except Exception:
    import fakeredis  # dev fallback only
    _redis_client = fakeredis.FakeRedis()
```

---

### IN-03: Test isolation — `_make_dlq_record` commits within a `db_session` fixture that only rolls back unflushed work

**File:** `tests/integration/test_destinos_lane.py:85`
**Issue:** `_make_dlq_record` calls `db_session.commit()` on line 85. The `db_session` fixture
teardown calls `session.rollback()` (conftest.py:97), but a `rollback()` after a `commit()` is
a no-op — the committed rows persist in the test database across test runs. This causes test
data leakage; `test_validate_batch_returns_202` asserts `validated >= 2` rather than `== 2`
precisely because prior test runs may have left DLQ records for `uf='BA'` in the DB.

**Fix:** Replace the commit-in-helper pattern with a nested savepoint / `begin_nested()` so the
teardown rollback cleans up committed work, or truncate relevant tables in a session-scoped
`autouse` fixture.

---

### IN-04: Dead commented-out code throughout

**File:** `brave/lanes/destinos/mtur.py:165-167`, `brave/lanes/destinos/notebooklm.py:226-227`
**Issue:** Both files end with commented-out `LaneProtocol` type-assertion lines:
```python
# _lane: LaneProtocol = MturSeedIngest(...)  # noqa: F841 (type-annotation comment only)
```
These are commented out, meaning they provide no actual static-analysis value. If the intent is
a compile-time structural typing check (like the `_check_protocol_compliance` functions in the
client files), the code should be in a live `_check_protocol_compliance()` function. If it is
purely documentary, it should be prose in the docstring.

**Fix:** Either activate as a proper `_check_protocol_compliance()` function body (matching
the pattern used in client files), or remove the commented code.

---

_Reviewed: 2026-06-12T15:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
