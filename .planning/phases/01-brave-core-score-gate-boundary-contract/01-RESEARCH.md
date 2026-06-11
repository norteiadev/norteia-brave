# Phase 1: Brave Core, Score Gate, Boundary & Contract - Research

**Researched:** 2026-06-11
**Domain:** Entity-agnostic medallion ETL pipeline (Nascente/Rio/Mar/DLQ) with reliability-score gate, client boundary, Celery orchestration, FastAPI observability, and Pact ingestion contract — Python greenfield service
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Table-per-layer for the three medallion stores (Nascente / Rio / Mar). Not a single mega-table.
- **D-02:** DLQ and descarte are routing values (`routing` / `sub_state` column) within Rio, not separate tables.
- **D-03:** Versioning by supersession — append a new row + `superseded_by` pointer; never mutate-in-place.
- **D-04:** Nascente row carries: `source`, `source_ref`, `entity_type`, `uf`, `payload (JSONB)`, `content_hash`, `ingested_at`, `version`. Immutable.
- **D-05:** Celery + Redis with celery-redbeat for single-source-of-truth scheduling; fan-out by UF. Tasks idempotent; poison messages quarantined; one beat instance.
- **D-06:** Orchestration behind an interface so a future Temporal swap stays contained.
- **D-07:** Two-stage dedup: exact `content_hash` blocking → fuzzy via pgvector. Block first by territorial key (UF + município) so homonym municípios in different UFs can never merge.
- **D-08:** pgvector index = HNSW (active-write workload). Recall at the chosen `ef_search` must be measured, not assumed.
- **D-09:** LLM access behind a `clients/` interface with a fake. Use `instructor` with `Mode.Tools`.
- **D-10:** Pin the DeepSeek slug + ordered fallback list in `pydantic-settings`. Log the resolved provider per call. `provider.data_collection: deny`.
- **D-11:** Every LLM output passes a mandatory validate-or-quarantine second layer (Pydantic). Malformed → quarantine.
- **D-12:** §7.6 score is a pure, zero-I/O function. Weights and thresholds live in config (pydantic-settings).
- **D-13:** Each score stamps the `score_version` (weight-set identity).
- **D-14:** Ship a score-distribution / histogram simulation harness as the first verification gate.
- **D-15:** Mar→norteia-api push is idempotent by `source_ref` and carries full per-criterion provenance/lineage.
- **D-16:** Freeze the push JSON shape via a consumer-driven Pact test.
- **D-17:** Persist of Google `place_id` is allowed as cache; canonical data is always the first-party validated record.
- **D-18:** Three load-bearing package boundaries: `core/` entity-agnostic; lanes share only data through Mar; every external system behind `clients/` interface.
- **D-19:** Postgres driver = psycopg 3 (not psycopg2).
- **D-20:** `llm_generations` table + USD cost guard before dispatch (enforcing, not advisory).
- **D-21:** Per-layer Brave metrics + queue/worker health + audit log exposed via FastAPI REST.

### Claude's Discretion

- Exact table/column DDL, migration tool (Alembic assumed), FastAPI router layout, Celery queue topology, and test-fixture structure are left to research/planning.

### Deferred Ideas (OUT OF SCOPE)

- Active freshness-decay / re-score cron (§7.8) — v2 (FRESH-01)
- Auto-tuning of §7.6 weights from steward decisions — v2 (TUNE-01)
- OTA price cross-check — v2 (OTA-01)
- Temporal durable-workflow engine — only if proven need emerges (Phase 3 trigger)
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CORE-01 | Pipeline stores raw, source-tagged, versioned payloads (JSONB) in Nascente, immutable | Table-per-layer DDL; NascenteRecord model with append-only semantics; Alembic migration |
| CORE-02 | Rio explodes a Nascente payload, dedups (exact hash blocking → fuzzy/embedding via pgvector), normalizes names/coords/addresses, and labels with the Norteia taxonomy | Two-stage dedup pattern; pgvector HNSW; blocking by territorial key first |
| CORE-03 | A scored record routes three ways by config thresholds: Mar (≥85), DLQ (51–84.9), descarte (≤50) | Pure §7.6 score function; pydantic-settings config weights/thresholds; routing enum on RioRecord |
| CORE-04 | Mar holds canonical records, versioned by supersession | MarRecord with `superseded_by` FK; append-new + pointer pattern |
| CORE-05 | Pipeline pushes a Mar record to norteia-api idempotently, keyed by canonical key / `source_ref` | NorteiaApiClient interface; httpx behind the boundary; Pact contract verifies shape |
| CORE-06 | Every record carries provenance/lineage (sources + per-criterion §7.6 breakdown + decisions) through to the Mar push | `score_breakdown JSONB` on RioRecord; `provenance JSONB` on MarRecord; Pact payload includes breakdown |
| CORE-07 | DLQ is a durable, actionable queue (not a log): records carry reason codes | `routing='dlq'` + `dlq_reason` column on RioRecord; queryable by FastAPI |
| CORE-08 | Pipeline can reprocess / re-score a record on demand idempotently (config change, new corroboration, human validation, or error report) without double-publishing | Reprocess task that resets routing + re-runs Rio; Mar push idempotent by `source_ref`; `score_version` comparison |
| CORE-09 | Pipeline classifies errors as transient (backoff retry) vs permanent (route to DLQ/descarte) | tenacity for retry; error classification in task wrapper; quarantine for poison |
| CORE-10 | Celery + Redis run the pipeline 24/7 with beat scheduling and fan-out by UF | celery-redbeat; one beat instance; idempotency keys per task; poison quarantine |
| CORE-11 | Every external system (Places, OTA, Apify, WhatsApp, Mtur, NotebookLM, NorteiaApi) sits behind a client interface with a fake | Protocol-based client boundary in `brave/clients/`; fake impls in `tests/fakes/` |
| CORE-12 | FastAPI exposes webhooks (WhatsApp/email), REST for the dashboard, and lane ingest, with idempotent webhook receivers | FastAPI app structure; error-report webhook → reopen endpoint |
| SCORE-01 | Score engine computes a reliability score as a pure function: origem 30% · completude 20% · corroboração 20% · atualidade 15% · validação humana 15% | Pure Python module, zero I/O; pydantic-settings weights; ScoreResult with breakdown |
| SCORE-02 | Weights and Mar/DLQ/descarte thresholds are calibrable via config; scores are versioned against the weight set used | ScoreConfig in pydantic-settings; `score_version` = hash or tag of the weight set |
| SCORE-03 | One engine serves both destino and atrativo, unit-tested on Mar/DLQ/descarte boundary cases | Entity-agnostic ScoreInput schema; boundary-case parametrized tests |
| OBS-01 | Pipeline records every LLM call in an `llm_generations` table (per-lane, per-model, with USD cost) | `llm_generations` table DDL; LLMClient wrapper writes row before returning |
| OBS-02 | A USD cost guard enforces a spend ceiling and halts/throttles when exceeded | Redis counter for daily spend; pre-dispatch check in LLMClient; raise CostGuardError |
| OBS-03 | Pipeline exposes per-layer Brave metrics (volume, rates, throughput) and queue/worker health via FastAPI | FastAPI `/metrics` router; aggregate queries against the three tables |
| OBS-04 | Pipeline writes audit logs for steward and pipeline actions | structlog JSON logs; `audit_log` table for steward mutations (approve/reject/reprocess) |
| CNTR-01 | The Mar→norteia-api ingestion contract is frozen and verified by a Pact contract test | pact-python 3.x consumer test; Pact file published; Mar push shape documented |
| CNTR-02 | A community error-report webhook reopens a published record back into Rio/DLQ (self-healing loop) | POST `/webhook/error-report` → locate MarRecord → create/reset RioRecord with `routing='dlq'` |
| TEST-01 | Full suite runs 100% offline via docker-compose (Postgres+Redis); real externals are opt-in by flag; CI runs keyless | docker-compose.yml with Postgres+pgvector + Redis; pytest-socket to block real network in CI |
| TEST-03 | HTTP boundaries faked with respx/VCR, LLM faked, webhooks fixture-driven; norteia-api contract covered by Pact | FakeLLMClient; respx mocks for httpx clients; pact-python consumer test in `tests/contract/` |
</phase_requirements>

---

## Summary

Phase 1 delivers the foundational greenfield scaffold for `norteia-brave`: a Python package with three medallion-layer tables (Nascente/Rio/Mar), a pure §7.6 score engine, three-way routing, client boundary fakes for all eight external systems, Celery+Redis 24/7 orchestration, FastAPI observability surface, and a Pact consumer contract for the Mar→norteia-api push — all validated by a 100%-offline keyless test suite.

The architecture is well-specified in the existing planning documents: table-per-layer medallion ETL, routing/sub_state columns inside the mutable Rio layer (no extra tables for DLQ/descarte), versioning by supersession (append + pointer, never mutate), a pure zero-I/O score function with config-driven weights, and every external system behind a typed Python Protocol interface with a fake implementation. The stack is validated from PyPI (all versions pinned from the registry in June 2026) and free of structural decisions to re-litigate.

The deliverable is the client *seam* and the backend skeleton — no real API calls in Phase 1. The planner's primary work is decomposing this into sequential build waves: project scaffold and tooling first, then models and migrations, then the pure score engine (most foundational, zero deps), then the Rio processing pipeline with fakes, then Celery orchestration, then FastAPI surface, then observability, and finally the Pact contract test. Every external dependency is behind a faked client from wave 1 so tests can run offline at every step.

**Primary recommendation:** Build in strict dependency order — scaffold → models/migrations → score engine → client boundary + fakes → Rio pipeline → Celery tasks → FastAPI surface → observability → Pact contract. The score engine and client boundary are the load-bearing foundation; everything else builds on them.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Raw payload storage (Nascente) | Database / Storage | API / Backend | Append-only JSONB store; FastAPI route triggers write, DB owns persistence |
| Rio processing (dedup/normalize/label) | API / Backend (Celery workers) | Database | CPU-bound deterministic code; pgvector dedup queries hit DB |
| §7.6 score computation | API / Backend (pure function) | — | Zero I/O; called synchronously inside a Celery task |
| Three-way routing (Mar/DLQ/descarte) | API / Backend (Celery workers) | Database | Routing enum written to DB; threshold read from config |
| Mar push to norteia-api | API / Backend (Celery workers) | — | Outbound HTTP behind NorteiaApiClient interface |
| DLQ (actionable queue) | Database / Storage | API / Backend | `routing='dlq'` rows in RioRecord; FastAPI exposes read/mutate endpoints |
| Celery orchestration (fan-out by UF) | API / Backend (Celery workers + beat) | Database | celery-redbeat stores schedule in Redis; state in Postgres |
| Client boundary (faked externals) | API / Backend | — | Python Protocol interfaces + fakes; all external I/O flows through here |
| FastAPI REST + webhooks | API / Backend | — | Thin controllers delegating to core services |
| `llm_generations` + audit log | Database / Storage | API / Backend | Tables written by service layer; FastAPI exposes read-only |
| USD cost guard | API / Backend | Database / Storage | Redis counter for daily spend; pre-dispatch check in LLMClient |
| Pact consumer contract | API / Backend (test layer) | — | Consumer test lives in `tests/contract/`; Pact file is a JSON artifact |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.12 | Runtime | Async-mature, best LLM/data ecosystem; 3.12 is the safe production floor |
| FastAPI | 0.136.3 | API + webhooks + REST for dashboard + lane ingest | Async-native, Pydantic-native, OpenAPI auto-docs |
| Uvicorn | 0.49.0 | ASGI server | Standard FastAPI runtime |
| Pydantic | 2.13.x | Schemas, settings, LLM output validation | v2 Rust core; validation backbone for score config + LLM 2nd layer |
| PostgreSQL | 16 or 17 | JSONB Nascente store + relational Brave state + pgvector | Single DB avoids a second datastore |
| pgvector (extension) | 0.8.x server-side | Embedding-based fuzzy dedup in Rio | In-transaction dedup; HNSW for active-write workload |
| pgvector (Python) | 0.4.2 | Python adapter for pgvector types | SQLAlchemy integration |
| Celery | 5.6.3 | 24/7 orchestration, beat, fan-out by UF | Mature; tolerates day-scale latency |
| Redis | 7.x or 8.x server | Celery broker + result backend + cost-guard counters | Standard Celery broker; doubles as rate-limit store |
| redis (Python client) | 8.0.x | Python Redis client | Current major; compatible with Celery 5.6 |
| instructor | 1.15.1 | Structured LLM output (2nd-layer validation) | Mode.Tools default for DeepSeek; Pydantic retry built-in |
| openai | 2.41.1 | Client for OpenRouter (DeepSeek backend) | OpenRouter is OpenAI-compatible |
| anthropic | 0.109.1 | Sonnet 4.5 client (Phase 3 WhatsApp — stub this phase) | Native SDK for first-class streaming/tool-use |
| psycopg | 3.3.4 | Postgres driver | Async + sync (Celery+FastAPI); psycopg 3 not 2 |
| SQLAlchemy | 2.0.50 | ORM / Core for Brave state tables | 2.0 typed API; pgvector integrates via `pgvector.sqlalchemy` |
| Alembic | 1.18.4 | DB migrations | Standard SQLAlchemy migration tool |
| pydantic-settings | 2.14.1 | 12-factor config (slugs, weights, keys, flags) | Centralizes pinned DeepSeek slug + calibrable §7.6 weights |
| celery-redbeat | 2.3.3 | Redis-backed Celery beat scheduler | Single-source-of-truth schedule; no single-point file |
| httpx | 0.28.1 | Async HTTP for custom clients | Client layer for norteia-api push and future external clients |
| tenacity | 9.1.4 | Retry/backoff for external clients | Wrap HTTP clients (429/5xx); pairs with cost guard |
| structlog | 26.1.0 | Structured logging → audit logs | JSON logs correlate with `llm_generations` |

### Supporting (dev / ops)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | 9.0.3 | Test runner (100% offline suite) | Always; core gate |
| respx | 0.23.1 | Mock httpx calls | Preferred over VCR for deterministic, hand-authored client mocks |
| vcrpy | 8.1.1 | Record/replay cassettes | Shape-fidelity cases; scrub PII/keys from cassettes |
| fakeredis | 2.36.1 | In-process Redis for unit tests | Score/queue logic without Redis container |
| pact-python | 3.4.0 | Consumer-driven Pact contract | Verifies Mar push shape; use 3.x API (not 2.x) |
| pytest-socket | 0.8.0 | Block real network calls in CI | Hard failure if test attempts outbound connection; keyless CI guard |
| ruff | latest | Lint + format | Replaces flake8+isort+black; single fast tool |
| pyright | latest | Static typing | Enforce on score engine + client interfaces |
| celery-types | 0.26.0 | Celery type stubs | Pairs with pyright/mypy on task definitions |
| flower | 2.0.1 | Celery monitoring UI | Dev/ops visibility into queues/workers |
| uv | 0.11.7 (installed) | Package manager + venv | Faster pip alternative; available on this machine |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| celery-redbeat | File-based Celery beat | File-based is a single-point-of-failure; redbeat stores schedule in Redis |
| psycopg 3 | asyncpg | asyncpg is faster but psycopg 3 covers sync (Celery) + async (FastAPI) with cleaner pgvector/SQLAlchemy story |
| pytest-socket | Manual socket patching | pytest-socket is a pytest plugin with declarative fixture; no patching boilerplate |
| pact-python 3.x | pact-python 2.x | 3.x is the current release (3.4.0); 2.x is maintenance-only. 3.x has a modernized API — plan tasks against 3.x |
| respx | VCR.py | respx is better for deterministic test-authored mocks; VCR is better for real-shape cassettes |
| HNSW pgvector index | IVFFlat | IVFFlat recall drifts under active writes; needs rebuilds; HNSW better for continuous ingest |

**Installation:**
```bash
# Using uv (available on this machine)
uv venv .venv && source .venv/bin/activate

uv pip install "fastapi[standard]==0.136.*" "uvicorn[standard]==0.49.*" \
  "pydantic==2.13.*" "pydantic-settings==2.14.*" \
  "celery==5.6.*" "redis==8.0.*" "celery-redbeat==2.3.*" \
  "sqlalchemy==2.0.*" "psycopg[binary]==3.3.*" "pgvector==0.4.*" "alembic==1.18.*" \
  "openai==2.41.*" "anthropic==0.109.*" "instructor==1.15.*" \
  "httpx==0.28.*" "tenacity==9.1.*" "structlog==26.*"

# Dev dependencies
uv pip install "pytest==9.0.*" "respx==0.23.*" "vcrpy==8.1.*" "fakeredis==2.36.*" \
  "pact-python==3.4.*" "pytest-socket==0.8.*" \
  "ruff" "pyright" "celery-types==0.26.*" "flower==2.0.*"
```

---

## Package Legitimacy Audit

> slopcheck was not available on this machine (pip install failed). All packages are tagged [ASSUMED] from the registry-existence check only. All packages confirmed present on PyPI via `pip index versions`.

| Package | Registry | PyPI Latest | Source Repo | slopcheck | Disposition |
|---------|----------|-------------|-------------|-----------|-------------|
| fastapi | PyPI | 0.136.3 | github.com/fastapi/fastapi | unavailable | [ASSUMED] — well-known, ~300M/month downloads |
| celery | PyPI | 5.6.3 | github.com/celery/celery | unavailable | [ASSUMED] — well-known |
| celery-redbeat | PyPI | 2.3.3 | github.com/sibson/redbeat | unavailable | [ASSUMED] — established project |
| pydantic | PyPI | 2.13.x | github.com/pydantic/pydantic | unavailable | [ASSUMED] — well-known |
| pydantic-settings | PyPI | 2.14.1 | github.com/pydantic/pydantic-settings | unavailable | [ASSUMED] — official Pydantic sub-project |
| sqlalchemy | PyPI | 2.0.50 | github.com/sqlalchemy/sqlalchemy | unavailable | [ASSUMED] — well-known |
| alembic | PyPI | 1.18.4 | github.com/sqlalchemy/alembic | unavailable | [ASSUMED] — official SQLAlchemy sub-project |
| psycopg | PyPI | 3.3.4 | github.com/psycopg/psycopg | unavailable | [ASSUMED] — well-known |
| pgvector | PyPI | 0.4.2 | github.com/pgvector/pgvector-python | unavailable | [ASSUMED] — official pgvector Python adapter |
| instructor | PyPI | 1.15.1 | github.com/jxnl/instructor | unavailable | [ASSUMED] — well-known LLM library |
| openai | PyPI | 2.41.1 | github.com/openai/openai-python | unavailable | [ASSUMED] — official OpenAI SDK |
| anthropic | PyPI | 0.109.1 | github.com/anthropic-sdk/anthropic-sdk-python | unavailable | [ASSUMED] — official Anthropic SDK |
| httpx | PyPI | 0.28.1 | github.com/encode/httpx | unavailable | [ASSUMED] — well-known |
| tenacity | PyPI | 9.1.4 | github.com/jd/tenacity | unavailable | [ASSUMED] — well-known |
| structlog | PyPI | 26.1.0 | github.com/hynek/structlog | unavailable | [ASSUMED] — well-known |
| redis (Python) | PyPI | 8.0.x | github.com/redis/redis-py | unavailable | [ASSUMED] — official Redis client |
| uvicorn | PyPI | 0.49.0 | github.com/encode/uvicorn | unavailable | [ASSUMED] — well-known |
| pytest | PyPI | 9.0.3 | github.com/pytest-dev/pytest | unavailable | [ASSUMED] — well-known |
| respx | PyPI | 0.23.1 | github.com/lundberg/respx | unavailable | [ASSUMED] — established httpx mock |
| vcrpy | PyPI | 8.1.1 | github.com/kevin1024/vcrpy | unavailable | [ASSUMED] — well-known |
| fakeredis | PyPI | 2.36.1 | github.com/cunla/fakeredis-py | unavailable | [ASSUMED] — established project |
| pact-python | PyPI | 3.4.0 | github.com/pact-foundation/pact-python | unavailable | [ASSUMED] — official Pact Foundation library |
| pytest-socket | PyPI | 0.8.0 | github.com/miketheman/pytest-socket | unavailable | [ASSUMED] — established project |
| celery-types | PyPI | 0.26.0 | github.com/sbdchd/celery-types | unavailable | [ASSUMED] — established stubs |
| flower | PyPI | 2.0.1 | github.com/mher/flower | unavailable | [ASSUMED] — well-known Celery UI |

**Packages removed due to slopcheck [SLOP] verdict:** none (slopcheck unavailable)
**Packages flagged as suspicious [SUS]:** none identified

*slopcheck was not available at research time — all packages above are tagged `[ASSUMED]`. All are well-known projects with long histories on PyPI, confirmed via `pip index versions`. The planner should add a `checkpoint:human-verify` before the bulk install task if strict slopcheck compliance is required.*

---

## Architecture Patterns

### System Architecture Diagram

```
Ingest trigger (Celery beat / API call)
        │
        ▼
[NASCENTE]  ── append-only JSONB store
  NascenteRecord: source, source_ref, entity_type, uf, payload, content_hash, version, ingested_at
        │
        │  Celery task: process_nascente(nascente_id)
        ▼
[RIO]  ── mutable working area
  Step 1: exact content_hash dedup  ──── duplicate found? supersede old
  Step 2: territorial-key block (UF + município)
  Step 3: pgvector HNSW fuzzy dedup within block  ──── merge candidate? measure recall
  Step 4: normalize (names / coords / addresses)
  Step 5: label (Norteia taxonomy)
  Step 6: score §7.6 (PURE FUNCTION, zero I/O)
     │  ScoreInput → ScoreResult(score, breakdown, score_version)
     │
     ├── score ≥ 85  ──────────────────────────────────────▶ [MAR]
     │                                                          │  Celery task: push_to_norteia_api(mar_id)
     │                                                          │  NorteiaApiClient.push() (httpx behind interface)
     │                                                          │  idempotent by source_ref
     │                                                          ▼
     │                                               POST /api/internal/territorial/{destinations|attractions}
     │                                               (service token, Pact contract verifies shape)
     │
     ├── 51 ≤ score ≤ 84.9  ──▶  routing='dlq'  ──▶  FastAPI REST exposes for steward review
     │                            dlq_reason code       dashboard: approve→Mar | reject→descarte | reprocess→Rio
     │
     └── score ≤ 50  ──────────▶  routing='descarte'  ──▶  retained for audit, not reprocessed by default

                              ┌─────────────────────────────────┐
                              │  REVERSE FLOW (error-report)    │
                              │  POST /webhook/error-report      │
                              │  → locate MarRecord by source_ref│
                              │  → reset RioRecord routing='dlq' │
                              └─────────────────────────────────┘

[CLIENT BOUNDARY]  brave/clients/
  Protocol interfaces:  PlacesClient | OTAClient | ApifyClient | WhatsAppClient
                        MturClient | NotebookLMClient | LLMClient | NorteiaApiClient
  Fake impls:           tests/fakes/fake_*.py
  Real impls:           (most deferred to Phase 2/3; NorteiaApiClient is Phase 1)
  Network guard:        pytest-socket blocks outbound in CI; real = opt-in flag

[CELERY + REDIS]
  Broker + result backend: Redis 7/8
  Beat scheduler: celery-redbeat (schedule in Redis, not file)
  Beat task: sweep_uf(uf) → fan-out → process_nascente tasks
  Idempotency: every task is a no-op on re-run (canonical key / source_ref uniqueness)
  Poison quarantine: after N failures → quarantine table (NOT the review DLQ)
  Row locks: SELECT FOR UPDATE on RioRecord transitions

[FASTAPI SURFACE]
  Routers:
    /webhook/error-report  → reopen Mar → DLQ (CNTR-02)
    /api/v1/metrics        → per-layer volume/rates/queue health (OBS-03)
    /api/v1/audit          → audit log read (OBS-04)
    /api/v1/dlq            → list/mutate DLQ records (CORE-07, CORE-08)
    /api/v1/health         → readiness check

[OBSERVABILITY]
  llm_generations table: lane, model, resolved_provider, tokens, usd_cost, created_at
  USD cost guard: Redis INCRBYFLOAT daily counter; pre-dispatch check; CostGuardError → halt
  audit_log table: action, entity_type, record_id, before_state, after_state, actor, created_at
  Brave metrics: FastAPI aggregates from nascente/rio/mar counts + Celery worker stats
```

### Recommended Project Structure

```
norteia-brave/
├── pyproject.toml              # uv-managed; all deps pinned
├── .env.example                # template; .env in .gitignore
├── docker-compose.yml          # postgres (pgvector) + redis for local/CI
├── alembic/                    # migrations
│   ├── env.py
│   └── versions/               # 0001_init_nascente_rio_mar.py etc.
├── brave/
│   ├── __init__.py
│   ├── config/
│   │   ├── settings.py         # pydantic-settings: ScoreConfig, LLMConfig, DBConfig, flags
│   │   └── score_weights.py    # ScoreWeightSet, score_version computation
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py           # SQLAlchemy: NascenteRecord, RioRecord, MarRecord, llm_generations, audit_log
│   │   ├── nascente/
│   │   │   ├── __init__.py
│   │   │   └── service.py      # store_raw(payload, source, entity_type, uf) → NascenteRecord
│   │   ├── rio/
│   │   │   ├── __init__.py
│   │   │   ├── dedup.py        # exact-hash + pgvector HNSW; territorial-key blocking
│   │   │   ├── normalize.py    # names / coords / addresses
│   │   │   ├── label.py        # Norteia taxonomy labeling
│   │   │   └── routing.py      # score → routing enum; reprocess logic
│   │   ├── mar/
│   │   │   ├── __init__.py
│   │   │   └── service.py      # promote_to_mar, supersede, reopen_from_error_report
│   │   └── score/
│   │       ├── __init__.py
│   │       ├── engine.py       # PURE: compute_score(ScoreInput, ScoreConfig) → ScoreResult
│   │       ├── schemas.py      # ScoreInput, ScoreResult, ScoreBreakdown
│   │       └── simulation.py   # histogram harness: simulate_distribution(samples) → stats
│   ├── clients/
│   │   ├── __init__.py
│   │   ├── base.py             # Protocol definitions for all 8 external systems
│   │   ├── llm.py              # LLMClient real impl (instructor+openai→OpenRouter+anthropic)
│   │   └── norteia_api.py      # NorteiaApiClient real impl (httpx push + Pact consumer)
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── llm_tracker.py      # write llm_generations row + cost-guard check
│   │   ├── cost_guard.py       # Redis counter; pre_dispatch_check(); CostGuardError
│   │   └── audit.py            # write audit_log; structlog JSON
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── celery_app.py       # Celery() + redbeat config; queue definitions
│   │   ├── beat_schedule.py    # RedBeatScheduler entries (sweep_uf per UF)
│   │   └── pipeline.py         # process_nascente, push_mar, reprocess_record tasks
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI() app; includes all routers
│   │   ├── deps.py             # dependency injection (DB session, settings)
│   │   └── routers/
│   │       ├── webhook.py      # POST /webhook/error-report
│   │       ├── metrics.py      # GET /api/v1/metrics
│   │       ├── audit.py        # GET /api/v1/audit
│   │       ├── dlq.py          # GET/PATCH /api/v1/dlq
│   │       └── health.py       # GET /api/v1/health
│   └── lanes/
│       ├── __init__.py
│       └── base.py             # Lane / Producer protocol (stub; lanes filled in Phase 2/3)
├── tests/
│   ├── conftest.py             # pytest fixtures: db session, fake clients, celery_app
│   ├── unit/
│   │   ├── test_score_engine.py        # parametrized Mar/DLQ/descarte boundary cases
│   │   ├── test_score_simulation.py    # histogram harness output validation
│   │   ├── test_dedup.py               # territorial-key blocking, hash dedup
│   │   └── test_routing.py             # routing logic for each threshold band
│   ├── integration/
│   │   ├── test_nascente_service.py    # store_raw → DB (docker-compose Postgres)
│   │   ├── test_rio_pipeline.py        # full Nascente→Rio→score→route (with fakes)
│   │   ├── test_mar_push.py            # promote→Mar→push (NorteiaApiClient fake)
│   │   ├── test_celery_tasks.py        # idempotency; poison quarantine (fakeredis)
│   │   ├── test_cost_guard.py          # enforcing circuit breaker (fakeredis)
│   │   └── test_error_report_webhook.py # reopen Mar → DLQ (FastAPI test client)
│   ├── fakes/
│   │   ├── __init__.py
│   │   ├── fake_llm.py         # FakeLLMClient — returns fixture ScoreResult/extraction
│   │   ├── fake_norteia_api.py # FakeNorteiaApiClient — records push calls, returns 200
│   │   └── fake_places.py      # FakePlacesClient (stub; used from Phase 3)
│   └── contract/
│       └── test_pact_norteia_api.py  # pact-python 3.x consumer test; generates Pact file
└── dashboard/                  # Next.js + Bun (Phase 4; empty stub this phase)
```

### Pattern 1: Pure Score Engine with Config-Driven Weights

**What:** §7.6 score computation is a pure function — it takes a `ScoreInput` (normalized record with per-criterion values) and a `ScoreConfig` (loaded from pydantic-settings), and returns a `ScoreResult` with the total score, per-criterion breakdown, and `score_version`. Zero I/O, no database calls, no LLM calls.

**When to use:** Always for scoring. Never put DB reads or LLM calls inside the score function.

**Example:**
```python
# brave/core/score/engine.py
from brave.config.settings import ScoreConfig
from brave.core.score.schemas import ScoreInput, ScoreResult, ScoreBreakdown

def compute_score(inp: ScoreInput, config: ScoreConfig) -> ScoreResult:
    """Pure function. No I/O. Fully unit-testable."""
    origem_pts    = inp.origem_value * config.weight_origem / 100
    completude_pts = inp.completude_value * config.weight_completude / 100
    corroboration_pts = inp.corroboracao_value * config.weight_corroboracao / 100
    atualidade_pts = inp.atualidade_value * config.weight_atualidade / 100
    validation_pts = inp.validacao_humana_value * config.weight_validacao_humana / 100

    total = origem_pts + completude_pts + corroboration_pts + atualidade_pts + validation_pts

    if total >= config.threshold_mar:
        routing = "mar"
    elif total >= config.threshold_dlq:
        routing = "dlq"
    else:
        routing = "descarte"

    return ScoreResult(
        score=round(total, 2),
        routing=routing,
        score_version=config.score_version,
        breakdown=ScoreBreakdown(
            origem=origem_pts,
            completude=completude_pts,
            corroboracao=corroboration_pts,
            atualidade=atualidade_pts,
            validacao_humana=validation_pts,
        ),
    )
```

### Pattern 2: Client Boundary — Protocol + Fake + Real Swap

**What:** Every external system is a typed `Protocol` (structural typing). Production code receives the protocol type. Tests inject fakes. No `isinstance` checks, no mocking frameworks patching internals.

**When to use:** All 8 external systems: LLMClient, NorteiaApiClient, PlacesClient, OTAClient, ApifyClient, WhatsAppClient, MturClient, NotebookLMClient.

**Example:**
```python
# brave/clients/base.py
from typing import Protocol
from brave.core.score.schemas import LLMExtractionResult

class LLMClientProtocol(Protocol):
    async def extract(self, prompt: str, schema: type) -> LLMExtractionResult: ...

class NorteiaApiClientProtocol(Protocol):
    async def push_destination(self, payload: dict) -> None: ...
    async def push_attraction(self, payload: dict) -> None: ...

# tests/fakes/fake_llm.py
class FakeLLMClient:
    def __init__(self, fixture_result: LLMExtractionResult):
        self._result = fixture_result
        self.calls: list[dict] = []

    async def extract(self, prompt: str, schema: type) -> LLMExtractionResult:
        self.calls.append({"prompt": prompt, "schema": schema.__name__})
        return self._result
```

### Pattern 3: Celery Task Idempotency and Poison Quarantine

**What:** Every Celery task that writes to the DB must be idempotent — a retry must be a no-op. Poison messages (tasks that fail permanently) go to a `poison_quarantine` table after N retries, NOT to the review DLQ (they are distinct: review DLQ = §7.6 routing; poison quarantine = Celery operational).

**When to use:** All pipeline tasks (process_nascente, push_mar, reprocess_record, sweep_uf).

**Example:**
```python
# brave/tasks/pipeline.py
from celery import shared_task
from brave.core.nascente.service import get_or_store_raw
from brave.core.rio.routing import process_nascente_record

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_nascente(self, nascente_id: str) -> None:
    try:
        record = get_nascente(nascente_id)
        if record.rio_id is not None:
            return  # Idempotency: already processed
        process_nascente_record(record)
    except TransientError as exc:
        raise self.retry(exc=exc)
    except PermanentError:
        quarantine_poison(nascente_id, reason=str(exc))
        return  # Do not re-raise; stop retrying
```

### Pattern 4: Two-Stage Dedup with Territorial Key Blocking

**What:** Exact hash blocking first (cheap, zero false positives), then territorial key blocking (UF + município), then pgvector HNSW fuzzy within the block. Never compare vectors across UF boundaries.

**When to use:** Always in Rio dedup step.

**Example:**
```python
# brave/core/rio/dedup.py
from pgvector.sqlalchemy import Vector
from sqlalchemy import select

async def find_duplicate(session, record: RioRecord) -> RioRecord | None:
    # Stage 1: Exact hash
    existing = await session.scalar(
        select(NascenteRecord).where(
            NascenteRecord.content_hash == record.content_hash,
            NascenteRecord.id != record.id
        )
    )
    if existing:
        return existing

    # Stage 2: Territorial-key block + pgvector HNSW fuzzy
    # NEVER compare across UF — homonym municipalities must stay separate
    candidates = await session.scalars(
        select(RioRecord)
        .where(
            RioRecord.uf == record.uf,
            RioRecord.municipio_id == record.municipio_id,
            RioRecord.entity_type == record.entity_type,
            RioRecord.id != record.id,
        )
        .order_by(RioRecord.embedding.cosine_distance(record.embedding))
        .limit(10)  # Small candidate set; high ef_search for recall
    )
    for candidate in candidates:
        if cosine_similarity(candidate.embedding, record.embedding) > DEDUP_THRESHOLD:
            return candidate
    return None
```

### Pattern 5: Score Distribution Simulation Harness (DLQ-landfill prevention)

**What:** A simulation harness generates synthetic `ScoreInput` samples representative of each source type (origem=100/80/40; various completude/corroboração/atualidade values; validação humana=0 for cold start) and plots the score histogram. The histogram must be run before wiring intake to verify the 50/85 band boundaries produce a workable DLQ volume.

**When to use:** During Wave 0 / Phase 1 setup before any real records are ingested.

**Example:**
```python
# brave/core/score/simulation.py
import statistics
from brave.core.score.engine import compute_score
from brave.core.score.schemas import ScoreInput
from brave.config.settings import ScoreConfig

def simulate_distribution(config: ScoreConfig, samples: list[ScoreInput]) -> dict:
    scores = [compute_score(s, config).score for s in samples]
    mar_count = sum(1 for s in scores if s >= config.threshold_mar)
    dlq_count = sum(1 for s in scores if config.threshold_dlq <= s < config.threshold_mar)
    desc_count = sum(1 for s in scores if s < config.threshold_dlq)
    return {
        "total": len(scores),
        "mar_pct": mar_count / len(scores) * 100,
        "dlq_pct": dlq_count / len(scores) * 100,
        "descarte_pct": desc_count / len(scores) * 100,
        "mean": statistics.mean(scores),
        "stdev": statistics.stdev(scores),
        "histogram": compute_histogram(scores, bins=10),
    }
```

### Pattern 6: pact-python 3.x Consumer Contract Test

**What:** The Pact consumer test lives in `tests/contract/` and runs against a mock provider (pact-python 3.x spins up a mock server). It defines the expected Mar push shape and generates a Pact file (JSON contract artifact). This does NOT require the Laravel norteia-api to be running.

**Important:** pact-python 3.4.0 is the current release. The 2.x API (`Consumer`, `Provider`, `Pact()` constructor) is deprecated. Use the 3.x API (`pact.consumer`, `pact.v3`) — plan tasks accordingly.

```python
# tests/contract/test_pact_norteia_api.py
import pytest
from pact.v3 import Pact  # pact-python 3.x API

PACT_DIR = "tests/contract/pacts"

@pytest.fixture(scope="module")
def pact():
    with Pact("norteia-brave", "norteia-api") as p:
        yield p

def test_push_destination_contract(pact):
    (
        pact.upon_receiving("a valid destination Mar push")
            .with_request("POST", "/api/internal/territorial/destinations")
            .with_headers({"Authorization": "Bearer <service_token>"})
            .with_body({
                "source": "mtur",
                "source_ref": "mtur:BA:123",
                "entity_type": "destination",
                "canonical": {"name": "Trancoso", "uf": "BA", "municipio": "Porto Seguro"},
                "reliability_score": 87.5,
                "score_version": "v1.0",
                "provenance": {"origem": 30.0, "completude": 20.0, ...}
            })
            .will_respond_with(200, body={"id": "uuid", "source_ref": "mtur:BA:123"})
    )
    with pact.serve() as mock_provider:
        client = NorteiaApiClient(base_url=mock_provider.url)
        client.push_destination(...)
```

### Anti-Patterns to Avoid

- **LLM inside the score engine:** Non-reproducible gate; defeats calibrable weights; untestable. Keep the score function pure.
- **One mega-table for all three layers:** Couples immutable raw store to mutable lifecycle; every query carries a state filter; can't independently index (pgvector only belongs on Rio).
- **Separate DLQ/descarte tables:** Avoids the pitfall of state churn = row moves across tables; routing values on RioRecord are correct.
- **psycopg2 anywhere:** Maintenance mode; no async; clumsier pgvector path.
- **File-based Celery beat:** Single-point-of-failure schedule; use celery-redbeat.
- **IVFFlat pgvector index:** Recall drifts under active writes; HNSW is the correct default.
- **Dedup on name-embedding alone:** Homonym municipalities across UFs will merge; always block by territorial key first.
- **Two separate "DLQ" concepts without clear naming:** The §7.6 review DLQ (`routing='dlq'` on RioRecord) and the Celery poison quarantine (`poison_quarantine` table) must be named and documented distinctly to avoid confusion.
- **pact-python 2.x API:** 3.x is current; plan tasks against the 3.x `pact.v3` API.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Structured LLM output with retries | Custom JSON parser + retry loop | `instructor` (Mode.Tools) + Pydantic | instructor feeds validation errors back to the model; handles tool-call parsing; your own loop is 200 lines with edge cases |
| Exponential backoff for HTTP clients | `time.sleep` in a try/except loop | `tenacity` | tenacity handles jitter, max attempts, exception type filtering; your own is a subtle bugs factory |
| Config from env vars | `os.environ.get(...)` everywhere | `pydantic-settings` | type safety, validation, nested models, `.env` file loading, test overrides via env |
| DB migrations | Raw SQL files applied manually | `alembic` | revision history, autogenerate from SQLAlchemy models, upgrade/downgrade, team-safe |
| JSON structured logging | `logging.basicConfig(...)` | `structlog` | context binding, stdlib integration, JSON processor pipeline, correlation IDs |
| Redis-backed Celery schedule | Celery file-based beat | `celery-redbeat` | schedule survives restarts; multiple worker processes safe; stored in Redis alongside the broker |
| Mock HTTP calls in tests | `unittest.mock.patch` on httpx internals | `respx` | declarative mock patterns; respx-level not function-level; works with async httpx naturally |
| Block real network in CI | Custom socket patch | `pytest-socket` | pytest plugin; `@pytest.mark.disable_socket` or `--disable-socket` flag; hard failure not silent |
| Consumer contract testing | Ad-hoc JSON comparison | `pact-python` | generates machine-readable Pact file; provider can verify against it independently; cross-repo safety |

**Key insight:** This phase's value is not in the infrastructure tooling — all infrastructure problems are solved by mature libraries. The custom code is the §7.6 score engine, the medallion data model, the Rio processing pipeline, the client boundary interfaces, and the Pact contract shape. Spend engineering effort there, not on re-implementing retry logic.

---

## Common Pitfalls

### Pitfall 1: DLQ Landfill at Cold Start

**What goes wrong:** Cold-start records (validação humana=0, corroboração thin, origem=40 for LLM-generated) mathematically collapse into the 51–84.9% DLQ band. The DLQ receives essentially all intake; the human review queue grows faster than it can be drained; the gate silently degrades to "approve everything, slower."

**Why it happens:** The §7.6 weights were designed for steady-state records that have human validation and corroboration. At cold start, those two criteria (35% combined) are near-zero, compressing score distribution into the DLQ band.

**How to avoid:** Ship the score-distribution simulation harness (SCORE-02, see Pattern 5) as the very first verification gate in Phase 1. Run it before wiring any real intake. The 50/85 thresholds are tunable knobs in `pydantic-settings` — treat them as such, not as fixed truths. Design the DLQ review unit as "a município's batch" not "a row."

**Warning signs:** DLQ depth grows monotonically; Mar push rate near zero in week 1; >80% of Rio output carries `routing='dlq'`; score histogram has a single tall spike between 50 and 85.

### Pitfall 2: Embedding Dedup False Merges — Territorial Homonyms

**What goes wrong:** "Trancoso" (a distrito/destino) merges into "Porto Seguro" (its parent município). "São Domingos/BA" merges with "São Domingos/SE." pgvector's HNSW index is *approximate by design* — "no similar vector found" does not mean "no duplicate exists."

**Why it happens:** Embedding similarity conflates lexical/semantic similarity with territorial identity. A distrito and its parent are semantically very close. Homonym municipalities are lexically identical.

**How to avoid:** Never compare vectors across UF boundaries. Block by `uf + municipio_id` before any vector comparison. Measure pgvector recall (at chosen `ef_search`) on a labeled duplicate set — do not assume. Cache embeddings at ingest; re-embed only on name change.

**Warning signs:** Mar count is suspiciously lower than known municipality counts; a distrito disappears into its parent; two reviewers find the same praia listed twice.

### Pitfall 3: pact-python 2.x vs 3.x API Confusion

**What goes wrong:** Documentation examples and StackOverflow answers are predominantly for pact-python 2.x (`Consumer("X")`, `Provider("Y")`, `pact.Pact()` constructor). The current release is 3.4.0 with a different API (`pact.v3`, `Pact("consumer", "provider")` context manager). Using 2.x patterns with the 3.x package raises import errors or subtle behavioral differences.

**Why it happens:** pact-python 3.x is a significant API rewrite. Training data and web documentation lag the current release.

**How to avoid:** Plan the Pact task against the `pact.v3` module. Read the pact-python 3.x changelog / migration guide before implementing. The 3.x API is more Pythonic (context manager, fluent builder). Pin `pact-python==3.4.*` explicitly.

**Warning signs:** `ImportError: cannot import name 'Consumer' from 'pact'`; test files that use the old `pact.Pact(consumer=..., provider=...)` constructor style.

### Pitfall 4: Celery Operational — Poison Loop and Beat Duplication

**What goes wrong:** A malformed payload that crashes a worker gets re-queued and crashes again — looping forever. Two `celery beat` processes fire the same UF sweep twice. A retried task double-inserts a record or pushes Mar twice.

**Why it happens:** Celery defaults are tuned for fire-and-forget web tasks, not 24/7 stateful pipelines. `acks_late` + retries without idempotency keys cause duplicates. Redis at-least-once semantics surprise developers.

**How to avoid:** Idempotency keys on every write (canonical key / `source_ref`). Dead-letter quarantine after N failures (separate from the review DLQ). Single `celery beat` instance enforced (celery-redbeat handles this). Row-level locks (`SELECT FOR UPDATE`) on RioRecord state transitions. Tune visibility timeout > max task runtime.

**Warning signs:** A worker in a restart loop; queue depth growing while workers are idle; duplicate Nascente rows; Mar push called twice for the same `source_ref`.

### Pitfall 5: Real Network Calls Leaking into Tests

**What goes wrong:** A developer adds a test that calls OpenRouter "just to verify the prompt." CI starts needing an API key. The offline discipline degrades one test at a time.

**Why it happens:** The offline boundary depends on *every* external touch going through a client interface. One direct `httpx.get()` call bypasses it.

**How to avoid:** `pytest-socket` as a pytest plugin that fails (not silently passes) on any outbound network attempt. Combine with `--disable-socket` as the CI default. Real externals are opt-in by a flag (`RUN_REAL_EXTERNALS=1`). The client interface is the only allowed channel to the outside world.

**Warning signs:** `pytest-socket` not in dev dependencies; CI suddenly slow or requiring keys; "skip this test in CI" comments; flaky tests that pass locally but fail in CI.

### Pitfall 6: Score Config Not Versioned → Calibration Drift

**What goes wrong:** Weights change (tuning after first UF validation), but records scored under the old weights sit in Mar with no `score_version` tag. Re-scoring the same record with new weights gives a different result with no audit trail.

**Why it happens:** `score_version` is easy to forget when it seems like just a config detail.

**How to avoid:** The `score_version` field is mandatory on every RioRecord (score) and on the MarRecord push payload. Derive `score_version` from a hash or explicit tag of the weight set in `ScoreConfig`. The Pact contract payload must include `score_version` so norteia-api can store it.

---

## Code Examples

### §7.6 ScoreConfig via pydantic-settings

```python
# brave/config/settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class ScoreConfig(BaseSettings):
    # Weights (sum to 100)
    weight_origem: float = 30.0
    weight_completude: float = 20.0
    weight_corroboracao: float = 20.0
    weight_atualidade: float = 15.0
    weight_validacao_humana: float = 15.0
    # Thresholds
    threshold_mar: float = 85.0
    threshold_dlq: float = 51.0
    # Version stamp
    score_version: str = "v1.0"

    model_config = SettingsConfigDict(env_prefix="BRAVE_SCORE_")

class LLMConfig(BaseSettings):
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Primary slug + ordered fallback; validate each supports Mode.Tools before promoting
    deepseek_primary_slug: str = "deepseek/deepseek-chat"
    deepseek_fallback_slugs: list[str] = ["deepseek/deepseek-v3.2"]
    provider_data_collection: str = "deny"  # Enforce on every request
    usd_daily_budget: float = 10.0  # Enforcing cost guard ceiling

    model_config = SettingsConfigDict(env_prefix="BRAVE_LLM_")
```

### SQLAlchemy Models (core/models.py sketch)

```python
# brave/core/models.py
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Numeric, ForeignKey, DateTime, func
from pgvector.sqlalchemy import Vector
import uuid

class Base(DeclarativeBase):
    pass

class NascenteRecord(Base):
    __tablename__ = "nascente_records"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(64))
    source_ref: Mapped[str] = mapped_column(String(256), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))  # "destination" | "attraction"
    uf: Mapped[str] = mapped_column(String(2))
    payload: Mapped[dict]  # JSONB (SQLAlchemy JSON type → JSONB on PG)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(default=1)
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # FK to the next version of this source_ref (for supersession)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nascente_records.id"), nullable=True)

class RioRecord(Base):
    __tablename__ = "rio_records"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nascente_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nascente_records.id"))
    entity_type: Mapped[str] = mapped_column(String(64))
    uf: Mapped[str] = mapped_column(String(2))
    municipio_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Routing: "in_progress" | "mar" | "dlq" | "descarte"
    routing: Mapped[str] = mapped_column(String(32), default="in_progress", index=True)
    dlq_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Sub-state for Atrativos lane (Phase 3); null for Destinos
    sub_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalized: Mapped[dict | None]       # JSONB; normalized record
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    score_breakdown: Mapped[dict | None]  # JSONB; per-criterion breakdown
    score_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime | None]
    canonical_key: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)

class MarRecord(Base):
    __tablename__ = "mar_records"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    rio_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rio_records.id"))
    entity_type: Mapped[str] = mapped_column(String(64))
    source_ref: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    canonical: Mapped[dict]     # JSONB; canonical fields
    provenance: Mapped[dict]    # JSONB; sources + per-criterion breakdown + decisions
    reliability_score: Mapped[float] = mapped_column(Numeric(5, 2))
    score_version: Mapped[str] = mapped_column(String(64))
    parent_mar_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mar_records.id"), nullable=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mar_records.id"), nullable=True)
    published_at: Mapped[datetime] = mapped_column(server_default=func.now())

class LLMGeneration(Base):
    __tablename__ = "llm_generations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    lane: Mapped[str] = mapped_column(String(64))       # "core" | "destinos" | "atrativos"
    model_slug: Mapped[str] = mapped_column(String(128))
    resolved_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int]
    completion_tokens: Mapped[int]
    usd_cost: Mapped[float] = mapped_column(Numeric(10, 6))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

### Alembic HNSW Index Migration

```python
# alembic/versions/0002_add_hnsw_index.py
from alembic import op

def upgrade():
    # HNSW index for pgvector fuzzy dedup
    # Requires pgvector server extension >= 0.5 (confirmed: 0.8.x in docker image)
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS
        rio_records_embedding_hnsw_idx
        ON rio_records
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
    """)

def downgrade():
    op.execute("DROP INDEX IF EXISTS rio_records_embedding_hnsw_idx;")
```

### docker-compose.yml for Offline Test Suite

```yaml
# docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg17  # pgvector 0.8.x bundled
    environment:
      POSTGRES_DB: norteia_brave
      POSTGRES_USER: brave
      POSTGRES_PASSWORD: brave
    ports:
      - "5432:5432"
    volumes:
      - ./postgres-data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - ./redis-data:/data
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| psycopg2 | psycopg 3.x | 2022 | Async support, cleaner pgvector/SQLAlchemy integration |
| IVFFlat pgvector index | HNSW pgvector index | 2023 (pgvector 0.5+) | Better recall under continuous writes; no rebuild needed |
| File-based Celery beat | celery-redbeat | 2015+ (well established) | Schedule survives restarts; multi-worker safe |
| Naive `response_format=json_object` | instructor Mode.Tools | 2024 | Pydantic retry loop; validation errors fed back to model; mandatory 2nd-layer |
| pact-python 2.x API | pact-python 3.x API | 3.0 released 2024 | Modernized Python API; `pact.v3` module; 3.4.0 is current |
| `openai` < 1.0 | `openai` 2.x | 2024 | Fully typed, async-native; OpenRouter-compatible via `base_url` |
| SQLAlchemy 1.x | SQLAlchemy 2.0 | 2023 | Typed API, `Mapped[]` annotations, async-native |

**Deprecated/outdated:**
- `psycopg2`: Maintenance mode — do not use in this project.
- `googlemaps` (legacy client): Targets the deprecated Places API. `google-maps-places` (New) is needed for Phase 3 fields (`business_status`, `reviews[].publishTime`, `weekday_text`). This phase stubs the client interface; the real impl lands in Phase 3.
- pact-python 2.x style imports: `from pact import Consumer, Provider` is 2.x. Use `from pact.v3 import Pact`.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | All listed PyPI packages are legitimate (slopcheck not available for verification) | Package Legitimacy Audit | Low probability; all are well-established projects, but risk exists that a package name is squatted or the installed version has a regression |
| A2 | pact-python 3.x API uses `pact.v3.Pact` as the entry point | Code Examples (Pact) | Tasks built against 3.x API may need adjustment if the API surface differs; consult official pact-python 3.x docs during planning |
| A3 | The Postgres Docker image `pgvector/pgvector:pg17` ships pgvector 0.8.x | docker-compose.yml | If the image ships an older pgvector, HNSW may be unavailable (requires >= 0.5) — verify image version during Wave 0 |
| A4 | §7.6 weights (origem 30 / completude 20 / corroboração 20 / atualidade 15 / validação humana 15) and thresholds (50/85) are correct starting points | Score engine | Distribution may not be workable at cold start — the simulation harness (SCORE-02, D-14) is the verification gate; treat thresholds as tunable |
| A5 | uv is the preferred package manager for this project | Project scaffold | CLAUDE.md does not specify a package manager; uv is available (0.11.7) and recommended for Python 3.12+ projects; confirm with project owner if pip/poetry is preferred |

---

## Open Questions

1. **Package manager preference (uv vs pip-tools vs poetry)**
   - What we know: `uv` is available (0.11.7) on the dev machine. No `pyproject.toml` exists yet. The CLAUDE.md does not specify a package manager.
   - What's unclear: Project owner preference for lockfile format and CI compatibility.
   - Recommendation: Use `uv` with `pyproject.toml` + `uv.lock`. uv is the fastest and most modern option. If the project owner has a strong preference for pip-tools or poetry, the planner can substitute — the decision does not affect any other architecture choices.

2. **pgvector Docker image exact version**
   - What we know: The Python `pgvector` adapter is 0.4.2; HNSW requires server-side pgvector >= 0.5.
   - What's unclear: Which Docker image tag ships pgvector 0.8.x. `pgvector/pgvector:pg17` is the official image but the tag may not always pin the extension version.
   - Recommendation: Use `pgvector/pgvector:pg17` and add a Wave 0 verification step that runs `SELECT extversion FROM pg_extension WHERE extname = 'vector';` to confirm >= 0.8.

3. **pact-python 3.x consumer API exact surface**
   - What we know: pact-python 3.4.0 is current; the 2.x API is deprecated; the module is `pact.v3`.
   - What's unclear: The exact builder syntax for the consumer DSL in 3.x (the Code Examples section shows a plausible pattern, but should be verified against the official 3.x docs/examples before the contract task is written).
   - Recommendation: The planner should include a sub-task "read pact-python 3.x migration guide and pin the exact DSL pattern" at the start of the Pact wave.

4. **Score version strategy — hash vs explicit tag**
   - What we know: `score_version` must stamp each scored record so re-scores are comparable (D-13).
   - What's unclear: Whether `score_version` should be a human-readable tag (e.g., `"v1.0"`) or a deterministic hash of the weight set (tamper-evident but opaque).
   - Recommendation: Use a human-readable semantic tag (e.g., `"v1.0"`) set in `pydantic-settings`. This is easier to reason about during calibration. Add a validation that the tag changes if any weight changes.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker | docker-compose offline suite | ✓ | 29.4.0 | — |
| Docker Compose | Postgres+Redis for tests | ✓ | v5.1.2 | — |
| Python | Runtime | ✓ | 3.13.11 | Use 3.12 venv if C-extension wheels fail |
| uv | Package management | ✓ | 0.11.7 | pip 25.3 (also available) |
| pip | Package management | ✓ | 25.3 | — |
| psql | DB connectivity check | ✗ | — | Use `docker exec` to run psql inside container |
| redis-cli | Redis connectivity check | ✗ | — | Use `docker exec` to ping Redis inside container |

**Missing dependencies with no fallback:** None that block execution.

**Missing dependencies with fallback:**
- `psql` — use `docker exec norteia-brave-postgres-1 psql ...` for DB checks.
- `redis-cli` — use `docker exec norteia-brave-redis-1 redis-cli ping` for Redis checks.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Partial | Service token (Sanctum ability) on the Mar push; Bearer header on FastAPI (dashboard Phase 4) |
| V3 Session Management | No | No user sessions; service-to-service token |
| V4 Access Control | Yes | Mar push endpoint authenticated; error-report webhook authenticated + rate-limited |
| V5 Input Validation | Yes | pydantic validates all inbound payloads (webhook, API); instructor validates all LLM output |
| V6 Cryptography | No | No custom crypto; service token managed by norteia-api (Sanctum) |

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Unauthenticated error-report webhook → reopen/poison Mar records | Tampering | Authenticate webhook with a shared secret; validate `source_ref` exists; rate-limit per IP |
| SQL injection via JSONB payload fields | Tampering | SQLAlchemy parameterized queries; never interpolate payload strings into SQL |
| Cost blowup via Celery fan-out misconfiguration | Denial of Service | USD cost guard (OBS-02) as hard circuit breaker; per-day Redis counter; halt on breach |
| Poison message looping in Celery queue | Denial of Service | `max_retries=3`; poison quarantine after N failures; separate from review DLQ |
| LLM provider data retention (paid ≠ private) | Information Disclosure | `provider.data_collection: deny` on every OpenRouter request + account setting; assert in unit test |
| Service token for norteia-api push leaked | Elevation of Privilege | Token in env var (never in code); `BRAVE_LLM_*` and `BRAVE_DB_*` env vars; .env in .gitignore; keyless CI |

---

## Sources

### Primary (HIGH confidence)
- `.planning/phases/01-brave-core-score-gate-boundary-contract/01-CONTEXT.md` — locked decisions D-01..D-21, exact scope
- `.planning/REQUIREMENTS.md` — CORE-01..12, SCORE-01..03, OBS-01..04, CNTR-01..02, TEST-01, TEST-03
- `.planning/research/STACK.md` — validated 2026 stack, version pins from PyPI/npm
- `.planning/research/ARCHITECTURE.md` — medallion mapping, table-per-layer, sub_state, supersession, build order, package boundaries
- `.planning/research/PITFALLS.md` — DLQ-landfill, dedup false-merge, Celery operational, Pact drift, cost guard (all Phase 1-relevant)
- `docs/PLANO-BRAVE.md` §B.1/§B.5/§B.6/§B.7/§C — full plan; authoritative for this milestone
- PyPI registry (`pip index versions`) — fastapi 0.136.3, celery 5.6.3, redis 8.0.x, psycopg 3.3.4, pgvector 0.4.2, sqlalchemy 2.0.50, alembic 1.18.4, instructor 1.15.1, openai 2.41.1, anthropic 0.109.1, pact-python 3.4.0, respx 0.23.1, vcrpy 8.1.1, fakeredis 2.36.1, pytest 9.0.3, pytest-socket 0.8.0, celery-redbeat 2.3.3, tenacity 9.1.4, structlog 26.1.0, pydantic-settings 2.14.1, langgraph 1.2.4, langchain-core 1.4.6, flower 2.0.1, uvicorn 0.49.0, httpx 0.28.1, celery-types 0.26.0

### Secondary (MEDIUM confidence)
- `.planning/research/SUMMARY.md` — synthesized findings, confidence levels, phase ordering rationale
- `.planning/research/FEATURES.md` — feature dependencies, "don't hand-roll" analysis, MVP definition
- python.useinstructor.com/integrations/deepseek/ — DeepSeek Mode.Tools default; MD_JSON for reasoning models
- pgvector README (github.com/pgvector/pgvector) — HNSW approximate; ef_search recall knob; requires extension >= 0.5 for HNSW

### Tertiary (LOW confidence — [ASSUMED])
- Score-distribution calibration (50/85 thresholds, §7.6 weights against real intake): domain reasoning; the simulation harness is the required validation gate
- pact-python 3.x exact DSL surface: inferred from 2.x→3.x release pattern; must be verified against official 3.x docs during planning

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all versions pinned from PyPI registry in this session
- Architecture: HIGH — fully specified in CONTEXT.md (D-01..D-21) + ARCHITECTURE.md; no alternatives to research
- Pitfalls: HIGH (DLQ-landfill, dedup, Celery operational) / MEDIUM (score calibration thresholds — mitigation is the simulation harness)
- Pact contract API: MEDIUM — pact-python 3.x is current but exact DSL must be confirmed against official docs

**Research date:** 2026-06-11
**Valid until:** 2026-07-11 (stable stack; model slug lineup may shift faster — re-verify DeepSeek slugs at Phase 2 build time)
