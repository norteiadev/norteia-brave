---
phase: 01-brave-core-score-gate-boundary-contract
reviewed: 2026-06-12T00:00:00Z
depth: standard
files_reviewed: 28
files_reviewed_list:
  - brave/core/models.py
  - brave/core/score/engine.py
  - brave/core/score/schemas.py
  - brave/core/score/simulation.py
  - brave/core/nascente/service.py
  - brave/core/rio/dedup.py
  - brave/core/rio/normalize.py
  - brave/core/rio/label.py
  - brave/core/rio/routing.py
  - brave/core/mar/service.py
  - brave/clients/base.py
  - brave/clients/norteia_api.py
  - brave/config/settings.py
  - brave/observability/cost_guard.py
  - brave/observability/llm_tracker.py
  - brave/observability/audit.py
  - brave/tasks/celery_app.py
  - brave/tasks/beat_schedule.py
  - brave/tasks/pipeline.py
  - brave/api/main.py
  - brave/api/deps.py
  - brave/api/routers/health.py
  - brave/api/routers/metrics.py
  - brave/api/routers/dlq.py
  - brave/api/routers/audit.py
  - brave/api/routers/webhook.py
  - brave/cli.py
  - brave/lanes/base.py
findings:
  critical: 4
  warning: 8
  info: 6
  total: 18
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-06-12T00:00:00Z
**Depth:** standard
**Files Reviewed:** 28
**Status:** issues_found

## Summary

Reviewed the Phase 1 Brave core, score gate, boundary contract, observability, Celery
pipeline, and FastAPI surface at standard depth. The §7.6 score engine is correctly pure
(zero I/O) and the webhook authentication gate is well-constructed (constant-time compare,
auth-before-rate-limit). However the review surfaced several BLOCKER-class defects:

1. **Production code imports `tests.fakes`** in two modules (`brave/tasks/pipeline.py`,
   `brave/cli.py`) — a hard violation of the boundary contract that will `ImportError` if
   `tests/` is not packaged in a production deploy, and couples shippable code to test code.
2. **Stage-1 dedup ignores UF/source/entity scoping** (`brave/core/rio/dedup.py`), violating
   the explicitly documented "NEVER compare across UF boundaries" invariant and producing
   cross-territorial false-positive dedup for any two payloads that hash identically.
3. **A swallowed Celery retry path** in `process_nascente` causes silent data loss: after a
   transient error, the record is neither processed nor quarantined on the *first* failure,
   and the `self.retry` exception is caught and discarded.
4. **The cost guard is not wired into the actual LLM cost path and is non-atomic** — the
   enforcing budget check and the spend increment are separate operations, so concurrent
   workers can overshoot the daily ceiling.

Highest-priority concerns are the two test-import BLOCKERs and the dedup scoping bug, all of
which are concrete correctness/contract failures rather than style.

## Critical Issues

### CR-01: Production modules import from `tests/` (boundary contract violation)

**File:** `brave/tasks/pipeline.py:284`, `brave/cli.py:41`
**Issue:** Both a Celery task and the CLI import `FakeNorteiaApiClient` from `tests.fakes.fake_norteia_api` at runtime. The architecture (D-09/D-18, `brave/clients/base.py` header) states production code accepts Protocol types and *tests* inject fakes from `tests/fakes/`. Importing test code from shippable code (a) raises `ModuleNotFoundError` in any deployment that does not package the `tests/` tree (the normal case — `tests/` is excluded from wheels/Docker prod images), crashing `push_mar` and `run-fixture`; and (b) inverts the dependency direction so the fake's behavior becomes load-bearing in production. In `push_mar`, the import is at function top, so even the `run_real_externals=True` branch fails to import before reaching the real client.

**Fix:** Move fakes behind the existing Protocol boundary and inject them, or guard the import so it only happens in the non-real branch and source the fake from a production-safe location. Minimal version:

```python
# brave/tasks/pipeline.py — inside push_mar, only import fake when actually needed
app_config = AppConfig()
if app_config.run_real_externals:
    api_client = NorteiaApiClient(base_url=..., service_token=...)
else:
    # Phase 1 offline default — provide a production-shipped no-op/in-memory client,
    # NOT tests.fakes. e.g. brave.clients.local.InMemoryNorteiaApiClient
    from brave.clients.local import InMemoryNorteiaApiClient
    api_client = InMemoryNorteiaApiClient()
```
Create a production-shippable stub client under `brave/clients/` (not `tests/`). The CLI should do the same.

### CR-02: Stage-1 dedup matches across UF / source / entity_type boundaries

**File:** `brave/core/rio/dedup.py:54-67`
**Issue:** Stage 1 selects `NascenteRecord` solely by `content_hash`, then returns the first linked `RioRecord` regardless of `uf`, `municipio_id`, `entity_type`, or `source`. The module docstring and Stage 2 go to great lengths to "NEVER compare vectors across UF boundaries (homonym municipio bug)," but Stage 1 bypasses that protection entirely. Two distinct entities in different states whose normalized payloads serialize to the same JSON (e.g. minimal stub payloads, or the same name with empty optional fields) collapse into one record, silently discarding a legitimate territorial record. `content_hash` is also not unique in the schema (it is only `index=True`), and `session.scalar` returns an arbitrary one of several matches.

**Fix:** Scope Stage 1 by the same territorial key used in Stage 2, and prefer matching on `(source, content_hash)` rather than `content_hash` alone:

```python
existing_nascente = session.scalar(
    select(NascenteRecord).where(
        NascenteRecord.content_hash == content_hash,
        NascenteRecord.uf == uf,
        NascenteRecord.entity_type == entity_type,
    )
)
...
rio = session.scalar(
    select(RioRecord).where(
        RioRecord.nascente_id == existing_nascente.id,
        RioRecord.uf == uf,
        RioRecord.entity_type == entity_type,
    )
)
```

### CR-03: Transient-error path in `process_nascente` silently drops the record on first failure and swallows retry

**File:** `brave/tasks/pipeline.py:176-194`
**Issue:** In the generic `except Exception` handler, `raise self.retry(...)` is wrapped in a `try/except self.MaxRetriesExceededError`. `Celery's Task.retry()` raises `Retry` (not `MaxRetriesExceededError`) to signal the broker to re-enqueue. That `Retry` exception is *not* caught here — but because `self.retry(...)` is `raise`d inside a `try` whose only `except` is `MaxRetriesExceededError`, the `Retry` propagates out through the outer `finally` correctly on retry-able attempts. The real defect: on the attempt where retries remain, `Retry` propagates *before* the record is quarantined or processed — fine — but `default_retry_delay`/`max_retries` are set both on the decorator (`max_retries=3`) and passed again as `max_retries=3` to `self.retry`, and the `MaxRetriesExceededError` branch re-runs `quarantine_poison` only if that specific exception type is raised. If `self.retry` raises `Retry` and the task is later re-delivered and exhausts retries, Celery raises `MaxRetriesExceededError` from *inside* `self.retry` on the final attempt, which IS caught — but `exc` at that point is the final exception, and quarantine is written. The genuine bug: when `self.retry` cannot retry (e.g. broker eager mode in tests, or `acks_late` redelivery), and any *non*-`MaxRetriesExceededError`, non-`Retry` exception escapes (e.g. `quarantine_poison` itself raising), the original record is left unprocessed with no quarantine row and the message is acked — silent data loss. The nested try/except around control-flow exceptions is fragile and hides this.

**Fix:** Do not wrap `self.retry` in a `try/except` that catches `MaxRetriesExceededError`; let Celery's normal retry mechanism propagate, and handle exhaustion via the `on_failure`/`max_retries` hook or by checking `self.request.retries >= self.max_retries` explicitly *before* calling retry:

```python
except Exception as exc:
    session.rollback()
    if self.request.retries >= self.max_retries:
        q_session, q_engine = _get_session()
        try:
            quarantine_poison(q_session, ..., error=str(exc))
            q_session.commit()
        finally:
            q_session.close(); q_engine.dispose()
        return
    raise self.retry(exc=exc)
```
This makes the quarantine-on-exhaustion path explicit and removes the control-flow exception swallowing.

### CR-04: Cost guard is non-atomic (check-then-increment race) — daily ceiling can be overshot

**File:** `brave/observability/cost_guard.py:47-67` and `:70-92`
**Issue:** `pre_dispatch_check` does `GET` + compare, and `record_spend` does a separate `INCRBYFLOAT`. There is no atomicity or reservation between the read and the spend, and the two are invoked from different points in `LLMTracker.track_and_call` (check before call, record after). With multiple Celery workers (or even one worker issuing concurrent async calls), N workers can each read `current < budget` simultaneously and all proceed, overshooting the enforcing ceiling by up to N-1 calls. The module docstring claims the guard "HALTS execution" as an enforcing control; a check-then-act race undermines that guarantee for the exact concurrent-spend scenario it exists to prevent. Additionally, `record_spend` is only called `if usd_cost > 0.0` (`llm_tracker.py:88`), so in Phase 1 (cost always 0.0) the counter is never incremented and the guard can never trip — acceptable for Phase 1 stubs but means the enforcement path is entirely untested by real spend.

**Fix:** Make the check-and-reserve atomic. Reserve the spend up front with a single `INCRBYFLOAT`, then compare the returned total against the budget and refund (negative `INCRBYFLOAT`) if it would exceed, or use a Lua script that increments-iff-under-budget:

```python
def reserve_spend(redis_client, config, amount):
    key = _daily_key()
    new_total = float(redis_client.incrbyfloat(key, amount))
    _ensure_ttl(redis_client, key)
    if new_total > config.usd_daily_budget:
        redis_client.incrbyfloat(key, -amount)  # refund
        raise CostGuardError("Daily LLM budget exceeded. Halting dispatch.")
```
Call this *before* dispatch instead of separate `pre_dispatch_check`/`record_spend`.

## Warnings

### WR-01: `record_spend` can leave the daily key with no TTL → permanent budget lockout

**File:** `brave/observability/cost_guard.py:84-90`
**Issue:** The TTL is set in a separate `expire` call after `incrbyfloat`, guarded by `ttl < 0`. If the process crashes between the `incrbyfloat` and the `expire`, the key persists with no expiry; the next day's spend accumulates on top of yesterday's total and the budget trips permanently (or never resets). Relying on a follow-up `expire` is not crash-safe.

**Fix:** Use `SET key value EX ttl NX` semantics or a pipeline/Lua to make increment+expire atomic, or always re-assert the TTL each write with `expire(key, _seconds_until_midnight())` (idempotent and self-healing) instead of only when `ttl < 0`.

### WR-02: `_seconds_until_midnight` comment says "local midnight" but computes UTC epoch boundary; inconsistent with `_daily_key` using `date.today()`

**File:** `brave/observability/cost_guard.py:39-44`, `:34-36`
**Issue:** `_seconds_until_midnight` computes `(epoch // 86400 + 1) * 86400`, which is midnight *UTC*. `_daily_key()` uses `date.today()`, which is the *server local* date. If the server is not on UTC, the key rolls over at local midnight but the TTL expires at UTC midnight — the counter can expire while the day's key is still "today," zeroing the budget mid-day, or persist into the next local day. The inline comment ("uses local midnight") also contradicts the code.

**Fix:** Use a single timezone consistently. Prefer UTC throughout: `datetime.now(timezone.utc).date().isoformat()` for the key and compute the TTL to the next UTC midnight from the same clock.

### WR-03: `route_by_score` dlq_reason message is misleading and omits the lower bound

**File:** `brave/core/rio/routing.py:74-77`
**Issue:** The DLQ reason is formatted as `score={...} below threshold_mar={...}`, but a DLQ record is also *at or above* `threshold_dlq`. A steward reading the reason for a `descarte`-adjacent record gets no indication of the DLQ band; and for the `descarte` branch no reason is recorded at all (`dlq_reason = None`), so rejected records carry no machine-readable rationale. The string also hardcodes the framing around only the upper threshold.

**Fix:** Include both thresholds, e.g. `f"score={result.score:.2f} in DLQ band [{config.threshold_dlq}, {config.threshold_mar})"`, and consider recording a reason on `descarte` too for auditability.

### WR-04: `promote_to_mar` idempotency compares full `canonical`/`provenance` dicts including `rio_id`, defeating the no-op upsert

**File:** `brave/core/mar/service.py:75-81, 52-58`
**Issue:** The "re-push is a no-op" fast path requires `existing.provenance == provenance`. But `provenance` embeds `"rio_id": str(rio_record.id)`. If the same source_ref is re-promoted from a *different* RioRecord (e.g. after reprocessing created a new Rio row, or a superseding nascente), `rio_id` differs and the no-op branch is skipped even when the scored data is identical — forcing an unnecessary supersession chain. Conversely, a genuine content change that happens to keep the same `rio_id` and score is also detected, so this is a correctness-of-idempotency smell rather than data loss.

**Fix:** Base the idempotency comparison on the canonical data and score only (`canonical`, `reliability_score`, `score_version`, and the `score_breakdown` sub-dict), excluding identity fields like `rio_id`/`nascente_id` from the equality check.

### WR-05: `process_nascente_record` dedup ordering can strand a just-created nascente as a self-duplicate

**File:** `brave/core/rio/routing.py:158-169`, with `brave/core/rio/dedup.py:54-67`
**Issue:** `process_nascente_record` calls `find_duplicate(content_hash=nascente.content_hash, ...)`. Stage 1 of `find_duplicate` looks up `NascenteRecord` by that same hash — which will match the very nascente being processed. Today it returns `None` because no `RioRecord` is linked yet, but the dedup is logically searching for the input against itself. If `store_raw` ever created a Rio row eagerly, or if a superseded nascente with the same hash already has a Rio row, the new nascente would be silently mapped to the old record. Combined with CR-02 (no UF scoping), this is fragile.

**Fix:** Exclude the current `nascente.id` from Stage-1 candidate selection (`NascenteRecord.id != nascente.id`) and apply the territorial scope from CR-02.

### WR-06: `_build_push_payload` derives `source` by naive `split(":")`, mis-parsing source_refs that fall back to a bare UUID

**File:** `brave/tasks/pipeline.py:234-238`, with `brave/core/mar/service.py:41`
**Issue:** `promote_to_mar` sets `source_ref = rio_record.canonical_key or str(rio_record.id)`. When `canonical_key` is null, `source_ref` becomes a bare UUID with no colons; `source_ref.split(":", 1)[0]` then returns the entire UUID as the `source`, producing a malformed push payload (`"source": "<uuid>"`). Even with a colon-formatted ref, there is no validation that the prefix is a known source. A `split` on an empty string yields `[""]`, so `source` becomes `""`, not the intended `"unknown"` (the `if source_parts else` guard is dead because `"".split(":",1)` is `['']`, always truthy).

**Fix:** Parse defensively: `source = source_ref.split(":", 1)[0] if ":" in source_ref else "unknown"`, and validate against the allowed source set before pushing.

### WR-07: `get_redis` caches a module-global client and silently falls back to in-process `fakeredis` in production

**File:** `brave/api/deps.py:92-108`
**Issue:** On any exception pinging the configured Redis (including a transient network blip at first call), `get_redis` permanently caches a `fakeredis.FakeRedis()` in the module global `_redis_client`. In production this means the cost guard, rate limiter, and webhook counters silently operate against an ephemeral in-process fake — disabling the enforcing cost ceiling and per-IP rate limiting without any error. The cache is never invalidated, so a momentary startup race disables Redis for the lifetime of the process.

**Fix:** Do not fall back to fakeredis in production. Gate the fallback behind an explicit dev/test flag (e.g. `run_real_externals` false or a dedicated `BRAVE_ALLOW_FAKEREDIS`), and otherwise let the connection error surface so the health check reports `redis: error`.

### WR-08: `health_check` reports top-level `"status": "ok"` even when DB or Redis is down

**File:** `brave/api/routers/health.py:26-39`
**Issue:** The handler computes `db_status`/`redis_status` independently but always returns `{"status": "ok", ...}` with HTTP 200. A readiness probe keying on the top-level `status` (or the HTTP code) will treat a database outage as healthy, defeating the purpose of the readiness check (D-21).

**Fix:** Set `status = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"`, and return HTTP 503 when any dependency is down so orchestrators can gate traffic.

## Info

### IN-01: `health_check` uses `__import__("sqlalchemy").text(...)` instead of a normal import

**File:** `brave/api/routers/health.py:30`
**Issue:** `db.execute(__import__("sqlalchemy").text("SELECT 1"))` is an obfuscated dynamic import where a top-level `from sqlalchemy import text` would be clearer and lint-friendly.
**Fix:** Add `from sqlalchemy import text` and call `db.execute(text("SELECT 1"))`.

### IN-02: `AppConfig.load` ignores its `db_url` parameter

**File:** `brave/config/settings.py:108-115`
**Issue:** `load(cls, db_url=None)` accepts `db_url` but never uses it (just `return cls()`). Dead parameter that implies behavior it does not provide.
**Fix:** Remove the parameter or actually thread it into a `DBConfig` override.

### IN-03: Duplicate `import os` inside `cli._run_fixture`

**File:** `brave/cli.py:12, 26`
**Issue:** `os` is imported at module top (line 12) and again inside `_run_fixture` (line 26). The inner import is redundant.
**Fix:** Remove the function-local `import os`.

### IN-04: `simulation.py` docstring math does not match the generator's actual ranges

**File:** `brave/core/score/simulation.py:77-91` vs `:103-106`
**Issue:** The docstring describes "completude 50–100, atualidade 0–30" and "completude=80–100 and atualidade=50–80," but the code uses `completude = uniform(60, 100)` and `atualidade = uniform(0, 50)`. The narrative comments will mislead anyone reasoning about the simulated distribution.
**Fix:** Align the docstring numbers with the implemented `uniform` ranges (or vice versa).

### IN-05: `label_entity` typed as `dict` without parameter args; `normalize_name.title()` corrupts accented/Brazilian proper names

**File:** `brave/core/rio/label.py:8`, `brave/core/rio/normalize.py:21`
**Issue:** `str.title()` lowercases interior letters and mishandles names with apostrophes/prepositions common in Brazilian toponyms (e.g. "Mata de São João" → "Mata De São João", "Lençóis" handling, "D'Ajuda"). This silently alters canonical names that feed dedup and the Mar canonical payload. Separately, `label_entity` annotates `normalized: dict` without type parameters.
**Fix:** Use a locale-aware title-casing that preserves Portuguese connective words and apostrophes, or only normalize whitespace and leave casing to source. Annotate `dict[str, Any]`.

### IN-06: `worker_prefetch_multiplier=1` mitigates but does not document the cost-guard concurrency assumption; beat_schedule references undefined `brave.sweep_uf`

**File:** `brave/tasks/celery_app.py:45`, `brave/tasks/beat_schedule.py:38`
**Issue:** The beat schedule registers 27 entries pointing at task name `"brave.sweep_uf"`, which is not defined anywhere in Phase 1 (the tasks define `brave.process_nascente`, `brave.push_mar`, `brave.reprocess_record`). If beat is started in Phase 1 it will enqueue messages no worker can route, accumulating unacked/errored tasks. This is documented as a Phase 2 stub, hence Info, but the schedule is *active* (`app.conf.beat_schedule = BRAVE_BEAT_SCHEDULE`).
**Fix:** Either gate the schedule behind a Phase 2 flag or register a no-op `brave.sweep_uf` task in Phase 1 so beat has a valid target.

---

_Reviewed: 2026-06-12T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
