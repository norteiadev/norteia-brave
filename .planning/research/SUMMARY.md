# Project Research Summary

**Project:** norteia-brave — Pipeline Brave (Collector)
**Domain:** 24/7 entity-agnostic ETL + reliability-scoring pipeline (Nascente→Rio→Mar/DLQ) with LLM collection lanes + WhatsApp owner-verification + Next.js ops dashboard
**Researched:** 2026-06-11
**Confidence:** HIGH

## Executive Summary

Brave is a **medallion ETL pipeline** (Bronze/Silver/Gold = Nascente/Rio/Mar) with a **multi-criteria reliability-score gate** (§7.6) that routes records to publish (Mar ≥85%), human review (DLQ 51–84.9%), or discard (descarte ≤50%). The way experts build this is well-established: an entity-agnostic core reused across pluggable collection lanes, a *pure deterministic* score engine (LLMs only extract/converse — they never score or route), table-per-medallion-layer storage with a routing/sub-state column inside the mutable Rio layer, and every external system hidden behind a client interface so the whole test suite runs 100% offline and keyless. The recommended stack (FastAPI · Celery+Redis · LangGraph · Pydantic+`instructor` · PostgreSQL with pgvector · DeepSeek-via-OpenRouter for extraction · Claude Sonnet for conversation · Next.js dashboard) is **validated and sound for 2026** — keep it. Five non-structural adjustments emerged: DeepSeek V4 now exists (resolve the doc's hedge with a pinned slug + fallback chain), use `instructor` Mode.Tools, default pgvector to **HNSW** (not IVFFlat), launch WhatsApp on **Twilio** then migrate to Meta Cloud, and keep Celery (Temporal is a deferrable upgrade, not a launch need).

The build order is dictated by hard dependencies: **Core → Ingestion contract (Pact) → Destinos → Atrativos**, with the Dashboard built in parallel as backing data appears. The score engine and client boundary must come first (everything routes through the gate; nothing is testable without the seam). Destinos must populate Mar before Atrativos, because an atrativo resolves a parent destino that must already be canonical. Atrativos is the hardest, riskiest lane (sub-state machine + WhatsApp + PII/LGPD) and ships last so the gate, push, and DLQ are already proven.

The dominant risks are **operational and compliance**, not technological. The single largest threat is the **DLQ becoming a landfill**: at cold-start almost every record lacks human validation and corroboration, so the §7.6 score distribution collapses into the DLQ band and buries reviewers — silently degrading "DLQ as gate" into "approve everything, slower." Mitigate by simulating the score histogram *before* wiring intake, designing batch-by-state review from day one, and auto-promoting high-origem corroborated records. The other critical risks: **WhatsApp number bans** (human gate + volume ramp + quality-driven auto-pause are load-bearing, not nice-to-haves), **LGPD consent/opt-out** modeled as a hard send-path gate (must land before the first real message), **embedding-dedup false-merges** (Trancoso ≠ Porto Seguro — always block by territorial key first), **DeepSeek hallucinated/malformed extractions** (validate-or-quarantine on every path + origem=40 firewall), and **cost blowups** during the all-BR cold start (the USD guard must be an enforcing circuit breaker, not a chart).

## Key Findings

### Recommended Stack

The PLANO-BRAVE stack is validated for 2026 with versions pinned from PyPI/npm and integration caveats verified against official docs (full detail in `STACK.md`). Single Postgres serves three roles (JSONB raw store, relational Brave state, pgvector dedup) avoiding a second datastore. The LLM split is confirmed: DeepSeek (paid, OpenRouter) for batch extraction/scoring/desmembramento; Claude Sonnet 4.5 (native Anthropic SDK) for PT-BR WhatsApp conversation.

**Core technologies:**
- **FastAPI 0.136 + Uvicorn** — async-native API for webhooks + dashboard REST + lane ingest; Pydantic-native; OpenAPI for the Next.js client
- **Celery 5.6 + Redis 7/8 + celery-redbeat** — 24/7 orchestration, beat scheduling, fan-out by UF; Redis doubles as cost-guard/rate-limit counter store
- **PostgreSQL 16/17 + pgvector 0.8 (HNSW)** — JSONB Nascente + relational state + transactional embedding dedup in one DB
- **LangGraph 1.2** — agent orchestration; confined to the bounded multi-turn WhatsApp conversation (NOT the multi-day macro lifecycle)
- **Pydantic 2.13 + instructor 1.15 (Mode.Tools)** — structured LLM output + the mandatory 2nd-layer validation that compensates for DeepSeek's looser schema adherence
- **OpenAI SDK (→ OpenRouter/DeepSeek) + Anthropic SDK (→ Sonnet)** — the LLM split
- **psycopg 3 + SQLAlchemy 2.0 + Alembic** — driver/ORM/migrations (psycopg 3, not 2; async + clean pgvector path)
- **Next.js 16 + Bun 1.3 + Vitest + MSW** (dashboard) — territorial CMS, Bearer auth, offline component tests

**What to avoid:** psycopg2, legacy `googlemaps`/Places-legacy, naive DeepSeek JSON-mode without validation, n8n holding any logic, file-based Celery beat, IVFFlat as default, Temporal in the foundational milestone.

### Expected Features

This is **not generic CRUD** — its two audiences are the pipeline itself and the operators/stewards working the dashboard (full detail in `FEATURES.md`). "Table stakes" means *the pipeline or operators fail without it*; a pipeline that publishes wrong records is worse than one that publishes nothing.

**Must have (table stakes):**
- Nascente (raw, source-tagged, versioned, append-only JSONB) + Rio (explode→dedup→normalize→label→score) + Mar (canonical, idempotent push) + DLQ (durable, monitorable, actionable)
- Score engine §7.6 — calibrable weights, one engine both entities, pure function
- Three-way routing (Mar/DLQ/descarte) + reprocess/re-score (idempotent) + error classification (transient retry vs permanent route)
- External clients behind interfaces (unblocks the 100%-offline keyless suite — build FIRST, cheap if first / expensive to retrofit)
- 24/7 Celery+Redis fan-out by UF; observability (`llm_generations` + enforcing USD cost guard + per-layer metrics + audit logs)
- Destinos lane: Mtur seed + NotebookLM + DesmembramentoAgent → DLQ → batch-by-state human validation → Mar
- Atrativos lane: Discovery → ContactFinder → Signal → WhatsApp gate (human) → WhatsAppAgent → re-score
- Dashboard: DLQ review (batch-by-state, per-criterion score) + Brave monitor + WhatsApp gate + Cost/LLM view + Bearer auth
- LGPD + WhatsApp BSP compliance (legal precondition for any real outreach)

**Should have (differentiators — the moat vs "just scrape and dump"):**
- Multi-criteria calibrable §7.6 score as a first-class gate (the moat)
- Owner-validation via WhatsApp outreach (first-party confirmation at scale — no scraper does this)
- LLM "desmembramento" (município → real tourist destinos; Trancoso ≠ Porto Seguro sede)
- Freshness signal from review recency (≤30d ⇒ funcionando), two-stage dedup, provenance-rich auditable golden records
- Community error-report → reopen loop (self-healing dataset), batch-by-state steward workflow

**Defer (v2+):** active freshness-decay cron, OTA price cross-check, auto-tuned §7.6 weights, additional lanes/entities, ML/learning-to-rank matcher, Temporal. **Deliberately NOT built:** human-approves-every-record, hosting Brave inside norteia-api, automated IG/FB DM, real-time/streaming, multi-tenant/i18n dashboard.

### Architecture Approach

Medallion ETL with a reliability-score gate. The **entity-agnostic core** (Nascente/Rio/Mar/score) is reused by **pluggable lanes** (Destinos, Atrativos, future entities) that depend on core but never on each other — Atrativos shares only *data* with Destinos (queries Mar for parent), never code. Every external system sits behind a `clients/` interface (the hard testability boundary). Key patterns: **table-per-medallion-layer + routing/sub_state column inside Rio** (DLQ/descarte are routing values, not tables); **versioning by supersession** (immutable append + pointer, never in-place mutation); **Celery-as-durable-executor advancing an explicit `sub_state` column, LangGraph-as-conversation-brain inside one step** (NOT one multi-day LangGraph run); **deterministic Rio/score, LLM only at the edges** (extraction in, conversation out — never scoring/routing). Full detail in `ARCHITECTURE.md`.

**Major components:**
1. **Brave core** — Nascente (append-only raw), Rio (mutable working area: dedup/normalize/label/route), Mar (published canonical), Score engine §7.6 (pure, zero-I/O, config weights)
2. **Collection lanes** — Destinos producers (Mtur/NotebookLM/Desmembramento) + Atrativos sub-state machine (Discovery/ContactFinder/Signal/WhatsApp)
3. **Network boundary clients** — one interface per external system (Places/OTA/Apify/WhatsApp/Mtur/NotebookLM/LLM/NorteiaApi) with fakes for tests
4. **Platform services** — FastAPI (webhooks + REST + ingest), Celery+Redis (24/7), observability schema
5. **Dashboard (Next.js)** — territorial CMS consuming FastAPI REST (never touches DB directly)
6. **norteia-api (external Laravel)** — consumes only Mar (idempotent by `source_ref`); contract verified by Pact

### Critical Pitfalls

Top risks from `PITFALLS.md` (13 total). The dominant theme: the hard problems are operational/compliance, not stack choice.

1. **DLQ landfill** — cold-start scores collapse into the 51–84.9% band, burying reviewers; gate silently degrades to approve-everything. *Avoid:* simulate the score histogram before intake; batch-by-state review unit = "a município's desmembramento" not "a row"; auto-promote high-origem corroborated records; monitor a DLQ drain-rate SLO and pause *intake* (not reviewers) when intake > drain.
2. **Score calibration drift + threshold gaming** — weighted-sum ≠ probability; "approve" auto-setting validação humana=100 makes the score mean "a human clicked." *Avoid:* version the score config + stamp `score_version`; golden-set per state re-scored on changes; decouple "reviewed" from "validated" (capture *what* was verified); per-reviewer approval-rate metric.
3. **Embedding-dedup failures at all-BR scale** — false merges (Trancoso into Porto Seguro; homonym municípios across UFs) + missed dups (HNSW is *approximate by design*). *Avoid:* never dedup on name-embedding alone — block by UF + município (+ parent) first; treat distrito-vs-município as hierarchy not dedup; measure pgvector recall at chosen `ef_search`; cache embeddings.
4. **DeepSeek schema weakness + hallucinated destinos** — malformed JSON slips through lenient paths; invented praias score into DLQ and get rubber-stamped into Mar. *Avoid:* mandatory `instructor`/Pydantic validate-or-quarantine on *every* LLM path (impossible code path otherwise); ground the desmembramento prompt with known localities; origem=40 firewall (no LLM-only destino reaches Mar without human/second-source corroboration).
5. **WhatsApp bans/throttling/template rejection** — cold numbers blasting unsolicited messages get quality Green→Yellow→Red→suspended (250/24h portfolio-shared cap). *Avoid:* human gate + hard-capped volume ramp; honestly-categorized utility template with identification + opt-out; quality-rating as first-class metric → auto-throttle on Yellow, auto-pause on Red; 24h-window-aware FSM; backup number/portfolio.
6. **LGPD consent/opt-out gaps** (PII lane) — messaging opted-out contacts, no consent/legal-basis log, no retention/minimization. *Avoid:* model consent/opt-out as a hard send-path gate (enforced in code, tested offline) + consent log at first contact + retention policy — **must land before the first real WhatsApp message**.

Further critical pitfalls (detail in `PITFALLS.md`): #7 Celery 24/7 operational (idempotency keys, poison-quarantine *distinct from* review-DLQ, single-beat, row locks); #8 cost blowups (enforcing circuit-breaker guard); #9 Places ToS (persist `place_id` only, not raw Google content); #11 offline-test/Pact drift (no-real-network CI guard + blocking Pact); #12/#13 OpenRouter slug instability + paid≠private (pinned slug + resolved-provider logging + `data_collection: deny` + provider allow-list).

## Implications for Roadmap

Research strongly converges on a **dependency-ordered build**: Core → Contract → Destinos → Atrativos, with Dashboard shadowing in parallel. The same ordering appears independently in STACK, FEATURES dependency graph, ARCHITECTURE build order, and PITFALLS phase mapping (all use the T1–T5 trilha map). This is high-confidence.

### Phase 1: Brave Core + Network Boundary + Observability
**Rationale:** Everything routes through the score gate; nothing is validatable until the engine + three layers + client boundary exist. The client boundary and offline test seam are cheap-if-first / expensive-to-retrofit. Most critical pitfalls (3, 4, 7, 8, 9, 11, 12, 13) have their *prevention* anchored here.
**Delivers:** Nascente/Rio/Mar/DLQ + **pure score engine §7.6 with a distribution-simulation harness** + FastAPI skeleton + Celery/Redis (idempotency keys, poison-quarantine, single-beat, locks) + all `clients/` interfaces with fakes + observability (`llm_generations`, enforcing USD cost guard, metrics, audit) + no-real-network CI guard.
**Addresses:** Score engine, Nascente/Rio/Mar routing, two-stage dedup (territorial-key blocking + measured recall + cached embeddings), idempotent reprocess, external-clients-behind-interfaces, observability, offline suite.
**Avoids:** DLQ landfill (sim harness + calibrable bands), dedup false-merge (blocking), validate-or-quarantine, Celery operational, cost blowup, Places ToS persistence boundary, offline-discipline erosion, slug instability, paid≠private.
**Uses:** FastAPI, Celery+Redis, PostgreSQL+pgvector(HNSW), psycopg3/SQLAlchemy/Alembic, Pydantic+instructor, structlog, pytest+respx+fakeredis.

### Phase 1b: Ingestion Contract (Pact) — Mar push shape
**Rationale:** Stabilize early (cheap to build, expensive to change) so norteia-api (other repo) and the lanes both rely on a frozen, idempotent-by-`source_ref` Mar shape. Front-loaded to de-risk the cross-repo boundary.
**Delivers:** `NorteiaApiClient` + Mar push shape + Pact consumer contract (versioned, single source of truth).
**Implements:** collector↔norteia-api boundary; idempotent publish data flow.
**Avoids:** Pact drift (pitfall 11).

### Phase 2: Destinos Lane
**Rationale:** Proves the full Nascente→Rio→DLQ→Mar→push path end-to-end on real data with the *simpler* validation model (no WhatsApp/PII). **Must precede Atrativos** — an atrativo references a parent destino that must already be in Mar.
**Delivers:** MturSeedIngest (origem=100) + NotebookLMIngest (origem=80) + **DesmembramentoAgent** (DeepSeek, origem=40, grounded prompt + mandatory validator) → Rio/score → DLQ → batch-by-state human validation (BA/RJ/SP/SC/CE/PE first) → Mar.
**Addresses:** Destinos table-stakes features + the desmembramento differentiator.
**Avoids:** DLQ landfill origin (flood starts here), hallucination firewall (origem=40 can't reach Mar unaided), hierarchy-aware dedup, single-state cold-start cost dry run.

### Phase 3: Atrativos Lane (+ WhatsApp + Compliance)
**Rationale:** Hardest, riskiest, most external dependencies (sub-state machine, LangGraph conversation, WhatsApp transport, PII/LGPD). Build last so the gate, push, and DLQ are already proven on Destinos.
**Delivers:** Atrativos sub-state machine (Celery-advanced `sub_state` + LangGraph conversation inside the WhatsApp step) — Discovery → ContactFinder → Signal (best-effort/non-blocking Apify+OTA) → WhatsApp gate (human) → WhatsAppAgent (Sonnet asks / DeepSeek extracts) → re-score. **Compliance lands here and before the first real message.**
**Addresses:** owner-validation differentiator, freshness signal, Atrativos table-stakes.
**Avoids:** WhatsApp bans (window-aware FSM, ramp, auto-pause), LGPD gaps (send-path suppression gate + consent log), gray-source dependence (non-blocking Apify/OTA, no automated DM), FSM concurrency races.

### Phase 4: Dashboard (Territorial CMS) — parallel to 1–3
**Rationale:** Each panel can be built as its backing FastAPI surface exists. Start the shell early; fill panels as tracks land. Several pitfalls' *verification* lives here.
**Delivers:** Brave monitor + DLQ review (batch-by-state, per-criterion score, structured "what did you verify") + WhatsApp gate UI (with quality rating + ramp/cap) + conversations + funnels + Cost/LLM view + Bearer auth.
**Addresses:** dashboard table-stakes + batch-by-state steward differentiator.
**Avoids:** DLQ-landfill (batch UI), gaming (verification capture + per-reviewer approval rate), gate over-approval (quality/ramp context).
**Uses:** Next.js 16, Bun, Vitest, MSW.

### Phase 5: norteia-api Consumer (external Laravel — out of this repo)
**Rationale:** External; only the *contract* matters here. Gated solely on Phase 1b being frozen. Verify against the consumer once the Pact is stable.

### Phase Ordering Rationale
- **Dependencies discovered:** score gate is the hub (must exist + be unit-tested before any lane); client boundary unblocks the entire offline suite; Destinos→Mar is a hard precondition for Atrativos' parent resolution; ContactFinder precedes WhatsAppAgent; human WhatsApp gate is a deliberate hard stop.
- **Architecture grouping:** core/ is entity-agnostic and lane-agnostic — building it complete and standalone is what lets future entities plug in. Lanes are independent producers, so Destinos and Atrativos are cleanly separable phases.
- **Pitfall avoidance:** front-loading the score-distribution sim, dedup blocking, idempotency, cost guard, and client boundary into Phase 1 prevents the most expensive-to-recover pitfalls (dedup false-merges, DLQ landfill, cost blowup are HIGH recovery cost). Compliance is gated to precede the first real WhatsApp message.

### Research Flags

Phases likely needing deeper research during planning (`/gsd:plan-phase --research-phase`):
- **Phase 1 (score-distribution calibration):** the 50/85 boundaries and §7.6 weights are MEDIUM-confidence (domain reasoning, not project-measured). The distribution-simulation harness needs real source-type sampling — flag for calibration research during planning.
- **Phase 3 (WhatsApp BSP):** BSP pricing/policy and messaging-limit tiers shift often (MEDIUM) — re-verify Twilio-vs-Meta-Cloud, template categorization, and current rate caps at build time. Also the Celery-as-durable-executor + LangGraph hybrid for the multi-day FSM warrants a focused design pass.

Phases with standard, well-documented patterns (can likely skip research-phase):
- **Phase 1b (Pact contract):** standard consumer-driven contract testing; shape is already specified.
- **Phase 2 (Destinos ingest):** Mtur/NotebookLM are mostly structured loaders; DesmembramentoAgent reuses the Phase-1 validate-or-quarantine LLM client.
- **Phase 4 (Dashboard):** mirrors `norteia-frontend`; established Next.js/Bun/Vitest/MSW patterns.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Versions pinned from PyPI/npm; integration caveats verified against official docs (instructor/DeepSeek, OpenRouter, pgvector HNSW). Model-slug lineup is MEDIUM (moves fast — mitigated by pinned slug + fallback). |
| Features | HIGH | Project fully specified in PROJECT.md/PLANO-BRAVE.md; domain patterns corroborated by current MDM/entity-resolution/DLQ/enrichment literature. |
| Architecture | HIGH | Design documented in PLANO §B + PROJECT.md; external patterns (medallion ETL, LangGraph/Celery hybrid durability boundary) verified against current practice. |
| Pitfalls | HIGH (external) / MEDIUM (calibration & Celery ops) | WhatsApp BSP, Places ToS, OpenRouter behavior, pgvector recall verified against official docs. Score-calibration and Celery operational specifics are domain reasoning, not yet project-measured. |

**Overall confidence:** HIGH

### Gaps to Address
- **Score-distribution at cold start (MEDIUM):** the 50/85 band boundaries and §7.6 weights are unvalidated against real intake. *Handle:* ship Phase 1 with a histogram-simulation harness; treat boundaries as tunable knobs; calibrate on the first state before national fan-out.
- **WhatsApp BSP pricing/policy/limits (MEDIUM):** shift frequently. *Handle:* re-verify Twilio-vs-Meta-Cloud, template categories, and rate caps at Phase 3 build time; keep BSP behind the client interface so the launch→Meta-Cloud migration is internal.
- **DeepSeek model-slug lineup (MEDIUM):** V4 exists but availability/tool-calling fidelity per slug varies. *Handle:* pinned slug + ordered fallback in config; deploy-time probe; log resolved provider/model to `llm_generations`.
- **Celery durability for the multi-day outreach FSM (MEDIUM):** Celery now, behind a clean orchestration interface; persist sub-state in Postgres so restarts never lose progress. *Handle:* revisit Temporal only if FSM pain proves real (deferred upgrade, not launch).

## Sources

### Primary (HIGH confidence)
- `.planning/PROJECT.md` + `docs/PLANO-BRAVE.md` §B.1–B.8/§C/§7.6 — requirements, boundaries, contract, score weights
- PyPI/npm version indexes — all stack versions pinned (fastapi 0.136, celery 5.6, langgraph 1.2, openai 2.41, anthropic 0.109, instructor 1.15, pgvector 0.4/0.8, next 16, vitest 4)
- python.useinstructor.com (DeepSeek Mode.Tools, OpenRouter integration) — structured-output approach
- Google Places API (New) Policies + Place IDs + Maps service terms — `place_id`-only caching boundary
- Meta/WhatsApp Business Platform docs — 250/24h portfolio cap, quality rating, template review, Red→suspension
- OpenRouter docs (Nitro variant, provider routing, data_collection) — slug/variant behavior
- pgvector README + Neon optimization — HNSW approximate, `ef_search` recall knob

### Secondary (MEDIUM confidence)
- MDM/entity-resolution/DLQ/enrichment literature (Profisee, Tamr, Senzing, Data Ladder, dataskew.io, OneUptime) — golden-record/steward/idempotency patterns
- pgvector HNSW-vs-IVFFlat analyses; Celery-vs-Temporal comparisons (dasroot.net, temporal.io, pydantic.dev) — durable-executor + LangGraph hybrid
- WhatsApp BSP pricing (Twilio docs, respond.io, whapi.cloud) — Twilio +$0.005/msg, per-template billing
- dbt Labs / Matillion — medallion architecture confirmation
- LangGraph-vs-Temporal durability-boundary (Data Science Collective) — checkpoints persist between nodes only

### Tertiary (LOW confidence)
- Score-calibration / DLQ-landfill / Celery-24/7 / cold-start-cost pitfalls — domain reasoning over §7.6 weights + general engineering practice; not yet project-measured (validate during Phase 1 calibration)

---
*Research completed: 2026-06-11*
*Ready for roadmap: yes*
