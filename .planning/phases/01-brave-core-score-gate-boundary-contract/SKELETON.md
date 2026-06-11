# Walking Skeleton — norteia-brave (Pipeline Brave)

**Phase:** 1
**Generated:** 2026-06-11

## Capability Proven End-to-End

A fixture payload flows Nascente → Rio (dedup/normalize/score §7.6) → route to Mar, DLQ, or descarte → Mar record is pushed to a Pact mock of norteia-api, and the Pact consumer test passes — all running offline via `docker-compose up` with Postgres+pgvector and Redis, no external keys.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Runtime | Python 3.12 | Best LLM/data ecosystem; async-mature; psycopg3 + pgvector wheels confirmed for 3.12 |
| API framework | FastAPI 0.136.x + Uvicorn 0.49.x | Async-native, Pydantic-native, OpenAPI for the Phase 4 dashboard client |
| Task queue | Celery 5.6.x + Redis 7/8, celery-redbeat 2.3.x | 24/7 orchestration; redbeat = single-source-of-truth schedule in Redis; beat not file-based |
| Database | PostgreSQL 17 (pgvector/pgvector:pg17 image, extension 0.8.x) | JSONB Nascente + relational Rio/Mar + pgvector HNSW fuzzy dedup in one DB |
| ORM + migrations | SQLAlchemy 2.0.50 + Alembic 1.18.x | Typed 2.0 API; pgvector.sqlalchemy for Vector columns; standard migration tooling |
| Postgres driver | psycopg 3.3.4 (binary) | Covers sync (Celery) + async (FastAPI); cleaner pgvector/SQLAlchemy 2.0 story than psycopg2 |
| Package manager | uv 0.11.7 | Fastest install; lock file; available on dev machine |
| Config | pydantic-settings 2.14.x | 12-factor; ScoreConfig weights + thresholds; LLMConfig pinned DeepSeek slug + fallbacks; DBConfig; env_prefix isolation |
| Layer storage | Table-per-layer (nascente_records / rio_records / mar_records) per D-01 | Immutable raw layer never coupled to mutable lifecycle; independent indexing (pgvector on Rio only) |
| DLQ/descarte | routing column on rio_records per D-02 | Routing values, not tables; DLQ = queryable rows; dashboard mutates in place |
| Versioning | Supersession (append + superseded_by FK) per D-03 | Full lineage; idempotent push; safe error-report reopen |
| Score engine | Pure zero-I/O function; weights in pydantic-settings per D-12 | Deterministic gate; exhaustively unit-testable; calibrable without redeploys |
| Dedup strategy | Exact hash blocking → territorial-key block (UF+município) → pgvector HNSW fuzzy per D-07/D-08 | Prevents Trancoso/Porto Seguro false merge and UF-homonym merges |
| LLM client | instructor 1.15.x + Mode.Tools + openai 2.41.x (OpenRouter endpoint) per D-09 | Mandatory Pydantic retry loop; DeepSeek native function calling; Phase 1 uses fake only |
| External boundary | clients/ Protocol interfaces + fake impls in tests/fakes/ per D-18 | Single testability seam; 100%-offline suite; every external call through here |
| Ingestion contract | pact-python 3.4.x consumer test (pact.v3 API) per D-16 | Frozen Mar push shape; cheap-early/expensive-late; both lanes + norteia-api repo depend on stability |
| Observability | llm_generations table + Redis USD cost guard (pre-dispatch) + structlog JSON audit per D-20/D-21 | Cost guard is enforcing (halts), not advisory; every LLM call logged |
| Offline test discipline | pytest-socket blocks all real network by default; real = RUN_REAL_EXTERNALS=1 per TEST-01 | Hard CI failure on any outbound call; CI runs keyless |
| Ingest entrypoint | `docker-compose up` (Postgres+Redis) + `make pipeline/run` fixture CLI | Substitutes for "one real UI interaction" — operator runs one end-to-end pipeline pass |

## Stack Touched in Phase 1

- [x] Project scaffold (pyproject.toml, uv, .env.example, Makefile, ruff, pyright)
- [x] Routing — FastAPI app with /api/v1/health, /api/v1/metrics, /api/v1/dlq, /api/v1/audit, /webhook/error-report
- [x] Database — Alembic migrations for all Phase 1 tables; real DB write from Nascente.store_raw; real read from Rio DLQ query
- [x] End-to-end pipeline run — `make pipeline/run` exercises fixture → Nascente → Rio → score → route → Mar push (against Pact mock)
- [x] Contract — pact-python 3.x consumer test generates Pact JSON file and passes offline

## Package Boundaries (load-bearing, per D-18)

```
brave/
  core/       entity-agnostic: models, nascente/, rio/, mar/, score/
  lanes/      pluggable producers: base.py stub (Phase 2 fills Destinos/Atrativos)
  clients/    network boundary: Protocol interfaces + norteia_api real impl
  observability/ llm_tracker, cost_guard, audit
  tasks/      celery_app, beat_schedule, pipeline tasks
  api/        FastAPI app + routers
  config/     settings, score_weights
tests/
  unit/       pure-function tests (score engine, simulation, routing)
  integration/ docker-compose DB+Redis tests (nascente, rio, mar, celery, cost_guard, webhook)
  fakes/      Protocol-implementing fakes (FakeLLMClient, FakeNorteiaApiClient, FakePlacesClient...)
  contract/   pact-python 3.x consumer test
```

## Constraint: No Real External API Calls

Phase 1 delivers the client *seam* and fakes, not live integrations. The NorteiaApiClient real impl exists only to satisfy the Pact test against pact-python's mock server. All other clients (Places, OTA, Apify, WhatsApp, Mtur, NotebookLM, LLM) are stub Protocol definitions with fake implementations in tests/fakes/.

## Out of Scope (Deferred to Later Phases)

- Any lane producer: Mtur/NotebookLM/Desmembramento (Phase 2) and Discovery/Contact/Signal/WhatsApp (Phase 3)
- Real external API calls (Places, OpenRouter/DeepSeek, Anthropic, Apify, Twilio, Mtur, NotebookLM)
- Dashboard UI Next.js (Phase 4) — directory stub only
- Active freshness-decay / re-score cron §7.8 (v2 FRESH-01)
- Auto-tuning §7.6 weights from steward decisions (v2 TUNE-01)
- OTA price cross-check (v2 OTA-01)
- Temporal durable-workflow engine (deferred per D-06)
- LangGraph agent graphs (Phase 2/3; lanes/ stub only this phase)

## Subsequent Slice Plan

- Phase 2: Destinos lane — MturSeedIngest + NotebookLMIngest + DesmembramentoAgent → DLQ → batch-by-state human validation → Mar push to destinations
- Phase 3: Atrativos lane — Discovery/ContactFinder/Signal sub-state machine + WhatsApp gate + LangGraph conversation + LGPD/BSP compliance
- Phase 4: Next.js territorial CMS dashboard — Brave monitor, DLQ review, WhatsApp gate, funnels, LLM cost view, Bearer auth
