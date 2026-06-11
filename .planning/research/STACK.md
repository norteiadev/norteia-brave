# Stack Research

**Domain:** 24/7 Python data-collection + reliability-scoring ETL pipeline (LLM extraction agents, embedding dedup, WhatsApp outreach, Next.js ops dashboard)
**Researched:** 2026-06-11
**Confidence:** HIGH (versions pinned from PyPI/npm; integration caveats verified against official docs)

> **Verdict:** The stack in PLANO-BRAVE §B.5/§B.6 is **sound and validated** for 2026. Keep it. There are **five concrete adjustments** (none structural): (1) DeepSeek V4 now exists on OpenRouter — the doc's "may not exist" hedge is resolved; (2) use `instructor` **Mode.Tools** (DeepSeek supports native function calling) with a JSON-mode fallback, not naive JSON-mode; (3) **HNSW** is the right pgvector index default, not IVFFlat; (4) **Meta Cloud API direct** is the better long-run BSP, with **Twilio as the launch BSP** to de-risk; (5) keep **Celery+Redis** for the milestone — Temporal is genuinely better for the day-scale outreach state machine but is a deferrable upgrade, not a launch requirement.

---

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
| **pydantic-settings** | 2.14.x | 12-factor config (slugs, weights, keys, flags) | Always — centralizes the pinned DeepSeek slug, calibrable §7.6 weights, and the test opt-in flags. |
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

---

## Installation

```bash
# Core (collector)
pip install "fastapi[standard]==0.136.*" "uvicorn[standard]==0.49.*" \
  "pydantic==2.13.*" "pydantic-settings==2.14.*" \
  "celery==5.6.*" "redis==8.0.*" "celery-redbeat==2.3.*" \
  "sqlalchemy==2.0.*" "psycopg[binary]==3.3.*" "pgvector==0.4.*" "alembic==1.18.*" \
  "langgraph==1.2.*" "langchain-core==1.4.*" \
  "openai==2.41.*" "anthropic==0.109.*" "instructor==1.15.*" \
  "httpx==0.28.*" "tenacity==9.1.*" "structlog==26.*" \
  "google-maps-places==0.9.*" "apify-client==3.0.*" "twilio==9.10.*"

# Dev dependencies (collector)
pip install "pytest==9.0.*" "respx==0.23.*" "vcrpy==8.1.*" "fakeredis==2.36.*" \
  "pact-python" "ruff" "pyright" "celery-types==0.26.*" "flower==2.0.*"

# Dashboard
bun add next@16 react react-dom
bun add -d vitest@4 msw@2
```

> Pin exact versions in `pyproject.toml`/lockfile for the build; the `.*` above is for first install. Pin the **DeepSeek slug** in `pydantic-settings` config, not code.

---

## LLM provider decision (resolves PLANO §B.6 open question)

| Item | Finding | Action |
|------|---------|--------|
| DeepSeek slug existence | As of mid-2026, **DeepSeek V4 Flash/Pro exist on OpenRouter** (1M context), alongside the V3.x `deepseek/deepseek-chat` family. The doc's "`deepseek-v4-flash` may not exist → fallback" hedge is now resolved — V4 is real. **MEDIUM** (model lineup moves fast). | Pin a **primary slug + ordered fallback list** in config. Suggested: primary `deepseek/deepseek-chat` (stable, broadly available), evaluate `deepseek/deepseek-v3.2` / V4 as throughput/quality upgrades. Validate each slug's tool-calling support before promoting. |
| `:nitro` throughput variant | `:nitro` is OpenRouter's throughput-optimized routing suffix — correct for batch/backend (latency-insensitive) extraction. **MEDIUM** | Keep `:nitro` for backend extraction/scoring/desmembramento. Do NOT use it where you need a specific provider's tool-calling fidelity (see gotcha). |
| "Paid ≠ won't train" | Correct concern. Set `provider.data_collection: "deny"` in the OpenRouter request body **and** the account setting. **HIGH** | Enforce in the client wrapper; assert it in a unit test. |
| WhatsApp conversation model | Claude Sonnet 4.5 via **native Anthropic SDK** (not OpenRouter) for conversation quality + direct quota/streaming control. **HIGH** | Keep the split exactly as planned. |

---

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

---

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

---

## Integration Gotchas (downstream-consumer priorities)

1. **OpenRouter + instructor + DeepSeek JSON reliability.**
   Use `instructor` with **Mode.Tools** (DeepSeek `deepseek-chat` supports native function calling; instructor's `from_provider` defaults to Tools for DeepSeek). Caveat: **`:nitro` and provider routing can land you on a backend whose tool-calling translation through OpenRouter is imperfect** — OpenRouter itself warns some models are "incompatible for tool calling/response format." Mitigation: (a) keep instructor's Pydantic-retry on (re-asks with validation errors), (b) configure a **fallback Mode** (JSON/MD_JSON) per slug, (c) add a contract test that the pinned slug actually returns valid tool calls, (d) the mandated **2nd-layer Pydantic validator** stays even after instructor — belt and suspenders, exactly as the doc specifies. **HIGH** confidence on approach.

2. **Celery vs Temporal for day-scale durable outreach.**
   The outreach state machine *wants* durable timers + human-signal resumption — Temporal's wheelhouse. But it tolerates day-scale latency and the volume is human-gated/ramped, so Celery's weaknesses (manual retry plumbing, progress loss on restart) are survivable. **Decision: Celery now, behind a clean orchestration interface so a Temporal swap is a later, contained milestone.** Persist sub-state in Postgres (not only in the broker) so a worker restart never loses an in-flight conversation — this single discipline closes most of Celery's durability gap. **HIGH.**

3. **pgvector dedup at all-Brazil scale.**
   Use **HNSW** (`vector_cosine_ops` or `vector_l2_ops` matching your embedding). Expected corpus = hundreds of thousands of destinations/attractions, not tens of millions → HNSW gives high recall with minimal tuning under continuous writes. Pre-filter candidates with cheap blocking (same UF/município, name trigram) **before** the vector compare to shrink the search and cut false merges (Trancoso ≠ Porto Seguro sede). Combine exact-hash dedup first, vector dedup second. Re-evaluate a dedicated vector DB only past ~5–10M vectors with heavy churn (not expected). **HIGH.**

4. **WhatsApp BSP — Twilio vs Meta Cloud.**
   **Launch on Twilio** (managed infra, faster to first message, +$0.005/msg over Meta template fees), **migrate to Meta Cloud API direct** when volume justifies the engineering (own webhook infra, rate-tier management starting ~80 msg/s, template-classifier risk). Put the BSP behind your WhatsApp client interface so the swap is internal. Note 2025 pricing: per-template-message billing; utility messages free in the 24h service window — your opt-out/identification template is a utility/transactional template, keep it free of any promotional sentence or it gets reclassified as marketing. **MEDIUM** (BSP pricing/policy shifts often — re-verify at build time).

5. **n8n-thin vs all-in-LangGraph testability.**
   Keep **zero logic in n8n**. Every decision (who to contact, opt-out handling, multi-turn questions, DeepSeek extraction of existe?/funcionando?/horários/valor) lives in LangGraph nodes so `pytest` covers it offline with a fake LLM and a fixtured WhatsApp webhook. n8n (if kept) only relays HTTP. Seriously consider **dropping n8n** and calling the BSP from a typed httpx client — it removes an un-testable hop and simplifies the offline suite. **HIGH.**

---

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

---

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

---
*Stack research for: 24/7 Python ETL + LLM extraction + WhatsApp outreach pipeline*
*Researched: 2026-06-11*
