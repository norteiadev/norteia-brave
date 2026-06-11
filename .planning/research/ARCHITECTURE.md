# Architecture Research

**Domain:** 24/7 entity-agnostic ETL + reliability-scoring engine (Nascente → Rio → Mar/DLQ), multi-lane LLM collection + WhatsApp outreach, Next.js ops dashboard, push-only canonical contract to a separate Laravel API
**Researched:** 2026-06-11
**Confidence:** HIGH (design is documented in `docs/PLANO-BRAVE.md` §B and `.planning/PROJECT.md`; external patterns verified against current ETL medallion and LangGraph/Celery orchestration practice)

---

## Standard Architecture

This is a **medallion ETL pipeline** (Bronze/Silver/Gold = Nascente/Rio/Mar) with a **reliability-score gate** routing records to publish (Mar), human review (DLQ), or discard (descarte). The entity-agnostic core is reused across lanes (Destinos, Atrativos, future entities). Collection lanes are pluggable producers feeding the same core. A separate dashboard and a separate Laravel consumer sit at the edges.

### System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                       norteia-brave (Python)  =  PIPELINE BRAVE            │
│                                                                            │
│  ┌─────────────── COLLECTION LANES (pluggable producers) ──────────────┐   │
│  │  Destinos:  MturSeedIngest · NotebookLMIngest · DesmembramentoAgent │   │
│  │  Atrativos: DiscoveryAgent · ContactFinderAgent · SignalAgent ·     │   │
│  │             WhatsAppAgent  (LangGraph sub-state machine)            │   │
│  └──────────────────────────────┬─────────────────────────────────────┘   │
│                                 │ writes raw payloads                      │
│                                 ▼                                          │
│  ┌──────────── ENTITY-AGNOSTIC BRAVE CORE (reused by all lanes) ────────┐  │
│  │                                                                       │  │
│  │   NASCENTE ──▶  RIO  ──score §7.6──▶  ┌─ ≥85% ──────▶  MAR ──push──▶  │  │
│  │   (raw, JSONB  (dedup/normalize/      ├─ 51–84.9% ──▶  DLQ (human)    │  │
│  │    versioned)   label/score)          └─ ≤50% ───────▶  DESCARTE      │  │
│  │                                                          │            │  │
│  │   Score engine §7.6 (calibrable weights)  ◀── reprocess─┘ (DLQ→Rio)   │  │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                            │
│  ┌── PLATFORM SERVICES ──┐  ┌── NETWORK BOUNDARY (client interfaces) ──┐    │
│  │ FastAPI (REST+webhook)│  │ PlacesClient · OTAClient · ApifyClient · │    │
│  │ Celery+Redis (24/7)   │  │ WhatsAppClient · MturClient ·            │    │
│  │ Observability schema   │  │ NotebookLMClient · LLMClient ·          │    │
│  │ (llm_generations,etc) │  │ NorteiaApiClient                        │    │
│  └───────────────────────┘  └──────────────────────────────────────────┘   │
└─────────────┬──────────────────────────────────────────────┬──────────────┘
              │ REST (Bearer)                                  │ POST Mar (idempotent)
              ▼                                                ▼  ◀── error-report webhook
   ┌─────────────────────┐                          ┌────────────────────────┐
   │ Dashboard (Next.js) │                          │  norteia-api (Laravel) │
   │ monitor·DLQ·gate·    │                          │  destinations/         │
   │ funnels·cost         │                          │  attractions (Mar only)│
   └─────────────────────┘                          └────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| **Nascente** | Store raw, immutable, source-tagged, versioned payloads from any lane/entity. Append-only. | One `nascente_records` table; `payload JSONB`, `source`, `entity_type`, `version`, `ingested_at`, `content_hash` |
| **Rio** | Explode payloads → dedup (exact hash + fuzzy/pgvector) → normalize (names/coords/addresses) → label (Norteia taxonomy) → score §7.6. Deterministic rules + DeepSeek NLP. | `rio_records` working table + `processing_state` column; pgvector embeddings; per-record `score_breakdown JSONB` |
| **Mar** | Canonical, publishable, versioned records (score ≥85% or DLQ-approved). Pushed to norteia-api. Supports invalidation/update. | `mar_records` table; `source_ref` unique; `provenance JSONB`; `published_at`; supersession chain |
| **DLQ** | Hold 51–84.9% records for human review (approve/reject/edit/reprocess). | Not a separate table — a `routing = dlq` value on `rio_records`; dashboard reads/mutates it |
| **Descarte** | Terminal state for ≤50% records (or CLOSED_* business_status). Retained for audit, not reprocessed by default. | `routing = descarte` on `rio_records` |
| **Score engine §7.6** | Pure function: origem 30% · completude 20% · corroboração 20% · atualidade 15% · validação humana 15% → score + per-criterion breakdown. Weights from config. Same engine for all entities. | Pure Python module, zero I/O, fully unit-testable; weights injected |
| **Collection lanes** | Produce raw payloads into Nascente; Atrativos lane also owns a per-record sub-state machine before/around Rio. | LangGraph graphs (agents) + Celery tasks (scheduling/fan-out) |
| **Network boundary clients** | Wrap every external API behind an interface so the whole suite runs 100% offline. | Protocol/ABC per client + fake impl for tests (respx/VCR for HTTP) |
| **FastAPI surface** | Inbound webhooks (WhatsApp/email/error-report) + REST for dashboard + lane ingest triggers. | FastAPI app; thin controllers delegating to core/services |
| **Celery + Redis** | 24/7 orchestration, beat schedules, fan-out by UF, retries/backoff. | Celery workers + beat; Redis broker/result backend |
| **Observability schema** | `llm_generations` (cost/tokens), per-layer Brave metrics, queue/worker stats, WhatsApp quality, audit log. | Own DB tables, exposed via FastAPI for the dashboard |
| **Dashboard (Next.js)** | Territorial CMS: Brave monitor, DLQ review (batch-by-state), WhatsApp gate, conversations, funnels, cost/LLM views. | Next.js + Bun, Bearer-header auth, consumes FastAPI REST |
| **norteia-api (external)** | Consume only Mar (idempotent upsert), serve UI + RAG, emit error-report webhook. | Separate Laravel repo; contract verified by Pact |

---

## Recommended Project Structure

```
norteia-brave/
├── brave/
│   ├── core/                      # ENTITY-AGNOSTIC — depends on nothing lane-specific
│   │   ├── nascente/              # raw ingest API (store_raw, versioning, hashing)
│   │   ├── rio/                   # dedup, normalize, label, route
│   │   │   ├── dedup.py           #   exact hash + pgvector fuzzy
│   │   │   ├── normalize.py       #   names/coords/addresses
│   │   │   └── routing.py         #   score → Mar / DLQ / descarte thresholds
│   │   ├── mar/                   # publish/invalidate/supersede + push orchestration
│   │   ├── score/                 # §7.6 PURE engine (no I/O) + config-driven weights
│   │   └── models.py              # SQLAlchemy models for the 3 layers + audit
│   │
│   ├── lanes/                     # PLUGGABLE PRODUCERS — depend on core, not each other
│   │   ├── base.py               #   Lane / Producer protocol → writes to Nascente
│   │   ├── destinos/
│   │   │   ├── mtur_seed.py
│   │   │   ├── notebooklm.py
│   │   │   └── desmembramento_agent.py    # LangGraph + DeepSeek
│   │   └── atrativos/
│   │       ├── state_machine.py  #   sub-states: discovered → … → re-score
│   │       ├── discovery_agent.py
│   │       ├── contact_finder_agent.py
│   │       ├── signal_agent.py
│   │       └── whatsapp_agent.py #   LangGraph (Sonnet ask / DeepSeek extract)
│   │
│   ├── clients/                   # NETWORK BOUNDARY — one interface per external system
│   │   ├── base.py               #   Protocols
│   │   ├── places.py / ota.py / apify.py / whatsapp.py
│   │   ├── mtur.py / notebooklm.py / llm.py
│   │   └── norteia_api.py        #   push Mar (idempotent) + Pact consumer
│   │
│   ├── observability/            # llm_generations, metrics, cost guard, audit
│   ├── api/                      # FastAPI app: routers (webhooks, dashboard REST, ingest)
│   ├── tasks/                    # Celery tasks + beat schedules (fan-out by UF)
│   └── config/                   # settings, score weights, pinned LLM slugs
│
├── tests/
│   ├── unit/                     # score engine, normalize, routing (pure, fast)
│   ├── fakes/                    # fake clients implementing clients/base protocols
│   ├── integration/              # docker-compose Postgres+Redis, respx/VCR
│   └── contract/                 # Pact consumer tests against norteia-api
│
├── docker-compose.yml            # Postgres (pgvector) + Redis for local/CI
└── dashboard/                    # Next.js + Bun (Node 22), Vitest + MSW
```

### Structure Rationale

- **`core/` is entity-agnostic and lane-agnostic** — it must compile and pass tests with zero references to "destino" or "atrativo". Lanes import core; core never imports lanes. This is the single most important boundary: it is what lets future entities (experiência, evento, rota) plug in without touching the engine.
- **`lanes/` are independent producers** — Destinos and Atrativos do not import each other. The only coupling is *data*: Atrativos resolves a parent destino that already exists in Mar (a query, not a code dependency).
- **`clients/` is the network boundary** — every external system is reachable only through an interface here. Tests inject fakes; production injects real impls. This is what makes "100% offline suite" achievable and CI keyless.
- **`score/` has no I/O** — keeping the §7.6 engine a pure function makes the gate exhaustively unit-testable (cases → Mar/DLQ/descarte) and weights calibrable from config without redeploys.

---

## Architectural Patterns

### Pattern 1: Hybrid layer storage — table-per-layer + state column (NOT one or the other)

**What:** Use a **separate physical table per medallion layer** (`nascente_records`, `rio_records`, `mar_records`) AND a `processing_state` / `routing` column **within Rio** for the in-flight lifecycle. Nascente is append-only and immutable; Rio is the mutable working area; Mar is the published canonical store.

**When to use:** When the three layers have genuinely different shapes, lifecycles, and access patterns — which they do here (raw JSONB blobs vs. normalized scored working rows vs. canonical published rows). The dashboard queries DLQ from Rio, the pusher queries unpublished from Mar; mixing them in one table with only a state column makes every query carry a state filter and couples the immutable raw store to the mutable lifecycle.

**Trade-offs:**
- (+) Clear ownership/immutability per layer; Nascente stays append-only (good for audit/replay); independent indexing (pgvector only on Rio).
- (+) DLQ and descarte are *just routing values on Rio* — no extra tables, dashboard mutates in place, reprocess = reset routing + re-run Rio.
- (−) A record's full history spans three tables (mitigated by `nascente_id` / `rio_id` foreign keys forming a lineage chain).
- Reject "single table + state column for everything" (couples immutable raw to mutable lifecycle, query-filter tax) and "table-per-state including DLQ/descarte tables" (state churn becomes row moves across tables = transactional pain).

**Example:**
```python
# core/models.py (sketch)
class NascenteRecord(Base):                 # append-only
    id; entity_type; source; source_origem  # §7.6 origem weight by source
    payload: JSONB; content_hash; version; ingested_at

class RioRecord(Base):                      # mutable working area
    id; nascente_id (FK)
    entity_type; routing: Enum(mar, dlq, descarte, in_progress)
    normalized: JSONB; embedding: Vector    # pgvector dedup
    score: Numeric; score_breakdown: JSONB  # per-criterion §7.6
    sub_state: str | None                   # atrativos sub-state machine
    canonical_key: str                      # dedup/idempotency anchor

class MarRecord(Base):                      # published canonical
    id; rio_id (FK); entity_type
    source_ref: str (UNIQUE)                # idempotency key for push
    canonical: JSONB; provenance: JSONB; reliability_score
    parent_mar_id: FK | None                # atrativo → destino
    published_at; superseded_by: FK | None  # versioning via supersession
```

### Pattern 2: Versioning by supersession (immutable append + pointer), not in-place mutation

**What:** Nascente never overwrites — a new payload for the same source is a new `version` row. Mar updates create a *new* `MarRecord` and set `superseded_by` on the old one (or bump a `version` and keep history). The "current" Mar record is the head of the chain. Invalidation sets `visibility`/supersession rather than deleting.

**When to use:** Always, for any record that crosses the contract to norteia-api or feeds RAG. Auditability ("why was this published, from what source, scored how?") and safe error-report reopen depend on never losing prior state.

**Trade-offs:**
- (+) Full lineage Nascente→Rio→Mar; error-report can reopen a specific version into Rio/DLQ; replay possible.
- (+) Idempotent push: `source_ref` unique means re-pushing the same canonical record is a no-op upsert.
- (−) Storage grows; needs a retention/compaction policy for descarte and old versions (defer; not a milestone concern).

### Pattern 3: Sub-state machine = explicit state column + Celery tasks, with LangGraph *inside* agent nodes

**What:** The Atrativos lifecycle (`discovered → contacts_found → signals_gathered → (Rio score) → aguardando_consulta_whatsapp → whatsapp_in_progress → re-score`) is modeled as an **explicit `sub_state` column on the Rio record**, advanced by **Celery tasks** (each task does one transition and persists the new state). The **WhatsApp conversation itself** (multi-turn, adaptive) is a **LangGraph graph** running *within* the `whatsapp_in_progress` step. The human WhatsApp gate is a hard stop: the record sits in `aguardando_consulta_whatsapp` until the dashboard flips it.

**When to use:** When transitions are long-running (day-scale outreach latency), tolerate failure/retry, and include a human gate — exactly this case. A DB-persisted state column survives worker restarts and is directly queryable by the dashboard (the gate and funnels are just `WHERE sub_state = ...` queries).

**Trade-offs:**
- (+) Durable across restarts (state in Postgres, not in a worker's memory); dashboard reads state directly; Celery retries give at-least-once advancement.
- (+) LangGraph confined to where it shines (cyclic, adaptive multi-turn conversation) — verified current guidance: LangGraph checkpointers persist *between* nodes only, so wrapping the whole multi-day machine in one LangGraph run would be fragile; Celery-as-durable-executor + LangGraph-as-conversation-brain is the recommended hybrid.
- (−) Two mechanisms to reason about (state column + graph). Mitigate by a single `advance(record)` dispatcher mapping `sub_state → task`.
- Reject a full durable-workflow engine (Temporal) for the milestone: the plan explicitly allows it *only if* durable workflows later justify it; Celery+Redis suffices because outreach tolerates day-scale latency.

### Pattern 4: Deterministic Rio rules vs. LLM extraction — separation of concerns

**What:** Rio's **dedup, normalization, routing, and the score arithmetic are 100% deterministic** (pure code, no LLM). DeepSeek is used only for *extraction/structuring* (raw text → schema) and *desmembramento* (listing sub-destinos), always behind a mandatory Pydantic+`instructor` second-layer validator. Conversational WhatsApp uses Sonnet. The score §7.6 is never an LLM judgment.

**When to use:** Always. Putting scoring or routing inside an LLM makes the gate non-reproducible and untestable. Keeping LLM at the edges (extraction in, conversation out) keeps the core deterministic and the offline test suite meaningful.

**Trade-offs:** (+) Reproducible gate, cheap exhaustive unit tests, calibrable weights. (−) Normalization rules must be maintained in code (acceptable; it is the system's value).

---

## Data Flow

### Forward flow (a record's journey)

```
[Lane producer]  (Mtur / NotebookLM / Places / desmembramento)
      │  store_raw(payload, source, entity_type)
      ▼
NASCENTE  ── append-only, versioned, content-hashed
      │  Celery: rio.process(nascente_id)
      ▼
RIO  ──▶ dedup (hash → pgvector fuzzy)
      ──▶ normalize (name/coords/address)
      ──▶ label (taxonomy)
      ──▶ score §7.6 → score + breakdown
      │
      ├── score ≥85% ─────────────────▶ MAR ──push──▶ norteia-api  (idempotent by source_ref)
      ├── 51–84.9% ──▶ routing=dlq ───▶ DASHBOARD (human: approve/edit → Mar | reject → descarte | reprocess → Rio)
      └── ≤50% ──────▶ routing=descarte (terminal, retained for audit)
```

### Atrativos sub-flow (overlaps Rio)

```
DiscoveryAgent → sub_state=discovered (parent destino resolved from Mar)
   → ContactFinderAgent → contacts_found
   → SignalAgent → signals_gathered   (CLOSED_* → descarte immediately)
   → RIO score §7.6
       ├ ≥85% → Mar
       ├ borderline → sub_state=aguardando_consulta_whatsapp ── HUMAN GATE (dashboard)
       │     → approved → whatsapp_in_progress (LangGraph: Sonnet asks / DeepSeek extracts)
       │     → owner validation boosts validação-humana → RE-SCORE → Mar/DLQ
       └ ≤50% → descarte
```

### Reverse flow (error-report reopen)

```
norteia-api "reportar erro" → POST /webhook (FastAPI)
   → locate MarRecord by source_ref
   → set norteia-api visibility=hidden/flagged (platform stops serving it)
   → create/reset RioRecord (routing=dlq) from the Mar lineage
   → record sits in DLQ for human re-review → re-publish or descarte
```

### Key Data Flows

1. **Idempotent publish:** Mar → `NorteiaApiClient.push()` → `POST /api/internal/territorial/{destinations|attractions}`, keyed by `source_ref` (unique). Re-push is an upsert no-op. Atrativo carries resolved `destination_id`. Verified by Pact consumer contract.
2. **Cost-guarded LLM call:** any agent → `LLMClient` → write `llm_generations` row (tokens, USD) → cost guard checks budget → DeepSeek (`:nitro`, pinned slug) or Sonnet. Guard can halt a lane on budget breach.
3. **Dashboard read path:** Next.js → FastAPI REST → reads Rio (DLQ/gate queues, `WHERE routing/sub_state`), observability tables (metrics/cost/audit). All mutations (approve/reject/reprocess/gate-approve) go back through FastAPI.

---

## Build Order (with dependency rationale)

The plan's GSD trilhas map cleanly to a dependency-ordered build. **Core before lanes; Destinos before Atrativos; dashboard parallel; contract external but must stabilize early.**

| Order | Track | Depends on | Why this order |
|-------|-------|-----------|----------------|
| **1** | **Brave core** (Nascente/Rio/Mar/DLQ + **score engine §7.6** + FastAPI skeleton + Celery/Redis + client interfaces + observability schema) | nothing | Everything routes through the score gate; nothing can be validated until the engine + layers + client-boundary exist. Build score engine first (pure, testable), then layers, then orchestration. |
| **1b** | **Ingestion contract + Pact** (NorteiaApiClient + Mar push shape, idempotent by `source_ref`) | core models | Stabilize early so norteia-api (Trilha 5, other repo) and the lanes can both rely on a fixed Mar shape. Cheap to build, expensive to change later — front-load it. |
| **2** | **Destinos lane** (MturSeedIngest, NotebookLMIngest, DesmembramentoAgent → Rio/score → DLQ batch-by-state → Mar) | core (1) | Destinos populate Mar with the simpler validation model (no WhatsApp/PII). Proves the full Nascente→Rio→DLQ→Mar→push path end to end on real data. **Must precede Atrativos** because an atrativo references a parent destino that must already exist in Mar. |
| **3** | **Atrativos lane** (Discovery → ContactFinder → Signal → WhatsApp gate → WhatsAppAgent → re-score → Mar/DLQ) | Destinos in Mar (2), core (1) | Adds the sub-state machine, LangGraph conversation, WhatsApp transport, and PII/LGPD compliance. Hardest, riskiest, most external dependencies — build last so the gate, push, and DLQ are already proven. |
| **4** | **Dashboard** (monitor, DLQ batch review, WhatsApp gate, conversations, funnels, cost) | FastAPI REST from each track | **Parallel to 1–3.** Each panel can be built as its backing data exists: monitor/DLQ as soon as core (1) emits; WhatsApp gate/conversations as Atrativos (3) lands. Start the shell early, fill panels as tracks complete. |
| **5** | **norteia-api consumer** (separate Laravel repo) | stable contract (1b) | External to this repo. Only the *contract* matters here (Pact). Build/verify against the consumer once 1b is frozen. |

**Critical path:** 1 → 1b → 2 → 3. Dashboard (4) shadows it; norteia-api (5) gated only on 1b.

---

## Scaling Considerations

| Scale | Architecture adjustments |
|-------|--------------------------|
| Cold start, few UFs (BA/RJ/SP/SC/CE/PE first) | Single Postgres + single Redis + a few Celery workers. Beat fans out per-UF tasks. Defer everything else. |
| All-BR steady 24/7 | Partition/route work by UF (already the fan-out key). Separate Celery queues per lane (destinos vs atrativos vs whatsapp) so slow WhatsApp/Apify work can't starve fast ingest. pgvector index tuning (HNSW) for dedup as Rio grows. Cost guard becomes load-bearing (budget per lane/day). |
| Heavy outreach + many sources | Rate-limit per external client (Places/Apify/WhatsApp BSP ramp) at the client boundary. Consider read replicas for the dashboard's heavy aggregate funnels. Reconsider Temporal only if multi-day workflows need stronger durability/visibility than Celery gives. |

### Scaling Priorities

1. **First bottleneck: external API rate limits + cost** (Places, Apify, WhatsApp BSP ramp, DeepSeek spend) — handled at the client boundary (rate-limit/backoff) and by the cost guard. This bites before DB does.
2. **Second bottleneck: Rio dedup at scale** (pgvector similarity over a growing corpus) — fix with HNSW indexing and UF-scoped candidate sets (dedup within a UF/parent first, not globally).
3. **Third: WhatsApp queue throughput vs. ban risk** — intentionally human-gated and ramped; throughput is deliberately capped by compliance, not engineering.

---

## Anti-Patterns

### Anti-Pattern 1: Putting the score/routing decision inside an LLM
**What people do:** Ask DeepSeek to "decide if this is canonical" or to emit the §7.6 score.
**Why it's wrong:** Non-reproducible gate, untestable, drifts with model changes, defeats the calibrable-weights requirement.
**Do this instead:** LLM only *extracts/structures*; the §7.6 score and Mar/DLQ/descarte routing are pure deterministic code with config weights.

### Anti-Pattern 2: One mega-table with a single `state` column for all of Nascente/Rio/Mar
**What people do:** Collapse the three layers into one table differentiated only by a status enum.
**Why it's wrong:** Couples the immutable raw store to the mutable lifecycle; every query carries a state filter; can't independently index (pgvector belongs only to Rio); audit/replay of raw becomes risky.
**Do this instead:** Table-per-layer for the three medallion stores; a `routing`/`sub_state` column *within Rio* for in-flight lifecycle (DLQ/descarte are routing values, not tables).

### Anti-Pattern 3: Lanes importing each other (Atrativos depends on Destinos code)
**What people do:** Make the Atrativos lane import Destinos modules to resolve parents.
**Why it's wrong:** Couples lanes, breaks the "future entities plug in" goal, makes either lane impossible to test in isolation.
**Do this instead:** Lanes share only *data* through the core (Atrativos *queries* Mar for the parent destino). No lane-to-lane code imports.

### Anti-Pattern 4: Wrapping the whole multi-day Atrativos lifecycle in a single LangGraph run
**What people do:** Model `discovered → … → re-score` (including the multi-day human WhatsApp gate) as one long LangGraph graph.
**Why it's wrong:** LangGraph checkpoints persist between nodes, not across worker restarts/days reliably; a human gate that pauses for days inside a graph run is fragile.
**Do this instead:** Explicit `sub_state` column advanced by Celery tasks for the macro lifecycle; LangGraph only for the bounded multi-turn WhatsApp *conversation* inside one step.

### Anti-Pattern 5: Calling external APIs directly from agents/Rio (no client boundary)
**What people do:** `requests.get(places_url)` inline in an agent.
**Why it's wrong:** Breaks the 100%-offline test mandate, leaks ToS/rate-limit/cost concerns everywhere, makes CI need keys.
**Do this instead:** Every external system behind a `clients/` interface; inject fakes in tests, real impls in prod; rate-limit/cost/ToS handled once per client.

---

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Google Places (New) Details | `PlacesClient` interface; persist `place_id` | ToS: canonical must be first-party validated; call only in collector. `business_status` CLOSED_* → descarte; `reviews[].publishTime ≤30d` → atualidade signal |
| OTA (Viator/GYG/Booking) | `OTAClient`; optional price cross-check | Ticketed only; partner onboarding gated; best-effort |
| Apify (IG/X scrape) | `ApifyClient`; LLM-filtered | Best-effort; Meta ToS gray; Places is the fallback signal; read-only (no automated DM) |
| WhatsApp Business API (Twilio/Meta Cloud) | `WhatsAppClient` + **n8n thin transport**; LangGraph holds the logic | Approved templates, 24h window, human gate + ramp, opt-out, consent log (LGPD/BSP) |
| Mtur / NotebookLM | `MturClient` / `NotebookLMClient` | Seed ingest sources; origem weights 100 / 80 |
| DeepSeek via OpenRouter / Sonnet | `LLMClient` | DeepSeek `:nitro`, pinned slug + fallback, `data_collection: deny`, mandatory Pydantic+`instructor` validator; Sonnet for conversation. Every call logged to `llm_generations` + cost guard |
| norteia-api | `NorteiaApiClient` (Pact consumer) | Push only Mar, idempotent by `source_ref`; inbound error-report webhook reopens to Rio/DLQ |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| lanes ↔ core | direct function calls (lanes import core) | One-directional: core never imports lanes |
| Atrativos ↔ Destinos | data only (query Mar for parent) | No code coupling between lanes |
| core/agents ↔ external world | via `clients/` interfaces only | The hard testability boundary |
| dashboard ↔ collector | FastAPI REST (Bearer header) | Dashboard never touches DB directly |
| collector ↔ norteia-api | HTTP push (Mar) + inbound webhook (error-report) | Only canonical Mar crosses; verified by Pact |
| Celery tasks ↔ DB state | persist `sub_state`/`routing` per transition | Durable lifecycle; dashboard queries the same columns |

---

## Sources

- `docs/PLANO-BRAVE.md` §B.1–B.8, §C, "Sequência de fases GSD" — primary architecture and contract (HIGH)
- `.planning/PROJECT.md` — requirements, boundaries, key decisions (HIGH)
- [dbt Labs — common data pipeline architecture patterns](https://www.getdbt.com/blog/common-data-pipeline-architecture-patterns) — medallion Bronze/Silver/Gold = Nascente/Rio/Mar confirmation (MEDIUM)
- [Matillion — ETL architecture & design patterns](https://www.matillion.com/blog/etl-architecture-design-patterns-modern-data-pipelines) — staging vs canonical layer separation (MEDIUM)
- [LangGraph vs Temporal for AI agents (Medium / Data Science Collective)](https://medium.com/data-science-collective/langgraph-vs-temporal-for-ai-agents-durable-execution-architecture-beyond-for-loops-a1f640d35f02) — checkpoints persist between nodes only; durability boundary (MEDIUM)
- [Orchestrating AI tasks: Celery vs Temporal (dasroot.net)](https://dasroot.net/posts/2026/02/orchestrating-ai-tasks-celery-temporal/) — Celery-as-durable-executor + LangGraph-as-brain hybrid (MEDIUM)
- [LangGraph (official)](https://www.langchain.com/langgraph) — stateful cyclic agent graphs, when appropriate (MEDIUM)

---
*Architecture research for: entity-agnostic ETL + reliability-scoring engine with LLM collection lanes*
*Researched: 2026-06-11*
