# Feature Research

**Domain:** Master-data / data-quality ETL pipeline with confidence scoring + human-in-the-loop DLQ + multi-source collection (LLM agents, Places, WhatsApp outreach) — territorial tourism dataset for Brazil ("Brave": Nascente → Rio → Mar)
**Researched:** 2026-06-11
**Confidence:** HIGH (project is fully specified in PROJECT.md / PLANO-BRAVE.md; domain patterns corroborated by current MDM / entity-resolution / DLQ / data-enrichment literature)

> Framing note: this is **not generic CRUD**. The product's users are two distinct audiences:
> (1) **the pipeline itself** (it must reliably ingest, score, route, and publish), and
> (2) **operators / data stewards** working the dashboard (DLQ review, WhatsApp gate, monitoring).
> "Table stakes" below means *the pipeline or the operators fail without it* — not "users feel the
> product is incomplete." A scoring pipeline that publishes wrong records to the platform is worse
> than one that publishes nothing.

## Feature Landscape

### Table Stakes (Pipeline / Operators Fail Without These)

These are non-negotiable for the milestone. The entire value proposition ("only validated,
reliability-scored canonical records reach the platform") collapses if any are missing.

#### Brave core (entity-agnostic engine)

| Feature | Why Required (fail mode if absent) | Complexity | Notes |
|---------|------------------------------------|------------|-------|
| **Nascente: raw, source-tagged, versioned ingest (JSONB)** | No append-only raw store → no replay/backfill, no audit, no re-score when rules change. Industry DLQ practice: *preserve evidence with timestamps + reason codes*. | MEDIUM | Immutable; never mutate raw. `source`, `source_ref`, `entity_type`, `uf`, `payload (JSONB)`, `ingested_at`, version. This is the system of record for provenance. |
| **Rio: explode → dedup → normalize → label → score** | The actual ETL. Without it Nascente is a swamp. Dedup is the single highest-value step (industry: ~92% of sources contain duplicates). | HIGH | Two-stage dedup (exact hash blocking → fuzzy/embedding via pgvector) is the standard entity-resolution pattern. Normalization (names/coords/addresses) is prerequisite to dedup quality. |
| **Score engine §7.6 (calibrable weights, one engine for both entities)** | The canonical gate. Hard-coded weights = can't tune without redeploy; per-entity copies = drift. This is the core differentiator *and* table stakes. | MEDIUM | origem 30 · completude 20 · corroboração 20 · atualidade 15 · validação humana 15. Weights in config. Pure function over a normalized record → deterministic, unit-testable (Mar/DLQ/descarte boundary cases). |
| **Three-way routing by score: Mar (≥85) / DLQ (51–84.9) / descarte (≤50)** | Without thresholded routing you're back to "human approves everything" → can't scale to all-BR cold start. | LOW | Thresholds are config, not magic numbers. Mirrors MDM "auto-link threshold + manual-review band" pattern. |
| **Mar: canonical store + idempotent push to norteia-api** | The only output that matters. Must be idempotent by canonical key / `source_ref` (re-push must not duplicate). | MEDIUM | Versioned; supports invalidation/update. Idempotency is non-negotiable per DLQ/ETL best practice — reprocessing must respect idempotency keys. |
| **Provenance / lineage on every record** | Stewards must defend every DLQ decision; the platform must trust scores. "Why is this 73%?" must be answerable per-criterion. | MEDIUM | Carry `provenance` (sources, per-criterion score breakdown, decisions) all the way to the Mar push. Modern MDM treats this as mandatory, not optional. |
| **DLQ as durable, monitorable, actionable queue (not just a log)** | A DLQ that's "visible, monitorable, actionable" is the explicit industry bar. A dead-letter table no one works = silent data loss. | MEDIUM | Backed by Brave state, surfaced in dashboard. Records carry reason codes. |
| **Reprocess / re-score on demand (idempotent)** | When weights change, sources improve, or human validates, records must re-flow without manual surgery or duplication. | MEDIUM | "Backfill any range" + "re-run is safe" are core ETL patterns. Triggered by config change, new corroboration, human validation, or community error report. |
| **Error classification: transient (retry) vs permanent (DLQ/descarte)** | Network blip ≠ corrupt data. Auto-retrying poison records burns cost; DLQ-ing transient failures floods stewards. | MEDIUM | Transient (timeout, 429) → backoff retry; permanent (schema fail, CLOSED_PERMANENTLY) → route. |
| **24/7 orchestration: Celery + Redis (beat), fan-out by UF** | Continuous all-BR coverage is a stated execution constraint. Manual triggering doesn't cover 27 UFs 24/7. | MEDIUM | Fan-out by UF = natural sharding + per-state batch mode for DLQ. |
| **External boundaries behind client interfaces** | Testability constraint: *no test hits Places/OTA/Apify/WhatsApp/OpenRouter/Mtur/norteia-api by default*. Without the seam, the suite can't run offline / keyless in CI. | LOW | Every external = an interface with a fake. Foundational, cheap if done first, expensive to retrofit. |
| **FastAPI surface: webhooks + REST + lane ingest** | Single entry for WhatsApp/email webhooks, dashboard reads, lane ingest, and community error-report reopen. | MEDIUM | Webhook receivers must be idempotent (providers retry). |
| **Observability: `llm_generations` table + USD cost guard + per-layer Brave metrics + queue/worker + audit logs** | Cost guard prevents runaway LLM spend (real money, batch volume across all-BR). Per-layer metrics = the only way to know the pipeline is healthy. Audit logs = compliance + steward defensibility. | MEDIUM | Governance dashboards tracking match accuracy, steward workload, throughput are standard MDM console features. |

#### Lane: Destinos (precedes Atrativos)

| Feature | Why Required | Complexity | Notes |
|---------|--------------|------------|-------|
| **MturSeedIngest (origem=100)** | Authoritative official seed; the trusted backbone every other destino source corroborates against. | LOW–MEDIUM | Categorized municipalities (Oferta Principal/Complementar/Apoio); link `municipality_id`. Mostly a structured loader. |
| **NotebookLMIngest (origem=80)** | Fills destinos absent from Mtur (distritos/localidades). Without it, coverage has official-only holes. | LOW | Structured-report loader → Nascente. |
| **DesmembramentoAgent §7.4 (DeepSeek, origem=40)** | The hard part: a "município" is not a "destino." Lists real destinos (praias/vilas/distritos) inside each Oferta Principal. Without it the dataset is administratively-shaped, not tourist-shaped. | HIGH | LLM-generated → flagged "pending validation" → low score by design → DLQ. **Mandatory 2nd-layer Pydantic+`instructor` validator** (DeepSeek JSON-schema is weak). |
| **Human validation in DLQ, batch-by-state (BA/RJ/SP/SC/CE/PE first)** | Sets `validação humana=100` → only path to Mar for most destinos. Batch-by-state = the workflow that makes cold-start tractable. | MEDIUM | Steward console territory; this is where destinos actually become canonical. |

#### Lane: Atrativos (depends on Destinos in Mar)

| Feature | Why Required | Complexity | Notes |
|---------|--------------|------------|-------|
| **Sub-state machine** (discovered → contacts_found → signals_gathered → score → [borderline] aguardando_consulta_whatsapp → whatsapp_in_progress → re-score) | Atrativo collection is multi-step and stateful; without an explicit state machine, progress is unrecoverable and untestable. | MEDIUM | Each transition is a Celery task + persisted state. Resumable. |
| **DiscoveryAgent (Google Places sweep + gov, resolves parent destino)** | Finds candidate atrativos and binds each to a destino **already in Mar** — the hard ordering dependency. | HIGH | Persist `place_id` (ToS). DeepSeek → schema → Nascente. Parent-destino resolution is the join point with the Destinos lane. |
| **ContactFinderAgent (Places Details + site/IG-FB/email)** | No contacts → no WhatsApp verification → no owner validation. Feeds the entire outreach lane. | MEDIUM | Phone/website/WhatsApp link/email. |
| **SignalAgent (business_status, hours, reviews freshness ≤30d)** | Cheap, ToS-clean automatic signals. `reviews[].publishTime ≤30d ⇒ funcionando` is the Atualidade signal; `CLOSED_*` → descarte avoids contacting dead businesses. | MEDIUM | Places primary; IG/X via Apify best-effort; OTA optional (ticketed cross-check only). "Continuous signal monitoring" is the industry freshness pattern. |
| **WhatsApp gate (human, dashboard) — borderline only** | Volume control = ban-risk + cost mitigation. Human decides *who* to contact. Without it you spam and get the number banned. | MEDIUM | Only <85% records lacking direct validation enter the gate. Ramp-up enforced. |
| **WhatsAppAgent (automated: BSP + n8n thin + LangGraph; Sonnet asks, DeepSeek extracts)** | Owner validation is the strongest corroboration signal; boosts score → re-score → Mar/DLQ. n8n stays thin so logic is testable in code. | HIGH | Approved templates, 24h window, opt-out, consent log. Sonnet for PT-BR conversation; DeepSeek for extraction. |

#### Dashboard (territorial CMS)

| Feature | Why Required | Complexity | Notes |
|---------|--------------|------------|-------|
| **DLQ review queue** (Nascente payload + Rio data + §7.6 per-criterion score + signals + WhatsApp log → approve/reject/edit/reprocess; batch-by-state) | The console where humans turn DLQ into Mar. The single most-used operator surface. Per-criterion score visibility = explainability = defensible decisions (MDM standard). | HIGH | Edit must re-score. Batch-by-state is the cold-start workflow. |
| **Brave monitor §15.7** (volume per layer, approval/rejection/DLQ rates, failure alerts, throughput, audit) | Operators can't fly blind on a 24/7 all-BR pipeline. Failure alerts catch a stuck lane before it silently rots. | MEDIUM | Governance dashboard pattern. |
| **WhatsApp gate UI** (aguardando_consulta_whatsapp queue → approve/reject; ramp) | The human control point that keeps outreach legal and the number un-banned. | MEDIUM | |
| **Cost & LLM view** | USD guard is meaningless if no one can see spend per lane/model. | MEDIUM | Reads `llm_generations`. |
| **Bearer-header auth** | Operator console with edit/approve power must be access-controlled (RBAC is MDM table stakes). | LOW | Stated stack constraint. |

#### Compliance & testability (table stakes — legal/operational, not optional)

| Feature | Why Required | Complexity | Notes |
|---------|--------------|------------|-------|
| **LGPD: legal basis + Norteia ID + opt-out + consent log + minimization** | Contacting businesses without this is illegal in Brazil. Atrativos/WhatsApp lane only (destinos have no contact PII). | MEDIUM | Consent log is a record, not a checkbox. |
| **WhatsApp BSP: approved templates + 24h window + human gate + ramp + opt-out** | Violations get the number banned and the BSP account suspended — kills the lane. | MEDIUM | Hard external constraint, not a feature choice. |
| **100%-offline test suite (docker-compose Postgres+Redis; externals opt-in; CI keyless)** | Stated constraint. Without it, no safe iteration and no CI. Score engine + desmembramento unit tests covering Mar/DLQ/descarte are explicitly required. | MEDIUM | `respx`/VCR for HTTP; LLM fakes; webhook fixtures; **Pact** for the norteia-api contract. |

### Differentiators (What Makes Brave Better Than a Naive Scraper)

These are where Brave competes against "just scrape Google and dump rows." They align directly with
Core Value (*only trustworthy canonical records reach the platform*).

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Multi-criteria §7.6 reliability score as a first-class, calibrable gate** | A naive scraper has one signal (it found the row). Brave fuses origem + completude + corroboração + atualidade + validação humana into a tunable score. This *is* the moat. | MEDIUM | Calibrable weights let ops tighten/loosen without code. One engine across entities prevents drift. |
| **Owner-validation via WhatsApp outreach** | Turns a guessed record into an owner-confirmed one (existe? funcionando? horários? valor?). No scraper does first-party confirmation at scale. Strongest corroboration boost. | HIGH | The headline capability of the Atrativos lane. |
| **LLM "desmembramento" — município → real tourist destinos** | Converts administrative units into tourist-shaped destinos (Trancoso ≠ Porto Seguro sede). Reshapes the dataset to how travelers actually think. | HIGH | LLM-as-collector with mandatory human validation gate. |
| **Freshness signal from review recency (≤30d ⇒ funcionando)** | Cheap, ToS-clean liveness proxy without contacting anyone. Most directories serve stale records; Brave actively decays/refreshes. | LOW–MEDIUM | Feeds Atualidade criterion. Continuous-signal pattern from data-enrichment SOTA. |
| **Two-stage dedup (exact hash → fuzzy/embedding via pgvector)** | Semantic dedup catches "Praia do Forte" vs "Praia de Forte" that exact match misses. Higher golden-record quality. | MEDIUM | Standard ER blocking+scoring, applied to PT-BR toponyms. |
| **Provenance-rich, auditable golden records** | Every Mar record carries its lineage + per-criterion score. The platform (and future AI agents) can reason about confidence, not just consume a name. Matches "golden context for AI" trend. | MEDIUM | Differentiator for the *consumer* (norteia-api RAG/assistants), not just internal ops. |
| **Community error-report → reopen in Rio/DLQ loop** | Closed feedback loop: published records that turn out wrong flow back for re-scoring. Self-healing dataset. | MEDIUM | Webhook from norteia-api → reopen. Most pipelines are one-way. |
| **Cost-guarded LLM observability (`llm_generations` + USD guard)** | Running DeepSeek + Sonnet across all-BR without a cost ceiling is a budget bomb. First-class cost governance is rare and operationally decisive. | MEDIUM | Per-lane/per-model spend visibility. |
| **Batch-by-state steward workflow** | Stewards work one UF at a time with full context, matching how the cold-start rollout actually proceeds (BA/RJ/SP/SC/CE/PE first). Throughput multiplier vs record-by-record. | MEDIUM | Workflow differentiator, not just a filter. |

### Anti-Features (Deliberately NOT Built This Milestone)

| Feature | Why Tempting | Why Problematic | Alternative |
|---------|--------------|-----------------|-------------|
| **Human-approves-every-record** | Feels "safe" / maximally accurate | Cannot scale to all-BR cold start; defeats the entire score-gate premise; steward burnout | §7.6 score + DLQ gate; humans see only the 51–84.9 band and borderline outreach |
| **Hosting Brave inside norteia-api** | One repo, fewer moving parts | External APIs on the platform's hot path; couples a heavy ETL engine to the consumer; explicitly rejected by user decision | Separate Python collector; only Mar crosses the boundary via idempotent push |
| **DLQ/monitor inside norteia-api Filament CMS (doc §15.7)** | Doc originally suggested it | Splits the operator surface; conscious deviation — this dashboard *is* the territorial CMS | Next.js dashboard owns DLQ + monitor + gates |
| **Automated IG/FB DM outreach** | More verification channels | Meta ToS gray/red zone; account-ban risk; legal exposure | Read-only IG/X signal via Apify (best-effort); WhatsApp BSP is the only outbound channel |
| **Full ML model training / learning-to-rank matcher** | "AI-native MDM" marketing; could auto-improve matching | Premature without labeled volume; opaque; un-unit-testable; massive scope | Deterministic rules + NLP (DeepSeek) + calibrable weights; revisit only after stewards generate labeled data |
| **Future lanes now** (official-site scraping monitor, business CMS, UGC) | "Core already supports them" | Scope explosion; unvalidated; core must *support* ≠ must *build* | Build entity-agnostic core; ship Destinos + Atrativos only |
| **Future entities now** (experiência, evento, temporada, rota) | Same engine could do them | Each needs its own taxonomy/signals; dilutes focus on getting destino+atrativo right | Entity-agnostic core proves extensibility; add entities post-validation |
| **Temporal / durable-workflow engine** | "Proper" workflow durability | Outreach tolerates day-scale latency; Celery+Redis fan-out is sufficient; Temporal adds infra weight | Celery + Redis (beat); adopt Temporal only if a durable-workflow need is proven |
| **Real-time / streaming everything** | Sounds modern | This is a batch enrichment domain (outreach takes days); streaming adds complexity with no value | Scheduled batch (Celery beat), fan-out by UF |
| **Generic dedup UI / manual merge tool for every record** | Full MDM stewardship parity | The score gate already auto-handles the bulk; a heavy merge UI is over-engineering for v1 | Edit-in-DLQ for the borderline band only; auto-dedup elsewhere |
| **Webhook receiver / migrations / ingest endpoints in norteia-api** | Needed end-to-end eventually | Built in the separate Laravel repo (Trilha 5); here only the **contract** matters | Pact contract test defines the boundary; Laravel side is out of this repo's scope |
| **Multi-tenant / per-client config, i18n, theming on dashboard** | "Build it right once" | Single internal ops tool; YAGNI | PT-BR, single tenant, Bearer auth |

## Feature Dependencies

```
[External clients behind interfaces]           (foundational — build FIRST; everything mocks against it)
        └──enables──> [Offline test suite] [Score engine tests] [Lane tests]

[Nascente (raw versioned store)]
        └──requires──> [Rio (dedup/normalize/label/score)]
                              └──requires──> [Score engine §7.6]
                                                    ├──drives──> [Mar / DLQ / descarte routing]
                                                    │                     └──requires──> [Mar idempotent push to norteia-api]
                                                    └──drives──> [DLQ review queue (dashboard)]
                                                                          └──feeds──> [Human validation → re-score]  (loop back into Rio)

[Celery + Redis orchestration] ──enables──> [24/7 fan-out by UF] ──enables──> [batch-by-state DLQ mode]

DESTINOS LANE  ──must populate Mar BEFORE/WITH──>  ATRATIVOS LANE
  (MturSeed + NotebookLM + Desmembramento → Rio → DLQ → human → Mar)
        └── parent destino in Mar ──required by──> [DiscoveryAgent.resolve_parent_destino]

ATRATIVOS sub-state machine:
  DiscoveryAgent ──> ContactFinderAgent ──> SignalAgent ──> (Rio score)
        └─[borderline]─> WhatsApp gate (human) ──> WhatsAppAgent ──> re-score ──> Mar/DLQ
  [ContactFinderAgent] ──required by──> [WhatsAppAgent]   (no contacts → no outreach)
  [SignalAgent] ──enhances──> [Score engine]  (atualidade/funcionando signals)

[Observability (llm_generations + cost guard + metrics + audit)] ──enables──> [Brave monitor] [Cost & LLM view]

[Community error-report webhook] ──reopens──> [Rio/DLQ]   (self-healing loop)

[Score gate] ──conflicts with──> [Human-approves-everything]   (mutually exclusive philosophies)
```

### Dependency Notes

- **External clients behind interfaces → everything:** This is the cheap-if-first / expensive-if-retrofit foundation. The offline keyless test suite (a hard constraint) is impossible without it. Build in Trilha 1.
- **Score engine → routing → DLQ → human validation → re-score (loop):** The score engine is the hub. It must exist and be unit-tested (Mar/DLQ/descarte boundary cases) before any lane produces records, and before the DLQ UI has anything to show. Human validation writes `validação humana=100` and **triggers re-score** — so re-score/reprocess must be idempotent or the loop double-publishes.
- **Destinos before Atrativos (hard ordering):** An atrativo's `DiscoveryAgent` resolves a parent destino that **must already be in Mar**. Atrativos cannot meaningfully reach Mar until at least the seed states' destinos are validated. This drives phase ordering: Trilha 2 (Destinos) precedes Trilha 3 (Atrativos).
- **ContactFinder before WhatsAppAgent:** No contacts → no outreach → no owner validation. The Atrativos sub-state machine encodes this strictly (`contacts_found` precedes outreach states).
- **SignalAgent enhances Score engine:** Provides Atualidade (review freshness) and `funcionando`/`CLOSED_*` inputs. Not a hard prerequisite to a *first* score, but the score is much weaker without it.
- **WhatsApp gate (human) is a hard precondition to WhatsAppAgent:** No automated outreach without human approval of *who* to contact — for ban/cost/legal reasons. This is a deliberate human bottleneck, not a missing automation.
- **Observability before/with everything 24/7:** A continuous all-BR pipeline without per-layer metrics + cost guard is operationally blind and financially exposed. Should land early in Trilha 1, not bolted on later.
- **Dashboard (Trilha 4) parallels Trilhas 1–3** but each panel depends on the corresponding backend surface existing (DLQ UI needs DLQ + score breakdown; gate UI needs the sub-state machine; cost view needs `llm_generations`).

## MVP Definition

### Launch With (this milestone)

Trilha 1 (Brave core) + Trilha 2 (Destinos) + Trilha 3 (Atrativos) + Trilha 4 (Dashboard).

- [ ] **External clients behind interfaces** — without it nothing is testable; build first
- [ ] **Nascente + Rio + Score engine §7.6 + Mar/DLQ/descarte routing** — the engine; the whole point
- [ ] **Idempotent Mar push (+ Pact contract)** — the only output that reaches the platform
- [ ] **Reprocess/re-score (idempotent) + error classification + DLQ** — reliability spine
- [ ] **Celery+Redis 24/7 fan-out by UF** — coverage constraint
- [ ] **Observability: `llm_generations` + USD cost guard + per-layer metrics + audit logs** — operate safely
- [ ] **Destinos lane: Mtur + NotebookLM + Desmembramento → DLQ → batch-by-state human validation → Mar** — must precede Atrativos
- [ ] **Atrativos lane: Discovery → ContactFinder → SignalAgent → WhatsApp gate → WhatsAppAgent → re-score** — owner-validated atrativos
- [ ] **Dashboard: DLQ review (batch-by-state) + Brave monitor + WhatsApp gate + Cost/LLM view + Bearer auth**
- [ ] **LGPD + WhatsApp BSP compliance** — legal precondition for any real outreach
- [ ] **100%-offline keyless test suite** (score engine + desmembramento boundary cases + Pact)

### Add After Validation (v1.x)

- [ ] **Active freshness decay / re-score cron** (§7.8 monitor) — trigger: Mar records aging in production
- [ ] **OTA price cross-check** (ticketed only) — trigger: ticketed-atrativo volume justifies partner onboarding
- [ ] **Richer steward analytics** (per-steward throughput, accuracy trends) — trigger: a steward team large enough to manage
- [ ] **Auto-tuning of §7.6 weights from steward decisions** — trigger: enough labeled DLQ outcomes to calibrate against

### Future Consideration (v2+)

- [ ] **Additional lanes** (official-site scraping monitor, business CMS, UGC) — defer until destino+atrativo proven
- [ ] **Additional entities** (experiência, evento, temporada, rota) — core supports them; build post-PMF
- [ ] **ML / learning-to-rank matcher** — defer until labeled volume exists; deterministic+NLP first
- [ ] **Temporal durable workflows** — only if a proven durable-workflow need emerges beyond Celery's reach

## Feature Prioritization Matrix

| Feature | Operator/Pipeline Value | Implementation Cost | Priority |
|---------|------------------------|---------------------|----------|
| Score engine §7.6 (calibrable) | HIGH | MEDIUM | P1 |
| Nascente/Rio/Mar/DLQ + routing | HIGH | HIGH | P1 |
| External clients behind interfaces | HIGH (unblocks tests) | LOW | P1 |
| Idempotent Mar push + Pact | HIGH | MEDIUM | P1 |
| Reprocess/re-score (idempotent) | HIGH | MEDIUM | P1 |
| Two-stage dedup (hash → pgvector) | HIGH | MEDIUM | P1 |
| Celery+Redis 24/7 fan-out by UF | HIGH | MEDIUM | P1 |
| Observability + USD cost guard | HIGH | MEDIUM | P1 |
| Destinos: Mtur + NotebookLM seed | HIGH | LOW–MED | P1 |
| DesmembramentoAgent §7.4 + validator | HIGH | HIGH | P1 |
| Batch-by-state human validation | HIGH | MEDIUM | P1 |
| DLQ review queue (dashboard) | HIGH | HIGH | P1 |
| Brave monitor (dashboard) | HIGH | MEDIUM | P1 |
| Atrativos sub-state machine | HIGH | MEDIUM | P1 |
| DiscoveryAgent (Places + parent destino) | HIGH | HIGH | P1 |
| ContactFinderAgent | HIGH | MEDIUM | P1 |
| SignalAgent (status/hours/freshness) | HIGH | MEDIUM | P1 |
| WhatsApp gate (human) | HIGH | MEDIUM | P1 |
| WhatsAppAgent (BSP + n8n + LangGraph) | HIGH | HIGH | P1 |
| LGPD + BSP compliance | HIGH (legal) | MEDIUM | P1 |
| Offline keyless test suite | HIGH | MEDIUM | P1 |
| Community error-report reopen loop | MEDIUM | MEDIUM | P2 |
| Cost & LLM dashboard view | MEDIUM | MEDIUM | P2 |
| OTA price cross-check | LOW–MED | MEDIUM | P3 |
| Active freshness-decay cron | MEDIUM | MEDIUM | P3 |
| Auto-tuned §7.6 weights | MEDIUM | HIGH | P3 |
| Additional lanes / entities | LOW (now) | HIGH | P3 |

**Priority key:** P1 = must have this milestone · P2 = should have, add when possible · P3 = future.

## Competitor / Reference Feature Analysis

Brave sits at the intersection of MDM platforms (Tamr, Profisee), entity-resolution tooling
(Senzing, Data Ladder), and place-data providers (Google Places, B2B enrichment "waterfall").
No competitor combines confidence-scored golden records *with* LLM-collector lanes *with* WhatsApp
owner-verification for territorial tourism data — that combination is Brave's niche.

| Capability | MDM platforms (Tamr/Profisee) | Place/enrichment providers | Brave's approach |
|-----------|-------------------------------|----------------------------|------------------|
| Confidence/match scoring | Yes — auto-link threshold + manual band | Implicit / vendor-internal | Explicit §7.6 multi-criteria, calibrable, one engine both entities |
| Human-in-the-loop steward console | Yes — no-code review, RBAC, audit | Rare (human spot-checks) | DLQ review with per-criterion explainability, batch-by-state |
| Survivorship / golden record | Yes — source-priority/most-recent/quality-score rules | Waterfall merge | Score-gated Mar + provenance; deterministic + NLP |
| LLM as a collection source | Emerging ("AI-native MDM") | Limited | DesmembramentoAgent + DeepSeek extraction with mandatory validator |
| First-party owner verification | No | Phone/email spot-checks | WhatsApp BSP outreach → owner-validation score boost |
| Provenance/lineage for AI consumption | Yes (golden-context trend) | No | Provenance carried to Mar; feeds norteia-api RAG/assistants |
| Self-healing from consumer feedback | Partial | No | Community error-report → reopen in Rio/DLQ |

## Sources

MDM / golden record / steward console:
- [What Is a Golden Record in MDM? — Profisee](https://profisee.com/blog/what-is-a-golden-record/)
- [Golden Record Management — Profisee](https://profisee.com/platform/golden-record-management/)
- [From Golden Record to Golden Context — Medium](https://medium.com/@Tahir-Khan/from-golden-record-to-golden-context-redefining-master-data-for-ai-agent-consumption-2349eec0840b)
- [Redefining Data Stewardship — Tamr](https://www.tamr.com/blog/redefining-data-stewardship-and-governance-how-ai-native-mdm-empowers-responsible-data-management)
- [From clean to confident: AI elevating MDM — KPMG](https://kpmg.com/be/en/insights/technology/data-insights/from-clean-to-confident-how-ai-is-elevating-master-data-management.html)

Entity resolution / dedup / survivorship:
- [What Is Entity Resolution? — Senzing](https://senzing.com/what-is-entity-resolution/)
- [Guide to data survivorship — Data Ladder](https://dataladder.com/guide-to-data-survivorship-how-to-build-the-golden-record/)
- [Entity Resolution blueprint — Databricks Community](https://community.databricks.com/t5/get-started-discussions/blueprint-entity-resolution-dedup-amp-golden-records-on/td-p/150885)

DLQ / idempotency / pipeline reliability:
- [Data Pipeline Design Patterns: Idempotency, DLQ, CDC — dataskew.io](https://dataskew.io/blog/data-pipeline-design-patterns/)
- [ETL Best Practices for Reliable Pipelines — OneUptime](https://oneuptime.com/blog/post/2026-02-13-etl-best-practices/view)
- [DLQ Patterns for Failed Message Handling — OneUptime](https://oneuptime.com/blog/post/2026-02-09-dead-letter-queue-patterns/view)

Data enrichment / verification / freshness:
- [Data Enrichment 2026: Waterfall vs Real-Time — Amplemarket](https://www.amplemarket.com/blog/best-b2b-data-enrichment-tools)
- [B2B Contact Data Quality Guide — Salesmotion](https://salesmotion.io/blog/b2b-data-quality-guide)

Project specification (primary, HIGH confidence):
- `.planning/PROJECT.md` (Active requirements)
- `docs/PLANO-BRAVE.md` (§B.1 core, §B.3 Destinos, §B.4 Atrativos, §B.7 dashboard, §7.6 score)

---
*Feature research for: confidence-scored territorial master-data pipeline with LLM + WhatsApp collection lanes*
*Researched: 2026-06-11*
