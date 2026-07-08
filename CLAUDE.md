<!-- GSD:project-start source:PROJECT.md -->
## Project

**norteia-brave — Pipeline Brave (Collector)**

`norteia-brave` is a standalone Python service that **is** the Norteia **Pipeline Brave** — a 24/7 data-collection and reliability-scoring engine that populates Norteia's territorial base (every Brazilian state, from a cold "carga inicial" start). It ingests raw territorial data from many sources, cleans/dedups/normalizes/scores it (reliability framework), and only publishes high-confidence canonical records ("Mar", ≥85%) to the consuming `norteia-api` (Laravel). It ships with a Next.js operations dashboard that is the **territorial CMS**: Brave monitor, DLQ human-review queue, WhatsApp gate, funnels, and cost/LLM observability.

This milestone delivers the **entity-agnostic Brave core** plus its **first two collection lanes: Destinos (destinations) and Atrativos (attractions)**.

**Core Value:** Only **validated, reliability-scored canonical records reach the platform** — the Brave pipeline (Nascente → Rio → Mar) with reliability scoring and a DLQ gate is the single thing that must work. Everything downstream (UI, AI assistants) depends on Mar being trustworthy.

### Constraints

- **Tech stack (collector)**: Python — FastAPI · Celery+Redis · LangGraph · Pydantic+`instructor` · PostgreSQL (JSONB Nascente, pgvector for dedup). The CLAUDE.md "PHP fixed" lock governs only norteia-api, not this repo.
- **Tech stack (dashboard)**: Next.js + Bun (Node 22), Bearer-header auth, Vitest + MSW.
- **Execution**: continuous 24/7 service covering all BR states.
- **Gating**: reliability score + DLQ is the canonical gate — not human-approve-everything.
- **Testing**: no test hits Places/OTA/Apify/WhatsApp/OpenRouter/Anthropic/Mtur/norteia-api by default; real = opt-in flag; CI runs without keys. Logic lives in code (Brave core, score engine, desmembramento, conversation); n8n is thin transport. Contract with norteia-api verified via Pact.
- **Compliance**: LGPD, WhatsApp BSP (templates/24h window/opt-out), Meta ToS (no automated DM), Google Places ToS (persist place_id, canonical = first-party validated), OTA partner approval, source-scraping legal risk documented per source.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Recommended Stack
### Core Technologies
| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| **Python** | 3.12 (3.13 ok) | Runtime | Async-mature, best LLM/data ecosystem. 3.12 is the safe production floor; 3.13 works but check C-extension wheels (psycopg, pgvector) first. |
| **FastAPI** | 0.136.x | API + WhatsApp/email webhooks + REST for dashboard + lane ingest | Async-native (matches outbound LLM/HTTP I/O), Pydantic-native (your validation layer is already Pydantic), OpenAPI auto-docs for the Next.js client. Industry default for this exact shape. **HIGH** |
| **Uvicorn** | 0.49.x | ASGI server | Standard FastAPI runtime. Run behind Gunicorn (`uvicorn.workers.UvicornWorker`) or use `--workers` for prod. **HIGH** |
| **Pydantic** | 2.13.x | Schemas, settings, LLM output validation | v2 (Rust core) is the validation backbone — Nascente payload schemas, score-engine config, and the mandatory 2nd-layer LLM validator all live here. **HIGH** |
| **PostgreSQL** | 16 or 17 | JSONB Nascente store + relational Brave state + pgvector | Single DB for raw payloads (JSONB), pipeline state, and dedup vectors avoids a second datastore. PG 16/17 both have mature JSONB + pgvector support. **HIGH** |
| **pgvector** (extension) | 0.8.x server-side / `pgvector` 0.4.x Python | Embedding-based fuzzy dedup in Rio | Keeps dedup in the same transaction as the canonical record. Adequate at all-Brazil scale (see Pitfalls). **HIGH** |
| **Celery** | 5.6.x | 24/7 orchestration, beat scheduling, fan-out by UF | Mature, battle-tested task queue. Fan-out-by-state and tolerant-of-day-scale-latency outreach fit Celery's model. Keep for this milestone. **HIGH** |
| **Redis** | 7.x or 8.x server / `redis` 8.0 Python client | Celery broker + result backend + rate-limit/cost-guard counters | Standard Celery broker; doubles as the cheap shared counter store for the USD cost guard and WhatsApp ramp limiter. **HIGH** |
| **LangGraph** | 1.2.x (+ `langchain-core` 1.4.x) | LLM agent orchestration (Desmembramento, Discovery/Contact/Signal, WhatsApp conversation graph) | Graph/state-machine model maps directly onto your explicit sub-state machines (`discovered → contacts_found → …`) and the multi-turn WhatsApp flow. v1.x is GA/stable. **HIGH** |
| **OpenAI SDK** | `openai` 2.41.x | Client for OpenRouter (DeepSeek backend extraction/scoring) | OpenRouter is OpenAI-compatible — point the OpenAI SDK at `https://openrouter.ai/api/v1`. One client, swappable provider. **HIGH** |
| **Anthropic SDK** | `anthropic` 0.109.x (Python) | Claude Sonnet 4.5 WhatsApp conversation | Quality PT-BR conversation per the LLM split. Native SDK (not via OpenRouter) for first-class streaming/tool-use + direct quota control. **HIGH** |
| **instructor** | 1.15.x | Structured LLM output (2nd-layer validation) | Wraps both clients; retries with validation errors fed back to the model. The enforcement layer that compensates for DeepSeek's looser schema adherence. Use **Mode.Tools** (see gotchas). **HIGH** |
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **psycopg** (v3) | 3.3.x | Postgres driver | Always. Use psycopg **3** (not psycopg2) — async support + better connection handling. Pair with `pgvector` Python adapter for vector types. |
| **SQLAlchemy** | 2.0.x | ORM / Core for Brave state tables | Always. 2.0 typed API. Use Core or ORM; pgvector integrates via `pgvector.sqlalchemy`. |
| **Alembic** | 1.18.x | DB migrations | Always — schema for Nascente/Rio/Mar/DLQ + `llm_generations` + audit. |
| **pydantic-settings** | 2.14.x | 12-factor config (slugs, weights, keys, flags) | Always — centralizes the pinned DeepSeek slug, calibrable reliability weights, and the test opt-in flags. |
| **celery-redbeat** | 2.3.x | Redis-backed Celery beat scheduler | Use instead of default file-based beat. Lets you run multiple beat-capable workers safely and store schedule in Redis (no single-point file). |
| **flower** | 2.0.x | Celery monitoring UI | Dev/ops visibility into queues/workers (complements your own Brave metrics). |
| **tenacity** | 9.1.x | Retry/backoff for external clients | Wrap Places/Apify/OTA/OpenRouter clients (429/5xx). Pairs with circuit-breaking around the cost guard. |
| **httpx** | 0.28.x | Async HTTP for custom clients | The client layer behind your network boundary (Places New, Apify, OTA, NotebookLM, norteia-api push). |
| **structlog** | 26.x | Structured logging → audit logs | Feeds the §15.7 audit-log requirement; JSON logs correlate with `llm_generations`. |
| **google-maps-places** | 0.9.x | Google Places API (New) client | Discovery/ContactFinder/Signal agents. This is the **New** Places client (Place Details / Text Search / Nearby). See gotcha re: legacy `googlemaps`. |
| **apify-client** | 3.0.x | IG/X scraping (best-effort signal) | SignalAgent only; behind the network boundary, always mockable. |
| **twilio** | 9.10.x | WhatsApp BSP (launch) | WhatsAppAgent transport if launching on Twilio (recommended launch path). |
### Development Tools
| Tool | Purpose | Notes |
|------|---------|-------|
| **pytest** | Test runner (100% offline suite) | 9.0.x. Core gate. Run via docker-compose with Postgres+Redis. |
| **respx** | Mock httpx calls (Places/OTA/Apify/Mtur/norteia-api) | 0.23.x. Preferred over VCR for deterministic, hand-authored client mocks. |
| **vcrpy** | Record/replay cassettes for externals | 8.1.x. Use for the few "shape-fidelity" cases (real Places JSON) you want captured once; keep cassettes scrubbed of PII/keys. |
| **fakeredis** | In-process Redis for unit tests | 2.36.x. Lets score/queue logic test without a Redis container in pure unit scope. |
| **pact-python** | Consumer-driven contract with norteia-api | Verifies the `POST /api/internal/territorial/...` ingestion contract per PLANO Part C. |
| **ruff** | Lint + format | Single fast tool; replaces flake8+isort+black. |
| **mypy** or **pyright** | Static typing | Enforce on score engine + client interfaces. `celery-types` 0.26.x adds Celery stubs. |
| **docker-compose** | Local Postgres+Redis for offline suite | Per Part C: CI runs keyless, externals opt-in by flag. |
### Dashboard (separate Next.js app)
| Technology | Version | Purpose | Notes |
|------------|---------|---------|-------|
| **Next.js** | 16.x | Territorial CMS / ops dashboard | App Router. Mirrors `norteia-frontend`. |
| **Node** | 22 LTS | Runtime target | Per constraint. |
| **Bun** | 1.3.x | Package manager / test runner / dev | `bun run test`, `bun install`. Fast; Node-22-compatible. |
| **Vitest** | 4.1.x | Unit/component tests | Per constraint. |
| **MSW** | 2.14.x | Mock the FastAPI surface (offline dashboard tests) | Per constraint; Bearer-header auth mocked at the network layer. |
## Installation
# Core (collector)
# Dev dependencies (collector)
# Dashboard
## LLM provider decision (resolves PLANO §B.6 open question)
| Item | Finding | Action |
|------|---------|--------|
| DeepSeek slug existence | As of mid-2026, **DeepSeek V4 Flash/Pro exist on OpenRouter** (1M context), alongside the V3.x `deepseek/deepseek-chat` family. The doc's "`deepseek-v4-flash` may not exist → fallback" hedge is now resolved — V4 is real. **MEDIUM** (model lineup moves fast). | Pin a **primary slug + ordered fallback list** in config. Suggested: primary `deepseek/deepseek-chat` (stable, broadly available), evaluate `deepseek/deepseek-v3.2` / V4 as throughput/quality upgrades. Validate each slug's tool-calling support before promoting. |
| `:nitro` throughput variant | `:nitro` is OpenRouter's throughput-optimized routing suffix — correct for batch/backend (latency-insensitive) extraction. **MEDIUM** | Keep `:nitro` for backend extraction/scoring/desmembramento. Do NOT use it where you need a specific provider's tool-calling fidelity (see gotcha). |
| "Paid ≠ won't train" | Correct concern. Set `provider.data_collection: "deny"` in the OpenRouter request body **and** the account setting. **HIGH** | Enforce in the client wrapper; assert it in a unit test. |
| WhatsApp conversation model | Claude Sonnet 4.5 via **native Anthropic SDK** (not OpenRouter) for conversation quality + direct quota/streaming control. **HIGH** | Keep the split exactly as planned. |
## Alternatives Considered
| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| **Celery + Redis** | **Temporal** (`temporalio` 1.28.x Python) | Temporal is *genuinely better* for the day-scale `aguardando_consulta_whatsapp → human gate → whatsapp_in_progress → re-score` workflow: durable timers (wait days with zero worker cost), exactly-once activity semantics, built-in human-in-the-loop signals, and a workflow-replay UI. **Defer, don't adopt now**: it adds a Temporal server/cluster to ops, a steeper learning curve, and rewrites your orchestration model. Adopt in a later milestone *if and only if* the WhatsApp outreach state machine proves painful in Celery (lost progress on restarts, manual retry plumbing, hard-to-audit long waits). For the foundational milestone, Celery+Redis ships faster and the latency tolerance covers its weaknesses. |
| **pgvector (HNSW)** | Dedicated vector DB (Qdrant/Milvus) | Only if dedup vectors exceed ~5–10M with high write churn *and* pgvector p99 degrades. Not expected at all-Brazil attraction/destination scale (hundreds of thousands, not tens of millions). Keeping vectors in Postgres preserves transactional dedup. |
| **instructor Mode.Tools** | Raw OpenRouter `response_format: json_schema` | OpenRouter does expose structured-output `response_format`, but support varies per underlying provider and isn't uniformly enforced for DeepSeek routes. `instructor` + Mode.Tools + Pydantic retry is more portable across the DeepSeek/Sonnet split and gives you the mandated 2nd-layer validation for free. |
| **Twilio (launch BSP)** | **Meta Cloud API direct** | Meta Cloud is cheaper at scale (~$0.005/msg Twilio markup avoided) and gives full template/rate-tier control — but is more maintenance-intensive (own webhook infra, compliance, rate-tier management). Recommended **end-state**; start on Twilio to ship, then migrate behind the WhatsApp client interface once volume justifies. |
| **n8n thin transport** | All-in-LangGraph (no n8n) | n8n gives ready-made WhatsApp Cloud nodes but is **not unit-testable** in your offline suite. Keep n8n strictly as dumb transport; **all conversation logic, opt-out, and extraction live in LangGraph code** so the suite stays 100% offline. If the only n8n value is the WhatsApp node, consider dropping n8n entirely and calling the BSP API from a typed client — simpler test story, one less moving part. |
| **psycopg 3** | asyncpg | asyncpg is faster but psycopg3 covers sync (Celery workers) + async (FastAPI) uniformly and has the cleaner pgvector + SQLAlchemy 2.0 story. |
| **google-maps-places (New)** | legacy `googlemaps` 4.10 | The legacy `googlemaps` client targets the **deprecated** Places API. Use the New client for `business_status`, `weekday_text`, `reviews[].publishTime` fields the SignalAgent needs. |
## What NOT to Use
| Avoid | Why | Use Instead |
|-------|-----|-------------|
| **psycopg2** | Maintenance mode; no async; clumsier pgvector path | **psycopg 3** |
| **Legacy `googlemaps` client / Places API (legacy)** | Google is sunsetting legacy Places; your required signal fields (`business_status`, `reviews[].publishTime`, `weekday_text`) are on **Places API (New)** | **google-maps-places** (New) |
| **Naive `response_format={"type":"json_object"}` for DeepSeek without validation** | DeepSeek JSON-mode can emit schema-valid-but-wrong or truncated JSON; this is exactly the §B.6 risk | **instructor Mode.Tools** + Pydantic retry |
| **n8n holding conversation logic** | Breaks the 100%-offline test mandate; logic in n8n is un-unit-testable | LangGraph code; n8n = transport only |
| **Default file-based Celery beat** | Single-file schedule = single point of failure, awkward in multi-worker 24/7 | **celery-redbeat** (Redis-backed) |
| **Adopting Temporal in the foundational milestone** | Real ops + learning cost before the workflow pain is proven | Celery+Redis now; revisit Temporal as a scoped upgrade |
| **IVFFlat as default pgvector index** | Recall drifts as data changes; needs rebuilds; you have active writes (continuous ingest) | **HNSW** (better recall under writes, less tuning) |
| **Putting any external API on norteia-api's path** | Violates the architecture boundary | All external calls in the collector only |
## Integration Gotchas (downstream-consumer priorities)
## Version Compatibility
| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| `langgraph` 1.2.x | `langchain-core` 1.4.x | Both on the 1.x GA line — install together; pin both. |
| `instructor` 1.15.x | `openai` 2.x, `anthropic` 0.10x, `pydantic` 2.x | instructor wraps both SDKs; all on current majors. Verify instructor's `from_provider("deepseek/...")` resolves to OpenRouter base URL or set base_url manually. |
| `pgvector` (Python) 0.4.x | `pgvector` server extension 0.8.x, `sqlalchemy` 2.0.x, `psycopg` 3.x | Server extension version ≥ 0.5 required for HNSW. Confirm your Postgres image ships pgvector ≥ 0.8. |
| `celery` 5.6.x | `redis` 8.0 client, `celery-redbeat` 2.3.x | Redis client 8.x is fine as broker lib; ensure server is Redis 7+. |
| `psycopg` 3.3.x | Python 3.12/3.13 | Confirm binary wheels for your Python minor before pinning 3.13. |
| `next` 16.x | Node 22, Bun 1.3.x | Bun as PM/test runner; Node 22 as runtime target. |
| `vitest` 4.x | `msw` 2.x | Both current majors; standard pairing. |
## Sources
- PyPI version index (`pip index versions`) — fastapi 0.136.3, celery 5.6.3, redis 8.0.0, langgraph 1.2.4, langchain-core 1.4.6, openai 2.41.1, anthropic 0.109.1, instructor 1.15.1, pydantic 2.13.4, pgvector 0.4.2, psycopg 3.3.4, sqlalchemy 2.0.50, alembic 1.18.4, respx 0.23.1, vcrpy 8.1.1, pytest 9.0.3, twilio 9.10.9, apify-client 3.0.2, temporalio 1.28.0, google-maps-places 0.9.0, celery-redbeat 2.3.3, tenacity 9.1.4, structlog 26.1.0, pydantic-settings 2.14.1, fakeredis 2.36.1 — **HIGH**
- npm (`npm view`) — next 16.2.9, vitest 4.1.8, msw 2.14.6, @anthropic-ai/sdk 0.104.1, temporalio (JS) 1.9.3, n8n 2.25.7; local: node v22.22.3, bun 1.3.13 — **HIGH**
- Context7 `/fastapi/fastapi` (versions list incl. 0.128.0) — **HIGH**
- python.useinstructor.com/integrations/deepseek/ — DeepSeek default **Mode.Tools**, supports function calling; MD_JSON for reasoning models — **HIGH**
- python.useinstructor.com/integrations/openrouter/ — instructor↔OpenRouter integration confirmed — **HIGH**
- openrouter.ai (DeepSeek collection, tool-calling models) — DeepSeek V4 Flash/Pro on OpenRouter, tool-calling translation caveats — **MEDIUM**
- pgvector HNSW vs IVFFlat analyses (GitHub pgvector, BigData Boutique, Instaclustr) — HNSW default for active-write workloads <~10M vectors — **MEDIUM/HIGH**
- WhatsApp BSP pricing (Twilio docs, respond.io, whapi.cloud) — Twilio +$0.005/msg, Meta Cloud cheaper but maintenance-heavy, 2025 per-template billing + 24h utility-free window, rate-tier ~80 msg/s start — **MEDIUM**
- Celery vs Temporal comparisons (dasroot.net, pydantic.dev durable-execution, temporal.io long-running) — Temporal better for durable timers/human-in-loop; Celery lighter — **MEDIUM/HIGH**
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

### Celery — Single-Queue Model (established 260630-mb4)

All Celery tasks (beat-dispatched and `.delay()`-dispatched) land on the **default `celery`
queue**. There are no task_routes, no custom Queue() objects, and no -Q flag on the worker.

`task_default_queue = "celery"` is set explicitly in `brave/tasks/celery_app.py` for
documentation and drift prevention. Beat schedule entries must NOT carry `options.queue`.

**Worker start (local / ops):**
```
celery -A brave.tasks.celery_app:app worker --loglevel=info
```

**Beat start (local / ops):**
```
celery -A brave.tasks.beat_schedule beat --loglevel=info
```
Note: beat must target `brave.tasks.beat_schedule` (not `celery_app`) so importing it executes
the BRAVE_BEAT_SCHEDULE builder and registers entries into the RedBeatScheduler. If beat is
already running when this configuration deploys, restart the beat service — redbeat persists
the schedule (including options) in Redis and only resyncs entry definitions on process restart.

**Dedicated lanes deferred:** task_routes + separate worker pools are deferred until
head-of-line-blocking is observed (long sweeps starving outreach/push under a single worker pool).
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
