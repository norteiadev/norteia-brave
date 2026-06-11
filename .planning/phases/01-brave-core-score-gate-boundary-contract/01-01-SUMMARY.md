---
phase: 01-brave-core-score-gate-boundary-contract
plan: "01"
subsystem: scaffold
tags:
  - python
  - sqlalchemy
  - alembic
  - pgvector
  - pydantic-settings
  - docker-compose
  - tdd
dependency_graph:
  requires: []
  provides:
    - brave-package-skeleton
    - sqlalchemy-models
    - alembic-migrations
    - pydantic-settings-config
    - client-protocol-boundary
    - docker-compose-offline-stack
  affects:
    - all subsequent plans in Phase 1
    - Phase 2 Destinos lane
    - Phase 3 Atrativos lane
tech_stack:
  added:
    - Python 3.12 (uv-managed venv)
    - FastAPI 0.136.3
    - SQLAlchemy 2.0.50
    - Alembic 1.18.4
    - psycopg 3.3.4 (binary)
    - pgvector 0.4.2
    - pydantic-settings 2.14.1
    - Celery 5.6.3 + celery-redbeat 2.3.3
    - redis 8.0.0
    - structlog 26.1.0
    - tenacity 9.1.4
    - httpx 0.28.1
    - instructor 1.15.1
    - pytest 9.0.3 + fakeredis 2.36.1 + respx 0.23.1
    - pact-python 3.4.0
    - pytest-socket 0.8.0
  patterns:
    - SQLAlchemy 2.0 typed mapped_column() API
    - pydantic-settings env_prefix isolation (BRAVE_SCORE_, BRAVE_LLM_, BRAVE_DB_)
    - Table-per-layer medallion model (D-01)
    - Routing values (not tables) for DLQ/descarte (D-02)
    - Supersession versioning via superseded_by FK (D-03)
    - Protocol structural typing for client boundary (D-09, D-18)
key_files:
  created:
    - pyproject.toml
    - docker-compose.yml
    - .env.example
    - Makefile
    - alembic.ini
    - alembic/env.py
    - alembic/versions/0001_init_nascente_rio_mar.py
    - alembic/versions/0002_add_hnsw_index.py
    - brave/__init__.py
    - brave/cli.py
    - brave/config/__init__.py
    - brave/config/settings.py
    - brave/core/__init__.py
    - brave/core/models.py
    - brave/lanes/__init__.py
    - brave/lanes/base.py
    - brave/clients/__init__.py
    - brave/clients/base.py
    - brave/observability/__init__.py
    - brave/tasks/__init__.py
    - brave/api/__init__.py
    - dashboard/.gitkeep
    - tests/__init__.py
    - tests/conftest.py
    - tests/fakes/__init__.py
    - tests/unit/__init__.py
    - tests/unit/test_scaffold_smoke.py
    - tests/integration/__init__.py
    - tests/contract/__init__.py
  modified:
    - .gitignore (added pacts/, *.pact.json entries)
decisions:
  - "D-01 implemented: table-per-layer (nascente_records / rio_records / mar_records)"
  - "D-02 implemented: routing column on RioRecord; DLQ/descarte are values not tables"
  - "D-03 implemented: superseded_by_id FK on NascenteRecord and MarRecord"
  - "D-08 implemented: HNSW index on rio_records.embedding (m=16, ef_construction=64)"
  - "D-09 implemented: 8 client Protocol interfaces in brave/clients/base.py"
  - "D-10 implemented: deepseek_primary_slug + fallbacks in LLMConfig"
  - "D-12 implemented: ScoreConfig with exact §7.6 weights and thresholds"
  - "D-13 implemented: score_version column on RioRecord and MarRecord"
  - "D-15 implemented: MarRecord.source_ref UNIQUE constraint"
  - "D-18 implemented: brave/core, brave/lanes, brave/clients package boundaries"
  - "D-19 implemented: psycopg 3 binary driver (postgresql+psycopg://...)"
  - "HNSW migration uses standard CREATE INDEX (not CONCURRENTLY) because PostgreSQL forbids CONCURRENTLY in a transaction block; production live-migration approach documented in code comment"
metrics:
  duration: "10 minutes"
  completed: "2026-06-11"
  tasks_completed: 1
  tasks_total: 1
  files_created: 29
  files_modified: 1
---

# Phase 1 Plan 01: Scaffold — Models, Migrations, Config, Client Protocols Summary

**One-liner:** Python 3.12 package scaffold with SQLAlchemy medallion models (Nascente/Rio/Mar), Alembic migrations with pgvector 0.8.2 HNSW index, pydantic-settings ScoreConfig/LLMConfig/DBConfig, 8 client Protocol interfaces, and docker-compose offline stack — all passing 54 unit smoke tests.

## What Was Built

### Project scaffold (pyproject.toml + tooling)
- uv-managed Python 3.12 project with all 25 pinned runtime and dev dependencies
- hatchling build backend with explicit `packages = ["brave"]` declaration
- ruff (lint + format) and pyright (static typing) configured
- pytest with asyncio_mode = "auto"

### Docker-compose offline stack
- `pgvector/pgvector:pg17` — confirmed ships pgvector 0.8.2 (>= 0.8 required)
- `redis:7-alpine` — Celery broker + cost-guard counters
- Both services with health checks (pg_isready / redis-cli ping)

### Alembic migrations
- `0001_init_nascente_rio_mar.py`: creates all 6 tables with exact column types, FKs, indexes, and `CREATE EXTENSION IF NOT EXISTS vector` for pgvector
- `0002_add_hnsw_index.py`: HNSW index `rio_records_embedding_hnsw_idx` on `rio_records.embedding` (vector_cosine_ops, m=16, ef_construction=64)

### SQLAlchemy models (brave/core/models.py)
Six `DeclarativeBase` mapped classes implementing decisions D-01..D-04, D-13, D-15:
- `NascenteRecord` — immutable, source-tagged, versioned, JSONB payload, supersession FK
- `RioRecord` — mutable, routing/sub_state column (not separate tables), HNSW embedding, score_version
- `MarRecord` — canonical, UNIQUE source_ref for idempotent push, supersession FK
- `LLMGeneration` — observability: every LLM call logged
- `AuditLog` — steward + pipeline audit trail
- `PoisonQuarantine` — Celery poison messages (distinct from §7.6 DLQ)

### pydantic-settings config (brave/config/settings.py)
- `ScoreConfig` (prefix `BRAVE_SCORE_`): §7.6 weights (30/20/20/15/15), thresholds (85/51), score_version
- `LLMConfig` (prefix `BRAVE_LLM_`): OpenRouter URL, DeepSeek primary slug + fallbacks, provider_data_collection="deny" (security invariant)
- `DBConfig` (prefix `BRAVE_DB_`): required DB URL + Redis URL
- `AppConfig`: aggregates ScoreConfig + LLMConfig + run_real_externals flag

### Client Protocol boundary (brave/clients/base.py)
8 typed Protocol interfaces using structural typing (no isinstance checks):
LLMClientProtocol, NorteiaApiClientProtocol, PlacesClientProtocol, OTAClientProtocol, ApifyClientProtocol, WhatsAppClientProtocol, MturClientProtocol, NotebookLMClientProtocol

### Package boundaries (D-18)
- `brave/core/` — entity-agnostic (lanes import core, never reverse)
- `brave/lanes/` — pluggable producers (base.py stub; Phase 2 fills Destinos/Atrativos)
- `brave/clients/` — network boundary (all 8 Protocols + future real impls)
- `brave/observability/` — llm_tracker, cost_guard, audit stubs
- `brave/tasks/` — Celery app, beat schedule, pipeline task stubs
- `brave/api/` — FastAPI app + router stubs

### Test harness
- 54 unit smoke tests in `tests/unit/test_scaffold_smoke.py`
- `tests/conftest.py` with session-scoped fixtures: db_engine, db_session, fake_redis, app_config, score_config
- Test package structure: unit/, integration/, fakes/, contract/

## Acceptance Criteria Status

| Criterion | Status |
|-----------|--------|
| `uv pip install -e ".[dev]"` exits 0, no conflicts | PASS |
| `docker compose up -d` boots postgres+redis healthy | PASS |
| `alembic upgrade head` creates all 6 tables | PASS — confirmed via information_schema |
| `SELECT extversion FROM pg_extension WHERE extname='vector'` >= '0.8' | PASS — 0.8.2 |
| HNSW index `rio_records_embedding_hnsw_idx` exists | PASS — confirmed via pg_indexes |
| `pytest tests/unit/test_scaffold_smoke.py` exits 0 | PASS — 54/54 |
| All 8 client Protocols importable without ImportError | PASS |
| `ScoreConfig().weight_origem == 30.0 and .threshold_mar == 85.0` | PASS |
| `NascenteRecord` has NO routing column | PASS |
| `RioRecord` HAS routing column | PASS |
| `MarRecord.source_ref` is UNIQUE | PASS |

## TDD Gate Compliance

- RED gate: `test(01-01)` commit `40f4cf3` — 54 failing tests written before any implementation
- GREEN gate: `feat(01-01)` commit `32b3b1c` — implementation passes all 54 tests

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocker] hatchling build backend requires explicit packages configuration**
- **Found during:** Task 1 — `uv pip install -e ".[dev]"` failed
- **Issue:** The project directory is named `brave/` but the package name is `norteia-brave`; hatchling couldn't auto-detect which directory to ship
- **Fix:** Added `[tool.hatch.build.targets.wheel] packages = ["brave"]` to pyproject.toml
- **Files modified:** pyproject.toml
- **Commit:** included in `32b3b1c`

**2. [Rule 3 - Blocker] HNSW migration CONCURRENTLY incompatible with Alembic transaction**
- **Found during:** Task 1 — `alembic upgrade head` failed with `CREATE INDEX CONCURRENTLY cannot run inside a transaction block`
- **Issue:** PostgreSQL forbids `CONCURRENTLY` inside a transaction; Alembic wraps migrations in transactions; `transaction_per_migration=False` + `connection.execution_options(isolation_level="AUTOCOMMIT")` both failed because the connection already had an active transaction object
- **Fix:** Removed `CONCURRENTLY` keyword from 0002 migration; replaced with standard `CREATE INDEX IF NOT EXISTS`. On an empty dev/CI table this is correct. Production live-migration approach (psql outside Alembic) documented in migration code comment.
- **Files modified:** alembic/versions/0002_add_hnsw_index.py
- **Impact:** Index is functionally identical (HNSW with same m/ef_construction); only build-time behavior changed (brief table lock during empty-table migration vs lock-free concurrent build). Production operators who need zero-downtime re-indexing on a live table should use the CONCURRENTLY variant directly in psql.
- **Commit:** included in `32b3b1c`

## Known Stubs

| Stub | File | Reason | Resolved by |
|------|------|--------|-------------|
| `brave/cli.py run-fixture` prints message only | brave/cli.py | Full pipeline (Nascente→score→Mar push) not built yet | Plan 01-02/01-03 |
| `brave/observability/__init__.py` — empty stub | brave/observability/__init__.py | llm_tracker, cost_guard, audit modules built in Plan 01-02 | Plan 01-02 |
| `brave/tasks/__init__.py` — empty stub | brave/tasks/__init__.py | Celery app, beat schedule, pipeline tasks built in Plan 01-02 | Plan 01-02 |
| `brave/api/__init__.py` — empty stub | brave/api/__init__.py | FastAPI app and routers built in Plan 01-03 | Plan 01-03 |
| `brave/lanes/base.py` — Lane Protocol stub only | brave/lanes/base.py | Concrete lanes (Destinos/Atrativos) in Phase 2/3 | Phase 2 Plan 02-01 |
| `tests/fakes/__init__.py` — empty | tests/fakes/__init__.py | FakeLLMClient, FakeNorteiaApiClient built in Plan 01-02 | Plan 01-02 |

These stubs are intentional and do not prevent this plan's goal (scaffold + import tests) from being achieved.

## Threat Flags

No new security-relevant surface introduced beyond what is in the plan's threat model:
- `.env.example` has no real values (T-01-02 mitigated)
- `.gitignore` covers `.env`, `.env.*`, `pacts/` (T-01-02 mitigated)
- All packages installed from pre-vetted list (T-01-SC mitigated per autonomous authorization)
- Migration idempotent via `IF NOT EXISTS` and revision chain (T-01-01 mitigated)

## Self-Check: PASSED

Files confirmed present:
- pyproject.toml: FOUND
- docker-compose.yml: FOUND
- brave/config/settings.py: FOUND
- brave/core/models.py: FOUND
- brave/clients/base.py: FOUND
- alembic/versions/0001_init_nascente_rio_mar.py: FOUND
- alembic/versions/0002_add_hnsw_index.py: FOUND
- tests/unit/test_scaffold_smoke.py: FOUND
- .planning/phases/01-brave-core-score-gate-boundary-contract/01-01-SUMMARY.md: FOUND

Commits confirmed:
- 40f4cf3: test(01-01) RED gate
- 32b3b1c: feat(01-01) GREEN gate

Database confirmed:
- All 6 tables created by alembic upgrade head
- pgvector 0.8.2 >= 0.8 requirement met
- HNSW index rio_records_embedding_hnsw_idx exists
- 54/54 unit smoke tests pass
