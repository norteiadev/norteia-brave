# norteia-brave — Pipeline Brave (Collector)

## What This Is

`norteia-brave` is a standalone Python service that **is** the Norteia **Pipeline Brave** — a 24/7 data-collection and reliability-scoring engine that populates Norteia's territorial base (every Brazilian state, from a cold "carga inicial" start). It ingests raw territorial data from many sources, cleans/dedups/normalizes/scores it (framework §7.6), and only publishes high-confidence canonical records ("Mar", ≥85%) to the consuming `norteia-api` (Laravel). It ships with a Next.js operations dashboard that is the **territorial CMS**: Brave monitor, DLQ human-review queue, WhatsApp gate, funnels, and cost/LLM observability.

This milestone delivers the **entity-agnostic Brave core** plus its **first two collection lanes: Destinos (destinations) and Atrativos (attractions)**.

## Core Value

Only **validated, reliability-scored canonical records reach the platform** — the Brave pipeline (Nascente → Rio → Mar) with §7.6 scoring and a DLQ gate is the single thing that must work. Everything downstream (UI, AI assistants) depends on Mar being trustworthy.

## Requirements

### Validated

- ✓ **Brave core pipeline** (Nascente → Rio → Mar/DLQ/descarte) with the pure, calibrable §7.6 score gate, idempotent reprocess/re-score, and supersession versioning — Phase 1
- ✓ **Entity-agnostic medallion data model** (table-per-layer + routing/sub_state, partial-unique active source_ref, HNSW pgvector dedup) — Phase 1
- ✓ **Client boundary** — all 8 external systems behind Protocol interfaces + fakes; 100%-offline keyless suite (149 tests) — Phase 1
- ✓ **24/7 orchestration** (Celery + celery-redbeat, idempotent tasks, poison quarantine) — Phase 1
- ✓ **Observability** (`llm_generations`, enforcing USD cost guard, per-layer metrics, audit, FastAPI surface) — Phase 1
- ✓ **Frozen idempotent Mar→norteia-api Pact contract** + error-report webhook (shared-secret auth) — Phase 1
- ✓ **Destinos lane** — MturSeedIngest (origem=100, bundled seed CSV), NotebookLMIngest (origem=80, IBGE-match corroboração boost), DesmembramentoAgent (origem=40, instructor Mode.Tools + validate-or-quarantine), DLQ steward validate + batch-by-state → Mar → idempotent push to `destinations`; origem=40 firewall + threshold_dlq=40 calibration; 191 offline tests — Phase 2
- ✓ **Atrativos lane (WhatsApp + Compliance)** — DiscoveryAgent (Places sweep + DeepSeek map, parent-destino resolved from Mar, place_id cache only), ContactFinderAgent, SignalAgent (CLOSED_* hard descarte, reviews≤30d atualidade, Apify best-effort); persisted resumable `sub_state` FSM (Celery, idempotent, SELECT FOR UPDATE, audit on transition); human WhatsApp gate FastAPI router + Redis volume ramp (CR-04 atomic); LangGraph WhatsAppAgent (Sonnet PT-BR ask + DeepSeek extract, AsyncPostgresSaver persistence, n8n thin transport); owner-validation → re-score → `push_attraction`; hard code-enforced send-path LGPD+BSP gate (consent log, anchored opt-out, 24h window, approved templates, quality-rating auto-pause) — 87 offline unit + 20 integration tests; code review found+fixed 4 blockers + 9 warnings — Phase 3. **Human-UAT pending:** live Twilio send, concurrency stress, 24h-window time-trace.
- ✓ **Dashboard (Territorial CMS)** — Next.js 16 App Router (Bun, Node 22, Tailwind v4, shadcn/ui, TanStack Query, Recharts) operations dashboard consuming the FastAPI surface (never the DB directly) behind Bearer-header auth via a server-side BFF Route Handler (service secret never reaches the browser; either-or steward/Bearer guard on existing mutation endpoints). Six surfaces: DLQ review queue (§7.6 per-criterion ScoreBreakdownPanel, batch-by-state, edit→re-score via existing PATCH endpoints), Brave monitor (rates/throughput/alerts), WhatsApp gate UI (+ read-only ramp-context/quality endpoint), Cost & LLM (group-by lane/model over `llm_generations`), conversations (append-only `conversation_message` log at both pipeline write-points, masked PII) + funnels (by UF/source). Thin read-only D-01 aggregation endpoints added to `brave/api/routers/dashboard.py`; frozen Phase 1 core/Pact untouched. 82 offline Vitest+MSW + offline pytest; verification passed 6/6 — Phase 4.

### Active

**Brave core (entity-agnostic, reusable, multi-source):**
- [ ] Nascente: store raw, source-tagged, versioned (JSONB) payloads from any lane/entity
- [ ] Rio: explode payloads → dedup (exact hash + fuzzy/embedding), normalize (names/coords/addresses), label (Norteia taxonomy), score §7.6
- [ ] Score engine §7.6: origem 30% · completude 20% · corroboração 20% · atualidade 15% · validação humana 15% — weights calibrable via config; same engine for destino & atrativo
- [ ] Mar: canonical ≥85%, publishable, versioned, push to norteia-api; supports invalidation/update
- [ ] DLQ: 51–84.9% → human review; descarte/reprocess ≤50%
- [ ] FastAPI surface: webhooks (WhatsApp/email) + REST for dashboard + lane ingest
- [ ] 24/7 orchestration: Celery + Redis (beat); fan-out by UF
- [ ] External network boundaries behind client interfaces (Places/OTA/Apify/WhatsApp/Mtur/NotebookLM/NorteiaApi)
- [ ] Observability: own `llm_generations` table + USD cost guard + per-layer Brave metrics + queue/worker + WhatsApp quality rating + audit logs, exposed via FastAPI

**Lane: Destinos (precedes Atrativos):**
- [ ] MturSeedIngest: ingest categorized Mtur municipalities (Oferta Principal/Complementar/Apoio) → Nascente (source=mtur, origem=100), linked to municipality_id
- [ ] NotebookLMIngest: structured reports → Nascente (source=notebooklm, origem=80) for destinos not in Mtur
- [ ] DesmembramentoAgent §7.4: DeepSeek lists real destinos inside each Oferta Principal município (distritos/praias/vilas) with tourist name/type/positioning → Nascente flag "LLM-generated, pending validation" (origem=40)
- [ ] Rio + score for destinos → typically DLQ (lacking human validation)
- [ ] Human validation in DLQ, batch-by-state (BA/RJ/SP/SC/CE/PE first) → validação humana=100 → Mar → push to `destinations`

**Lane: Atrativos (depends on Destinos in Mar):**
- [ ] Sub-state machine: discovered → contacts_found → signals_gathered → (Rio score) → [borderline] aguardando_consulta_whatsapp → (human gate) → whatsapp_in_progress → re-score
- [ ] DiscoveryAgent: Google Places (UF/município sweep) + gov; DeepSeek → schema → Nascente; resolves parent destino (already in Mar)
- [ ] ContactFinderAgent: Places Details (phone/website/WhatsApp link) + site/IG-FB/email
- [ ] SignalAgent: Places business_status (CLOSED_* → descarte), weekday_text hours, reviews[].publishTime ≤30d ⇒ funcionando; IG/X via Apify (best-effort); OTA optional price cross-check (ticketed only)
- [ ] WhatsApp gate (human, dashboard): only borderline (<85% for lack of direct validation); human approves who to contact; volume ramp
- [ ] WhatsAppAgent (fully automated): WhatsApp Business API (Twilio/Meta Cloud), n8n thin transport + LangGraph logic; Sonnet asks PT-BR (identifies Norteia + opt-out); DeepSeek extracts existe?/funcionando?/horários/valor; owner-validation boosts score → re-score → Mar/DLQ

**Dashboard (Next.js, Bun, Node 22) — territorial CMS:**
- [ ] Brave monitor §15.7: volume per layer, approval/rejection/DLQ rates, failure alerts, throughput, audit
- [ ] DLQ queue: review (Nascente payload, Rio data, §7.6 score per criterion, signals, WhatsApp log) → approve/reject/edit/reprocess; batch-by-state mode
- [ ] WhatsApp gate UI: aguardando_consulta_whatsapp queue → approve/reject; ramp
- [ ] WhatsApp conversations view, funnels (destinos & atrativos by UF/source), Cost & LLM views

**Compliance & testability:**
- [ ] LGPD: legal basis + Norteia identification + opt-out + consent log + minimization (atrativos/WhatsApp lane)
- [ ] WhatsApp BSP: approved templates, 24h window, human gate + ramp, opt-out
- [ ] Full local test suite 100% offline (docker-compose: Postgres+Redis); externals opt-in by flag; CI keyless

### Out of Scope

- Hosting Brave inside norteia-api — by user decision, the whole engine lives in this Python repo; norteia-api only consumes Mar
- DLQ/Brave monitor in norteia-api Filament CMS (doc §15.7 original suggestion) — moved to this dashboard, now the territorial CMS
- norteia-api ingestion endpoints, migrations, webhook receiver (Trilha 5) — built in the separate Laravel repo; here only the ingestion **contract** (Pact) matters
- Future lanes (official-site scraping monitor, business CMS, UGC) and future entities (experiência, evento, temporada, rota) — core must support them, but not built this milestone
- Automated IG/FB DM — read-only signal only (Apify best-effort; Meta ToS gray)

## Context

- **Greenfield repo**, scaffolded; no Brave/Nascente/Rio/Mar/DLQ/score code exists yet. Canonical framework defined in `docs/Norteia_MVP_Documentacao_Tecnica_v1.md` §7 / §16, treated as platform prerequisite #1.
- **Two-repo architecture:** `norteia-brave` (this, Python) = the pipeline; `norteia-api` (Laravel) = consumer. Only Mar items cross the boundary via `POST /api/internal/territorial/{destinations|attractions}` (service token / Sanctum ability, idempotent by canonical key/source_ref). A community "reportar erro" webhook flows back to reopen records in Rio/DLQ.
- **LLM split:** backend (extraction/scoring/desmembramento) = **DeepSeek paid via OpenRouter** (`:nitro` throughput; slug pinned in config, fallback `deepseek/deepseek-chat` / `deepseek-v3.2`; `provider.data_collection: deny`; mandatory 2nd-layer Pydantic+`instructor` validator). Conversational (WhatsApp) = **Claude Sonnet 4.5**.
- **Dependency order:** Destinos must populate Mar before/with Atrativos (an atrativo belongs to a destino).
- All external APIs run only in the collector, never on norteia-api's hot path.

## Constraints

- **Tech stack (collector)**: Python — FastAPI · Celery+Redis · LangGraph · Pydantic+`instructor` · PostgreSQL (JSONB Nascente, pgvector for dedup). The CLAUDE.md "PHP fixed" lock governs only norteia-api, not this repo.
- **Tech stack (dashboard)**: Next.js + Bun (Node 22), Bearer-header auth, Vitest + MSW.
- **Execution**: continuous 24/7 service covering all BR states.
- **Gating**: §7.6 score + DLQ is the canonical gate — not human-approve-everything.
- **Testing**: no test hits Places/OTA/Apify/WhatsApp/OpenRouter/Anthropic/Mtur/norteia-api by default; real = opt-in flag; CI runs without keys. Logic lives in code (Brave core, score engine, desmembramento, conversation); n8n is thin transport. Contract with norteia-api verified via Pact.
- **Compliance**: LGPD, WhatsApp BSP (templates/24h window/opt-out), Meta ToS (no automated DM), Google Places ToS (persist place_id, canonical = first-party validated), OTA partner approval, source-scraping legal risk documented per source.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Collector (this Python repo) IS the full Brave engine | Keep norteia-api footprint minimal; only Mar crosses the boundary | — Pending |
| Score §7.6 + DLQ as canonical gate (not approve-everything) | Scale to all-BR cold start without human bottleneck on every record | — Pending |
| DLQ + Brave monitor on this dashboard, not norteia-api Filament | Conscious deviation from doc §15.7; this becomes the territorial CMS | — Pending |
| DeepSeek (backend, OpenRouter) + Sonnet 4.5 (WhatsApp) | Throughput/cost for batch backend; quality for PT-BR conversation | — Pending |
| Destinos lane precedes Atrativos | An atrativo references a parent destino that must already be in Mar | — Pending |
| Celery+Redis for orchestration (Temporal only if durable workflows justify) | Fan-out by UF; outreach tolerates day-scale latency | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-16 after Phase 4 (Dashboard — Territorial CMS) completion — final phase of this milestone*
