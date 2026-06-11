# Phase 1: Brave Core, Score Gate, Boundary & Contract - Context

**Gathered:** 2026-06-11
**Status:** Ready for planning

> Captured in `--auto` mode: gray areas auto-resolved with the research-backed recommended option for each. Every decision below is a default downstream agents may refine during research/planning — none is a hard user lock except where it restates a PROJECT.md Key Decision.

<domain>
## Phase Boundary

Deliver the **entity-agnostic Brave engine**: a raw payload flows Nascente → Rio → Mar/DLQ/descarte through a pure, calibrable §7.6 score gate; every external system sits behind a faked client interface; Celery+Redis runs it 24/7; observability (`llm_generations`, USD cost guard, per-layer metrics, audit) is exposed via FastAPI; and the Mar→norteia-api push contract is frozen and verified by a Pact consumer test — all validated by a 100%-offline, keyless suite.

**In scope:** Nascente store, Rio processing (dedup/normalize/label), §7.6 score engine, three-way routing, Mar store + idempotent push, DLQ, reprocess/re-score, error classification, Celery orchestration, client boundary + fakes, FastAPI surface, observability, Pact contract, error-report webhook, offline test harness. (Requirements CORE-01..12, SCORE-01..03, OBS-01..04, CNTR-01..02, TEST-01, TEST-03.)

**Out of scope (other phases):** any lane producer (Mtur/NotebookLM/Desmembramento/Discovery/Contact/Signal/WhatsApp), the dashboard UI, real external API calls, the Laravel-side ingestion endpoint (separate repo — only the Pact contract lives here).
</domain>

<decisions>
## Implementation Decisions

### Data model & layer storage
- **D-01:** Table-per-layer for the three medallion stores (Nascente / Rio / Mar), NOT a single mega-table with a state column. The immutable raw store must not be coupled to the mutable lifecycle. (ARCHITECTURE.md)
- **D-02:** DLQ and descarte are **routing values** (`routing` / `sub_state` column) *within Rio*, not separate tables.
- **D-03:** Versioning by **supersession** — append a new row + `superseded_by` pointer; never mutate-in-place. Required for auditability, idempotent push, and safe error-report reopen.
- **D-04:** Nascente row carries at least: `source`, `source_ref`, `entity_type`, `uf`, `payload (JSONB)`, `content_hash`, `ingested_at`, `version`. Immutable.

### Orchestration & durability
- **D-05:** Celery + Redis with **celery-redbeat** (not file-based beat) for single-source-of-truth scheduling; fan-out by UF. Tasks idempotent; poison messages quarantined; one beat instance.
- **D-06:** Put orchestration behind an interface so a future **Temporal** swap stays contained. Temporal is NOT adopted this milestone (outreach tolerates day-scale latency — that risk lives in Phase 3, not here).

### Dedup strategy
- **D-07:** Two-stage dedup: **exact `content_hash` blocking → fuzzy via pgvector**. Block first by **territorial key (UF + município)** so homonym municípios in different UFs can never merge and a child destino never collapses into its parent município. (PITFALLS.md)
- **D-08:** pgvector index = **HNSW** (active-write workload), not IVFFlat. Recall at the chosen `ef_search` must be **measured**, not assumed — "no similar vector" ≠ "no duplicate".

### LLM client & structured output
- **D-09:** LLM access behind a `clients/` interface with a fake. Use **`instructor` with `Mode.Tools`** (DeepSeek supports native function calling), not naive JSON-mode.
- **D-10:** **Pin the DeepSeek slug + ordered fallback list** in `pydantic-settings` (primary `deepseek/deepseek-chat`; fallbacks resolved/validated before promotion). Log the **resolved provider** per call (`:nitro` optimizes throughput, can silently reroute). `provider.data_collection: deny`.
- **D-11:** Every LLM output passes a mandatory **validate-or-quarantine** second layer (Pydantic). Malformed → quarantine, never silently dropped. (No real LLM call in Phase 1 except via fake; the seam + validator are what ship here.)

### Score engine & calibration
- **D-12:** §7.6 score is a **pure, zero-I/O function** over a normalized record; weights (origem 30 / completude 20 / corroboração 20 / atualidade 15 / validação humana 15) and Mar/DLQ/descarte thresholds live in **config** (pydantic-settings), not code constants.
- **D-13:** Each score stamps the `score_version` (weight-set identity) so re-scores are comparable and reprocessing is idempotent.
- **D-14:** Ship a **score-distribution / histogram simulation harness** as the first verification gate — cold-start records mathematically tend to collapse into the 51–84.9 DLQ band ("DLQ landfill" risk). Treat the 50/85 boundaries as tunable; calibrate on one state before national fan-out. (PITFALLS.md, STATE.md blocker)

### Mar push & Pact contract
- **D-15:** Mar→norteia-api push is **idempotent by `source_ref`** (re-push = no-op upsert) and carries **full per-criterion provenance/lineage**.
- **D-16:** Freeze the push JSON shape early via a **consumer-driven Pact** test (this repo is the consumer/producer-of-data; the Laravel API is the provider). The contract is cheap-early / expensive-late and both lanes + the external repo depend on its stability.
- **D-17:** Persist of Google `place_id` is allowed as cache; **canonical data is always the first-party validated record** (enforced architectural boundary — relevant to the contract shape even though Places calls land in Phase 3). (PITFALLS.md, COMP-03)

### Project layout & boundaries
- **D-18:** Three load-bearing package boundaries: `core/` is **entity-agnostic** (lanes import core, never the reverse); lanes share only **data** through Mar (no lane-to-lane code coupling); every external system sits behind a `clients/` interface — the single testability seam. (ARCHITECTURE.md)
- **D-19:** Postgres driver = **psycopg 3** (not psycopg2); Places client (Phase 3) will use **google-maps-places (New)**, flagged now so the client interface is shaped for the New API fields.

### Observability & cost
- **D-20:** `llm_generations` table records every LLM call (lane, model, resolved provider, USD cost). A **USD cost guard** checks the ceiling **before** dispatch and halts/throttles on breach (enforcing, not advisory).
- **D-21:** Per-layer Brave metrics + queue/worker health + audit log exposed via FastAPI REST (the dashboard in Phase 4 consumes this, never the DB directly).

### Claude's Discretion
- Exact table/column DDL, migration tool (Alembic assumed), FastAPI router layout, Celery queue topology, and test-fixture structure are left to research/planning — decisions above set direction, not schema.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Primary plan & framework
- `docs/PLANO-BRAVE.md` — the full plan; §B.1 Brave core, §B.5 stack, §B.6 LLM/DeepSeek cautions, §B.7 observability, §C testability, GSD trilhas. The authoritative source for this milestone.
- `docs/brave-visao-geral.pdf` — Brave overview (visual/conceptual companion to the plan).
- The §-numbers in the plan (§7, §7.6 score formula, §7.7–7.8 invalidation, §15.7 monitor/audit) cite `docs/Norteia_MVP_Documentacao_Tecnica_v1.md` — that MVP doc lives in the **norteia-api** repo, not here; treat the §7.6 weights/thresholds quoted in PLANO-BRAVE.md as canonical for this repo.

### Research (this project)
- `.planning/research/SUMMARY.md` — synthesized findings + suggested phasing.
- `.planning/research/STACK.md` — validated 2026 stack, version pins, instructor/Mode.Tools, HNSW, psycopg3, redbeat, BSP notes.
- `.planning/research/ARCHITECTURE.md` — medallion mapping, table-per-layer + sub_state, supersession versioning, build order, package boundaries.
- `.planning/research/PITFALLS.md` — DLQ-landfill, dedup false-merge, OpenRouter slug churn, cost guard, Places ToS, offline-CI guard (phase-mapped).
- `.planning/research/FEATURES.md` — table-stakes vs differentiators, dependency graph, MVP definition.

### Project planning
- `.planning/PROJECT.md` — Core Value, Key Decisions, constraints.
- `.planning/REQUIREMENTS.md` — Phase 1 requirements (CORE/SCORE/OBS/CNTR/TEST IDs).
- `.planning/ROADMAP.md` §"Phase 1" — goal + 5 success criteria.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — greenfield repo. Current contents: `docs/`, `.planning/`, `CLAUDE.md`, `README.md`, `.gitignore`. No Python package, no app code yet.

### Established Patterns
- None in-repo. The mirror repo `norteia-frontend` is cited in the plan as the Next.js+Bun reference for the Phase 4 dashboard (not relevant to Phase 1).

### Integration Points
- Outbound (this phase, behind a fake): the Mar→norteia-api push (`POST /api/internal/territorial/{destinations|attractions}`, service token, idempotent by `source_ref`) — shape frozen by Pact here, implemented in the Laravel repo later.
- Inbound (this phase): community error-report webhook → reopen a Mar record into Rio/DLQ.
</code_context>

<specifics>
## Specific Ideas

- The DLQ must be a **durable, monitorable, actionable queue** (records carry reason codes), not a log table no one works — explicit industry bar cited in FEATURES.md.
- The offline-test discipline is a **hard constraint**: no test hits any external by default; real = opt-in flag; CI keyless. The client boundary (D-18) exists primarily to make this possible, so build it first.
</specifics>

<deferred>
## Deferred Ideas

- **Active freshness-decay / re-score cron (§7.8)** — v2 (FRESH-01); the re-score *machinery* ships here, the scheduled decay does not.
- **Auto-tuning of §7.6 weights from steward decisions** — v2 (TUNE-01); Phase 1 only makes weights calibrable + versioned.
- **OTA price cross-check** — v2 (OTA-01); the OTA client interface may be stubbed but no integration.
- **Temporal durable-workflow engine** — only if a proven need emerges (Phase 3 outreach FSM is the trigger to re-evaluate, not Phase 1).

None of these are in Phase 1 scope — recorded so they aren't lost.
</deferred>

---

*Phase: 1-Brave Core, Score Gate, Boundary & Contract*
*Context gathered: 2026-06-11*
