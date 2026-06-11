# Pitfalls Research

**Domain:** 24/7 LLM-assisted territorial data-collection + reliability-scoring pipeline (Brave: Nascente→Rio→Mar + DLQ), all-Brazil cold start, paid DeepSeek/OpenRouter extraction, Claude Sonnet WhatsApp outreach, Google Places/Apify/OTA signals, strict 100%-offline test discipline.
**Researched:** 2026-06-11
**Confidence:** HIGH on WhatsApp BSP, Google Places ToS, OpenRouter slug/variant behavior, pgvector approximate-recall (all verified against current official/vendor docs). MEDIUM on scoring-calibration and Celery operational specifics (domain reasoning + general engineering literature, not project-measured yet).

> Trilha map used below (from PLANO §"Sequência de fases GSD"):
> **T1 = Brave core** (Nascente/Rio/Mar/DLQ + score engine + FastAPI + Celery/Redis + clients + observability) ·
> **T2 = Destinos lane** · **T3 = Atrativos lane (+WhatsApp)** · **T4 = Dashboard / CMS** · **T5 = norteia-api ingestion (Pact)** · plus a cross-cutting **Compliance** concern that lives mostly in T3.

---

## Critical Pitfalls

### Pitfall 1: The DLQ becomes a landfill — median score lands in the 51–84.9% band and buries human reviewers

**What goes wrong:**
On a cold all-Brazil start, almost every record lacks *validação humana* (15%) and most LLM-generated destinos start at *origem*=40. With the §7.6 weights, a DesmembramentoAgent destino with no human validation and thin corroboration mathematically lands in the DLQ band (51–84.9%) — not in Mar, not in descarte. Result: the DLQ receives essentially the entire intake (tens of thousands of municipality sub-destinos + every borderline atrativo), and the human queue grows faster than any team can drain it. The "DLQ as gate, not approve-everything" decision silently degrades into "approve everything, just slower."

**Why it happens:**
The score weights were designed for a *steady state* where validação humana and corroboração are usually present. At cold start those two criteria (35% combined) are near-zero for most records, so the *distribution* of scores collapses into the DLQ band. Nobody simulates the score distribution before wiring the pipeline; the band boundaries (50 / 85) are treated as fixed truths rather than as knobs that must be tuned against the actual intake distribution.

**How to avoid:**
- Before any real intake, run the score engine over a *synthetic but representative* sample of each source type (mtur origem=100, notebooklm=80, desmembramento=40, places-discovered atrativo) and **plot the score histogram**. Confirm the band boundaries produce a *workable* DLQ volume, not 95% of intake.
- Treat the DLQ as a **prioritized, batched** queue from day one (PLANO already specifies "modo lote por estado"): reviewers approve a *batch* of a município's sub-destinos in one action, not one record at a time. Design the dashboard review unit = "a município's desmembramento", not "a row".
- Add an **auto-promote shortcut for high-origem, well-corroborated records**: a mtur Oferta Principal município that 1:1-maps to an existing destino with coordinate corroboration can skip DLQ. Reserve human review for the genuinely ambiguous (LLM-generated subdivisions).
- Define and monitor a **DLQ drain-rate SLO** (records reviewed/day vs. records entering DLQ/day). If intake rate > drain rate for N days, the funnel is mis-tuned — pause intake fan-out, not reviewers.

**Warning signs:**
DLQ depth grows monotonically across states; Mar push rate is near-zero in week 1; >80% of Rio output carries the DLQ label; reviewers report "every item looks the same / I'm rubber-stamping". A score histogram with a single tall spike between 50 and 85.

**Phase to address:** T1 (score engine must ship with a distribution-simulation harness and calibrable boundaries) + T4 (batch-by-state review UI) + T2 (Destinos is where the flood originates).

---

### Pitfall 2: Score calibration drift and threshold gaming — the 85% line stops meaning "trustworthy"

**What goes wrong:**
Two related failures. (a) **Drift:** as sources, LLM prompts, and corroboration logic evolve across states, the same numeric score means different real-world confidence for BA vs. SP vs. a later state — but the 85% Mar boundary stays fixed, so Mar quality silently diverges. (b) **Gaming:** because *validação humana=100* is a hard 15-point boost that flips most borderline records over 85%, the path of least resistance for an overwhelmed reviewer is "click approve" — which injects human-validated=100 and pushes to Mar regardless of whether the underlying data is actually correct. The score becomes a proxy for "a human clicked", not for reliability.

**Why it happens:**
Weighted-sum scores are not probabilities; they have no inherent calibration to "% chance this record is correct". Without periodic back-testing against ground truth, nobody notices the score drifting away from reality. Gaming happens because the reward structure (drain the DLQ) and the scoring mechanic (human approval = +15 and Mar) are aligned toward volume, not correctness.

**How to avoid:**
- **Version the score config** (weights + band boundaries) and stamp every record with the `score_version` that produced it. Never silently re-weight live records.
- Keep a small **golden set per state** (records with known-correct ground truth) and periodically re-score it; if the same golden records change band after a config/prompt change, you have drift — investigate before shipping.
- **Decouple "human reviewed" from "human validated".** A reviewer action should record *what* they verified (existence? coordinates? name? owner-confirmed?), and only the verified dimensions feed the score. A bare "approve" should not auto-set validação humana=100.
- **Audit-log every score-affecting action** (PLANO §B.7 already mandates audit logs) and surface a "reviewer approval rate" metric per reviewer; an approval rate near 100% is a gaming red flag.

**Warning signs:**
Reviewer approval rate ~100%; Mar grows fast but community "reportar erro" webhooks spike weeks later; golden-set records silently change band after a prompt tweak; score distribution shifts between states with no explanation.

**Phase to address:** T1 (score versioning, golden-set harness, decoupled validation dimensions) + T4 (per-reviewer approval-rate metric, structured "what did you verify" UI).

---

### Pitfall 3: Embedding dedup fails at all-Brazil scale — both false merges and missed duplicates

**What goes wrong:**
Two opposite failure modes, both fatal to a territorial base:
- **False merge (over-dedup):** "Trancoso" (a famous distrito) gets merged into "Porto Seguro" (its parent município), or two genuinely different attractions with similar names collapse into one. Homonym municipalities are rampant in Brazil (dozens of "Bom Jesus", "Santa Maria", "São Domingos" across different UFs) — name-embedding similarity alone will merge a São Domingos/BA with a São Domingos/SE.
- **Missed duplicate (under-dedup):** the same praia ingested from Mtur and from NotebookLM under slightly different tourist names stays as two records. **Critically, pgvector's HNSW index is *approximate by design* — it will miss true near-neighbors depending on `ef_search`**, so "no similar vector found" does not mean "no duplicate exists".

**Why it happens:**
Embedding similarity conflates *lexical/semantic* similarity with *territorial identity*. A distrito and its parent município are semantically very close but are different entities. Homonyms are lexically identical but different entities. Teams trust the cosine score as if it were an identity oracle. And the approximate-index recall tradeoff is invisible — HNSW returns *some* neighbors fast, and developers assume that's *all* neighbors.

**How to avoid:**
- **Never dedup on name-embedding alone. Always gate by territorial keys first:** `UF` + `municipality_id` (and `parent destino` for atrativos) must match before two records are even *candidates* for merge. Homonyms in different UFs can never be candidates.
- **Use a blocking strategy:** exact hash on normalized (name + UF + município) first; embedding similarity only *within* a block and only as a *candidate generator*, with a deterministic tie-break (coordinate distance, type, parent) before any actual merge.
- **Treat distrito-vs-município as a hierarchy problem, not a dedup problem.** Trancoso is a *child* of Porto Seguro, not a duplicate of it. The DesmembramentoAgent's whole point is to create children; dedup must be hierarchy-aware so it doesn't undo that.
- **Tune and *measure* pgvector recall.** HNSW `ef_search` controls recall-vs-latency; pick it against a labeled duplicate set and record the achieved recall (community sees 99%+ at sufficiently high `ef_search`, but only if measured). Consider an **exact scan fallback** for the small, high-stakes candidate set after blocking, since exact search is cheap once the block is small.
- **Cap embedding cost** by embedding once at ingest, caching the vector, and only re-embedding on name change — not on every Rio pass.

**Warning signs:**
Mar count is suspiciously *lower* than known municipality counts (over-merge); two reviewers find the same praia twice (under-merge); a distrito disappears into its parent; dedup latency or embedding bill grows super-linearly; recall on the labeled duplicate set < target and nobody knows the number.

**Phase to address:** T1 (Rio dedup architecture: blocking + territorial-key gating + measured pgvector recall + cached embeddings) + T2 (hierarchy-aware merge for desmembramento output).

---

### Pitfall 4: DeepSeek JSON-schema weakness → silently malformed extractions and hallucinated destinos polluting Mar

**What goes wrong:**
- **Schema non-adherence:** DeepSeek (especially via OpenRouter, where the served provider can vary) produces JSON that *almost* matches the Pydantic schema — wrong types, missing fields, extra commentary, truncated arrays. If the second-layer validator is lenient or absent on some path, malformed records flow into Nascente/Rio.
- **Hallucinated destinos:** the DesmembramentoAgent asks DeepSeek to "list the real destinos inside this município". DeepSeek will happily invent plausible-but-nonexistent praias/vilas, or attach a famous beach to the wrong município. These hallucinations are *plausible*, score into the DLQ band, and — if reviewers rubber-stamp (Pitfall 2) — land in Mar as fake places.

**Why it happens:**
DeepSeek's structured-output adherence is weaker than frontier models (the PLANO itself flags this and mandates `instructor`). Hallucination is intrinsic to "list things that exist" prompts with no grounding source — the model has no way to say "I don't actually know the sub-destinos of this obscure município", so it confabulates. Cost/latency pressure tempts teams to skip the validation layer on "trusted" paths.

**How to avoid:**
- **Mandatory `instructor`/Pydantic validation on *every* LLM path, no exceptions**, with `max_retries` and a hard failure (→ Nascente quarantine / descarte, not silent drop) when the model can't produce valid structure. Make "raw LLM output reached Rio without validation" an impossible code path, enforced by the client interface.
- **Ground the desmembramento prompt** wherever possible: feed the município's known official localities/distritos (IBGE/Mtur) into the prompt and ask DeepSeek to *classify/position* them rather than *invent* them. Tag every LLM-generated destino origem=40 + "pending validation" (PLANO already does this) and **never auto-promote origem=40 to Mar without human validation** — this is the hallucination firewall.
- **Require corroboration before Mar** for LLM-generated entities: a destino that exists *only* because DeepSeek listed it (no Places hit, no second source) must stay in DLQ until a human or a second source confirms.
- **Validate the validator with offline fixtures** of known-bad DeepSeek outputs (truncated JSON, extra prose, wrong types) so the test suite proves malformed output is caught.

**Warning signs:**
`instructor` retry/parse-failure rate climbs after an OpenRouter provider change; reviewers report "this praia doesn't exist"; community error reports cluster on origem=40 records; extracted fields with default/empty values at suspicious rates.

**Phase to address:** T1 (the LLM client interface that enforces validation-or-quarantine) + T2 (grounded desmembramento prompt + origem=40 hallucination firewall).

---

### Pitfall 5: WhatsApp number bans, throttling, and template rejection kill the Atrativos outreach lane

**What goes wrong:**
The Atrativos lane depends on automated WhatsApp outreach. Brazilian small-business owners receiving an unsolicited automated message from an unknown number are *exactly* the population that blocks/reports — which tanks the number's **quality rating (Green→Yellow→Red)**. Verified current Meta behavior: messaging limits start low (**250 unique recipients / rolling 24h** for a new business portfolio, **shared across all numbers in the portfolio**), quality is computed from block/report rate over the last 24h, and **if Red persists across rolling windows (~14+ days) Meta can suspend the number outright**. Separately, **Meta can review/pause/reject any template at any time**; promotional content in a "utility" template, bad placeholder formatting, or commerce-policy violations get templates rejected — and a rejected/paused template means the lane can't open conversations at all.

**Why it happens:**
Teams treat WhatsApp as "just an API" and blast outreach to maximize coverage, ignoring that Meta's trust system *punishes volume from cold numbers* and *rewards opt-in*. The 24h customer-service window is misunderstood: outside it, you can only send pre-approved templates, so a bot that "just replies" silently fails after 24h. Template content is written like marketing, triggering rejection.

**How to avoid:**
- **Human gate + volume ramp is non-negotiable** (PLANO already specifies `aguardando_consulta_whatsapp` + ramp). Start far below the 250/24h cap, grow only as quality stays Green. The dashboard ramp control must enforce a hard daily cap and refuse to exceed it.
- **Design the first message as a genuine, identified, opt-out-respecting outreach** ("Olá, aqui é a Norteia... responda SAIR para não receber"), and submit it as a *correctly categorized* template (utility/marketing per actual content) — never disguise marketing as utility.
- **Track quality rating as a first-class metric** (PLANO §B.7 mandates WhatsApp quality rating in observability). On Yellow, auto-throttle; on Red, auto-pause the lane before Meta suspends the number.
- **Respect the 24h window in the state machine.** The LangGraph flow must know whether it's inside or outside the window and only send templates outside it.
- **Have a backup number / portfolio plan** and never put the whole operation on a single number (portfolio-shared limits mean one bad number can drag the portfolio).

**Warning signs:**
Quality rating drops to Yellow; block/report rate rises; template review returns "rejected" or "paused"; reply rate < a few %; messages outside 24h window silently fail to deliver; messaging-limit tier not increasing despite volume.

**Phase to address:** T3 (state machine 24h-window awareness, ramp enforcement, quality-driven auto-throttle/pause) + Compliance (template categorization, opt-out) + T4 (quality-rating dashboard + ramp control).

---

### Pitfall 6: LGPD consent/opt-out gaps in the Atrativos/WhatsApp PII lane

**What goes wrong:**
The Atrativos lane collects and processes **personal data of business owners** (phone, WhatsApp, email, name, conversation content). Without a logged legal basis, identification, opt-out, and consent trail, every outreach is an LGPD violation. Specific failure modes: messaging someone who already said SAIR/opt-out (no suppression list); no record of *when/why* a contact was added or *what legal basis* applies; storing the full WhatsApp conversation indefinitely with no minimization/retention; using a contact harvested for "signal verification" to also do marketing.

**Why it happens:**
Compliance is treated as a checkbox bolted on at the end, not as data-model + state-machine constraints. The team conflates "publicly listed business phone" with "free to message however we like" — LGPD still applies to processing personal data even if the number is public. Opt-out is implemented as a UI nicety, not as an enforced suppression gate in the send path.

**How to avoid:**
- **Model consent/opt-out as a hard gate in the send path**, not a flag a human checks. The WhatsAppAgent must query a suppression list and refuse to send to any opted-out or never-consented contact — enforced in code, tested offline.
- **Log a consent/legal-basis record per contact** at the moment of first contact (PLANO §B.8 mandates this): legal basis, Norteia identity shown, timestamp, opt-out offered. This is the audit artifact a regulator asks for.
- **Data minimization + retention:** store only the PII the lane needs; define a retention/anonymization policy for conversation logs; don't retain phone numbers of descarte/closed attractions longer than needed.
- **Purpose limitation:** a contact gathered for signal verification is used only for that; document it.
- **Make opt-out idempotent and irreversible** (SAIR once = suppressed forever unless re-opted-in), and propagate suppression across the portfolio.

**Warning signs:**
No suppression-list lookup in the send-path tests; conversation logs with no associated consent record; a contact receives a second message after opting out; PII fields persisted on descarte records; no retention job.

**Phase to address:** Compliance (data model: consent log + suppression list + retention policy) + T3 (enforced send-path gate) — must land *before the first real WhatsApp message*, per PLANO Verification.

---

### Pitfall 7: 24/7 Celery operational failures — stuck queues, poison messages, beat duplication, non-idempotent retries

**What goes wrong:**
A continuously-running Celery+Redis fan-out-by-UF pipeline accumulates classic distributed-job failures: a **poison message** (one malformed payload) that crashes a worker, gets re-queued, crashes again — looping forever and blocking the queue. **Non-idempotent tasks** that, on retry after a partial failure, double-insert a Nascente record or push the same atrativo to Mar twice. **Celery beat duplication** (two beat instances, or a beat restart) firing the same UF sweep twice. **Stuck/invisible tasks** where a worker dies mid-task and the message is never acked nor redelivered (or redelivered after the visibility timeout to a *second* worker, causing concurrent double-processing). **Lost state-machine transitions** in the atrativo lane when two workers grab the same atrativo and race `discovered→contacts_found`.

**Why it happens:**
Celery's defaults are tuned for fire-and-forget web tasks, not 24/7 stateful pipelines. `acks_late` + retries without idempotency keys cause duplicates. Redis as broker has at-least-once + visibility-timeout semantics that surprise teams. Beat is a single point of duplication if run more than once. Nobody designs for "what happens when this exact task runs twice".

**How to avoid:**
- **Idempotency keys everywhere that writes:** Nascente ingest, Rio output, and Mar push must be idempotent by canonical key/`source_ref` (PLANO already mandates idempotent Mar push to norteia-api — extend the same discipline *inside* Brave). A retried task must be a no-op, not a duplicate.
- **Dead-letter / quarantine for poison messages:** after N failures, route the message to a quarantine table for human inspection — never infinite-retry. (Note: this is a *different* DLQ from the §7.6 review DLQ — name them distinctly to avoid confusion: e.g. `poison_quarantine` vs. `review_dlq`.)
- **Single beat instance**, enforced (leader lock / dedicated process), and make beat-fired sweeps idempotent so an accidental double-fire is harmless.
- **State-machine transitions guarded by row-level locks / optimistic concurrency** (`SELECT ... FOR UPDATE` or a version column on the atrativo) so two workers can't both transition the same record.
- **Tune visibility timeout > max task runtime**, set sane `task_time_limit`, and monitor queue depth + worker liveness (PLANO §B.7 mandates queue/worker metrics).

**Warning signs:**
A worker restart loop; queue depth grows while workers are idle (stuck/invisible tasks); duplicate Nascente rows or duplicate Mar pushes; an atrativo oscillating between states; the same UF swept twice in the logs; OOM on a worker that keeps retrying one payload.

**Phase to address:** T1 (idempotency keys, poison-quarantine, beat singleton, locking primitives, queue/worker observability) + T3 (atrativo state-machine concurrency guards).

---

### Pitfall 8: Cost blowups — unbounded LLM, Places, and embedding spend during the all-Brazil cold start

**What goes wrong:**
A cold start over *every* Brazilian município is the single largest spend event in the system's life: DesmembramentoAgent calls DeepSeek per Oferta Principal município; DiscoveryAgent sweeps Places per UF/município; every record gets embedded for dedup; WhatsApp/Sonnet runs per borderline atrativo. Without a budget guard, a retry storm (Pitfall 7) or a fan-out misconfiguration can multiply spend 10–100× overnight. `:nitro` routing optimizes throughput, not cost, so it can route to a *pricier* provider precisely when you're running the highest volume.

**Why it happens:**
Per-call cost feels trivial; multiplied by ~5,500 municípios × multiple sources × retries, it isn't. Teams add the cost-guard "later". Retries silently re-bill. `:nitro` cost variance is invisible until the bill arrives.

**How to avoid:**
- **USD cost guard is a hard circuit breaker, not a dashboard chart** (PLANO §B.7 mandates the guard — make it *enforcing*). Per-day and per-lane budget caps that *pause fan-out* when exceeded, with the `llm_generations` table tracking actual spend per call.
- **Cache aggressively:** embed once per record (Pitfall 3), cache Places Details per `place_id` *within ToS limits* (Pitfall 9), and never re-run desmembramento for an unchanged município.
- **Pin the cheapest acceptable provider explicitly** rather than letting `:nitro` pick; the PLANO's "pin slug + fallback" discipline should include a price-aware provider preference, and watch that `:nitro` doesn't route to an expensive provider under load.
- **Idempotent + deduped tasks** so retries don't re-bill (ties to Pitfall 7).
- **Cost-test the cold start on one state first** (PLANO's BA/RJ/SP/SC/CE/PE batching is the right shape) and extrapolate before going national.

**Warning signs:**
`llm_generations` daily total trending up faster than record throughput; spend per net-new Mar record rising; retry rate up; `:nitro` resolving to a high-cost provider in logs; Places call count > unique-place count (cache miss).

**Phase to address:** T1 (enforcing cost guard + `llm_generations` + caching layer) + T2/T3 (per-lane budget caps, single-state cold-start dry run).

---

### Pitfall 9: Google Places ToS violation — caching prohibited content beyond place_id

**What goes wrong:**
Verified current ToS: **you may store `place_id` indefinitely (refresh if >12 months old, free), but you must NOT pre-fetch, cache, or store other Places content** (name, address, phone, hours, reviews, coordinates) beyond limited allowances. The natural-but-wrong design is to store the full Places payload in Nascente and treat it as canonical — that's a ToS violation that can get the API key revoked, killing the entire Atrativos discovery lane.

**Why it happens:**
A data-collection pipeline's whole instinct is "store everything you fetch". The place_id exemption gets over-generalized to "Places data is cacheable". The distinction between "store place_id + your own first-party-validated data" vs. "cache Google's content" is subtle and easy to violate.

**How to avoid:**
- **Persist only `place_id` long-term.** Treat Places content as *transient signal* used to *inform* a record, then store **your own validated/derived** canonical data (the PLANO's "dado canônico é o validado / first-party" principle is exactly right — make it an enforced architectural rule, not a guideline).
- **Re-fetch by place_id when you need fresh content** (refresh place_id itself if >12mo, at no charge).
- **Document the data-flow boundary** in the Places client: what may be persisted (place_id, your derived fields) vs. what is transient (raw Google content). Encode it so a developer can't accidentally JSONB-dump the whole Places response into Nascente.
- **Call Places only in the collector** (PLANO already mandates this), never on norteia-api's hot path.

**Warning signs:**
Nascente rows containing full raw Google Places content (reviews text, formatted_address) persisted as canonical; the team relying on cached Places data weeks old as source of truth; a Google policy/compliance email.

**Phase to address:** T1 (Places client interface enforces the persistence boundary) + T3 (Discovery/ContactFinder/SignalAgent store place_id + derived only).

---

### Pitfall 10: Apify/Meta gray-area scraping and OTA partner gating treated as reliable load-bearing sources

**What goes wrong:**
The plan correctly marks IG/X-via-Apify as "best-effort, ToS gray" and OTA as "gated, optional". The pitfall is building logic that *depends* on these — e.g. an atrativo's score requiring an Apify signal, or the funnel assuming OTA price cross-check is always available. Meta's ToS prohibits automated scraping/DM; Apify scraping of IG can break or get blocked at any time, and **automated DM is explicitly out of scope** (Meta ToS). OTA partner onboarding can be rejected or revoked, removing the price signal. If the pipeline treats these as required inputs, a state machine stalls waiting for a signal that never comes.

**Why it happens:**
Optional/best-effort sources get wired in as if they were reliable because they're available during development. The legal gray status of Apify scraping is acknowledged in a doc but not reflected in code resilience.

**How to avoid:**
- **All gray/optional signals are strictly additive and non-blocking:** a record must be fully scoreable and progressable *without* Apify or OTA. SignalAgent treats them as `best_effort` with timeouts and graceful absence (PLANO already frames Apify as best-effort + Places as fallback — enforce "Places is sufficient alone").
- **Never automate IG/FB DM** (PLANO §B.8 — read-only signal only). Keep this as an architectural prohibition, not a TODO.
- **OTA is cross-check only, ticketed-only, and degrades silently** if the partner relationship lapses.
- **Per-source legal-risk note** (PLANO §B.8 mandates documenting scraping risk per source) — keep it current so the team knows which sources are disposable.

**Warning signs:**
An atrativo stuck because an Apify call failed; score logic that can't compute without an OTA price; any code path that sends an IG/FB DM; Apify returning empty/blocked and the lane halting instead of degrading.

**Phase to address:** T3 (SignalAgent best-effort/non-blocking design) + Compliance (per-source risk doc, no-automated-DM prohibition).

---

### Pitfall 11: Offline-test discipline erodes — real API calls leak into CI and Pact contract drifts from norteia-api

**What goes wrong:**
The 100%-offline, keyless-CI requirement (PLANO Parte C) is strict but *fragile*: it degrades one careless test at a time. A developer adds a "quick" test that hits OpenRouter/Places "just to check the real thing", CI starts needing a key, flakiness and cost creep in, and eventually nobody trusts the suite. Separately, the **Pact contract with norteia-api drifts**: the collector's Mar-push shape changes, the Pact isn't updated, and the Laravel consumer breaks at integration time — discovered late, across two repos.

**Why it happens:**
The offline boundary depends on *every* external touch going through a mockable client interface; one direct `httpx`/SDK call bypasses it. Pact drift happens because the two repos evolve independently and contract verification isn't a blocking gate on both sides.

**How to avoid:**
- **Enforce the network boundary architecturally:** every external call goes through a client interface (PLANO already mandates this for Places/OTA/Apify/WhatsApp/Mtur/NotebookLM/NorteiaApi). Add a **CI guard that fails if a test makes a real outbound network call** (e.g. block sockets in the test harness / `pytest-socket`), so a leak is a hard failure, not a silent dependency.
- **Externals are opt-in by flag only**, and the default keyless CI proves the rule by having no keys to leak through.
- **Pact: collector publishes the contract, norteia-api verifies it as a blocking gate.** A contract change must break one side's CI loudly. The PLANO's "contrato de ingestão estável" as T5 prerequisite is right — make the Pact the *single source of truth* for the Mar-push shape, versioned.
- **Fixtures over live recording where possible** (respx/VCR.py for Places/OTA/Apify/Mtur, LLM fake, WhatsApp webhook fixture) — and review recorded cassettes so they don't embed secrets or go stale silently.

**Warning signs:**
CI starts requiring an API key; a test is flaky/slow in a way that smells like a network call; "skip this test in CI" comments appear; norteia-api integration breaks on a field the collector "always sent"; Pact verification not run on PRs.

**Phase to address:** T1 (client-interface boundary + no-real-network CI guard + fixture infra) + T5 (Pact as blocking, versioned contract on both repos) + cross-cutting in every trilha that adds an external touch.

---

### Pitfall 12: OpenRouter slug/variant instability silently changes the extraction backend

**What goes wrong:**
Verified current behavior: **`:nitro` is an active variant** (sorts providers by throughput, equivalent to `provider.sort: throughput`) — but model *slugs and provider availability shift over time* (the deprecated `:online` variant and the appearance of new `deepseek-v4-flash`/`deepseek-v4-pro` slugs confirm churn). The PLANO already flags that `deepseek-v4-flash` "pode não existir" and pins a fallback. The pitfall: an un-pinned or wrongly-pinned slug, or relying on `:nitro` to route to a *specific* provider, means a silent backend swap — different provider, different JSON-adherence quality (Pitfall 4), different price (Pitfall 8), different `data_collection` policy (Pitfall 13) — with no test catching it because the test uses a fake LLM.

**Why it happens:**
OpenRouter abstracts away *which* provider serves a request; `:nitro`/Balanced/Exacto change that choice dynamically. A model slug that worked in dev silently 404s or reroutes in prod. Because offline tests mock the LLM, the *real* slug/provider config is never exercised by CI — only in production.

**How to avoid:**
- **Pin the slug in config, centralized, with an explicit fallback chain** (PLANO already specifies `deepseek/deepseek-chat` / `deepseek-v3.2` fallback — keep it). Stamp the *actual resolved* model+provider into the `llm_generations` table per call so a silent swap is visible after the fact.
- **A startup/health-check probe** (opt-in, behind the real-API flag) that confirms the configured slug resolves and the provider honors `data_collection: deny` — run on deploy, not in keyless CI.
- **Pin provider preference explicitly** if a specific provider's quality/price/privacy matters, rather than trusting `:nitro` to pick.
- **Alert on resolved-provider change** in observability.

**Warning signs:**
`instructor` parse-failure rate jumps after a deploy with no code change (provider swapped); `llm_generations` shows a different resolved provider/model than configured; a 404/"model not found" in prod logs; cost-per-call steps up.

**Phase to address:** T1 (centralized pinned-slug config + resolved-provider logging + deploy-time probe) + T2/T3 (the lanes that consume DeepSeek).

---

### Pitfall 13: Paid ≠ private — LLM data-collection/privacy on the paid DeepSeek backend

**What goes wrong:**
Teams assume "we pay for the API, so they don't train on our data". Not automatically true. Territorial data plus — in the WhatsApp lane — **business-owner PII (names, phone numbers, conversation content)** could be retained/trained-on by the LLM provider unless data-collection is explicitly denied. This is both a privacy/LGPD exposure (Pitfall 6) and a competitive-data leak.

**Why it happens:**
The default routing on aggregators may allow providers that log/train; the `data_collection: deny` setting and account-level privacy settings are easy to forget. The PLANO explicitly warns "Pago ≠ não treina" — but a config drift or a `:nitro` reroute to a non-compliant provider re-opens the hole.

**How to avoid:**
- **Set `provider.data_collection: deny`** on every request *and* the account-level privacy setting (PLANO §B.6 mandates both). Make it part of the centralized LLM client config, not per-call discretion.
- **Restrict routing to providers that honor the policy** — combine with Pitfall 12's pinned provider preference so `:nitro` can't route to a logging provider.
- **Minimize PII sent to the backend LLM:** the DeepSeek extraction of WhatsApp replies should receive only what's needed (existe?/funcionando?/horários/valor), not the full conversation with names where avoidable.
- **Verify at deploy** (the Pitfall 12 probe) that the resolved provider honors deny.

**Warning signs:**
A request without `data_collection: deny`; `:nitro` routing to a provider known to log; full PII conversation payloads in the DeepSeek call; no account-level privacy setting confirmed.

**Phase to address:** Compliance + T1 (LLM client enforces deny + provider allow-list) + T3 (PII minimization in WhatsApp-reply extraction).

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Skip the score-distribution simulation; trust 50/85 boundaries as-is | Ship T1 faster | DLQ landfill (Pitfall 1); reviewers overwhelmed; gate degrades to approve-everything | Never — a 1-day histogram pays for itself |
| Dedup on name-embedding similarity alone, no territorial blocking | Less plumbing in Rio | False merges (Trancoso/Porto Seguro), missed homonym dups, ToS-irrelevant cost (Pitfall 3) | Never for territorial entities |
| Lenient/optional second-layer validation on a "trusted" LLM path | Fewer retries, lower latency | Malformed + hallucinated records reach Mar (Pitfall 4) | Never — validate-or-quarantine on all paths |
| One WhatsApp number, no ramp, blast outreach | Faster coverage | Number ban, portfolio-wide quality damage (Pitfall 5) | Never — ramp + gate are load-bearing |
| Store full raw Places payload as canonical in Nascente | Simple "store everything" ETL | Google ToS violation, key revocation (Pitfall 9) | Never — place_id + derived only |
| Make Apify/OTA signals required inputs | Richer score early | Lane stalls when gray-source breaks (Pitfall 10) | Only behind a non-blocking best-effort wrapper |
| Non-idempotent Celery tasks "we'll add keys later" | Faster T1 | Duplicate Nascente/Mar rows, double-billing on retries (Pitfall 7, 8) | Never in a 24/7 at-least-once pipeline |
| One "quick" test hitting a real API | Confidence it works end-to-end | Offline discipline erodes, CI needs keys, flakiness (Pitfall 11) | Only behind the opt-in real-API flag, never in default CI |
| Add LGPD consent log "after the pipeline works" | Faster T3 demo | Can't legally send a single real message; retrofit across data model (Pitfall 6) | Never — must precede first real WhatsApp message |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| WhatsApp Business API | Treating it as a plain send API; ignoring 24h window + quality rating | Window-aware state machine; quality-driven throttle; ramp under the 250/24h cold-tier cap (portfolio-shared) |
| WhatsApp templates | Marketing content in a "utility" template | Categorize honestly; expect review/pause/reject; have approved fallbacks before T3 ships |
| Google Places (New) | Caching full content as canonical | Store place_id only (refresh >12mo, free); Places content is transient signal → derive first-party canonical |
| OpenRouter / DeepSeek | Un-pinned slug; trusting `:nitro` to pick a specific provider | Pin slug + fallback chain; log resolved provider/model; deploy-time probe; explicit provider preference |
| OpenRouter privacy | Assuming paid = no training | `provider.data_collection: deny` per-request + account setting + provider allow-list |
| Apify (IG/X) | Depending on it as a required signal; risking automated DM | Best-effort, non-blocking, timeout-guarded; read-only, never DM (Meta ToS) |
| OTA (Viator/GYG/Booking) | Assuming price cross-check always available | Ticketed-only, cross-check only, degrades silently if partner gating lapses |
| Celery + Redis | Default at-least-once semantics + non-idempotent tasks | Idempotency keys on all writes; visibility-timeout > task runtime; poison-quarantine; single beat |
| norteia-api (Pact) | Two repos drift; contract not verified on PRs | Collector publishes Pact; api verifies as blocking gate; versioned contract = single source of truth for Mar shape |
| pgvector (HNSW) | Treating approximate search as exhaustive | Measure recall at chosen `ef_search`; block first, exact-scan the small candidate set |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Embedding every record on every Rio pass | Embedding bill grows super-linearly; slow Rio | Embed once at ingest, cache vector, re-embed only on name change | At national scale (~5,500 municípios × sub-destinos × passes) |
| pgvector full-recall expectation on a huge index | Missed duplicates that "should" have matched | Block by territorial key → small candidate set → high `ef_search` or exact scan; measure recall | As the Mar/Rio vector table grows past local-test sizes |
| DLQ as unbounded unprioritized queue | Reviewer throughput < intake; depth grows forever | Batch-by-state review unit; auto-promote high-confidence; drain-rate SLO | Immediately at cold start (Pitfall 1) |
| Celery fan-out by UF with no concurrency/budget caps | Cost spike + queue saturation on national sweep | Per-lane budget caps that pause fan-out; bounded concurrency | The all-BR cold start (Pitfall 8) |
| Per-record (not per-batch) Places/LLM calls | Call count ≫ unique entities; cost + rate limits | Cache by place_id; dedupe before calling; batch where API allows | National discovery sweep |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Service token for Mar push leaked or long-lived | Anyone can write canonical territorial data into norteia-api | Sanctum service ability scoped narrowly; rotate; never in client/dashboard; collector-only |
| Community "reportar erro" webhook unauthenticated | Attacker reopens/poisons Mar records via DLQ | Authenticate the webhook back to the collector; validate payload; rate-limit |
| Full business-owner PII sent to backend LLM / persisted broadly | Privacy/LGPD breach; data leak via provider training | Minimize PII to LLM; `data_collection: deny`; retention policy (Pitfalls 6, 13) |
| API keys (Places/OpenRouter/Anthropic/WhatsApp) in repo or CI logs | Key theft → cost/ToS/lane loss | Keyless CI (already required); secrets manager; never log resolved keys |
| Dashboard Bearer-header auth without scope on destructive actions (approve→Mar) | Unauthorized promotion of records to canonical/published | Authorize approve/reject/reprocess actions per role; audit-log every one (§B.7) |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| DLQ review one-row-at-a-time | Reviewer fatigue; rubber-stamping → gaming (Pitfall 2) | Batch-by-state review of a município's desmembramento as one unit |
| "Approve" button with no "what did you verify?" | Bare clicks inflate validação humana=100 falsely | Structured verification capture; only verified dimensions feed score |
| No visibility into *why* a record is in DLQ | Reviewer can't judge; slow, inconsistent decisions | Show §7.6 score per criterion + signals + Nascente payload + WhatsApp log (PLANO §B.7 already specs this — don't cut it) |
| WhatsApp gate without ramp/quality context | Operator over-approves contacts → number ban | Show quality rating + remaining daily cap + ramp state on the gate UI |
| No funnel/cost visibility per UF/source | Can't spot a state's cold-start cost blowup or a stuck lane | Funnels + Cost&LLM views per UF/source (PLANO §B.7) |

## "Looks Done But Isn't" Checklist

- [ ] **Score engine:** Often missing the *distribution simulation* — verify the score histogram over representative intake doesn't dump 80%+ into DLQ.
- [ ] **Dedup:** Often missing *territorial-key blocking* and *measured recall* — verify Trancoso doesn't merge into Porto Seguro and homonym municípios in different UFs never become merge candidates; record the achieved pgvector recall number.
- [ ] **LLM extraction:** Often missing *validate-or-quarantine on every path* — verify there is no code path where raw LLM output reaches Rio unvalidated; test with known-bad DeepSeek fixtures.
- [ ] **Desmembramento:** Often missing the *origem=40 hallucination firewall* — verify LLM-only destinos can't reach Mar without human validation or a second source.
- [ ] **WhatsApp lane:** Often missing *24h-window awareness, quality-driven auto-pause, and enforced opt-out suppression* — verify the send path refuses opted-out contacts and auto-pauses on Red.
- [ ] **LGPD:** Often missing *consent log written at first contact* and *retention job* — verify a consent record exists before any send and PII isn't retained on descarte.
- [ ] **Celery:** Often missing *idempotency keys, poison-quarantine, single-beat enforcement* — verify a retried task is a no-op and a poison message can't loop forever.
- [ ] **Cost guard:** Often "a chart, not a brake" — verify the USD guard actually *pauses fan-out* when a cap is hit.
- [ ] **Places:** Often missing the *persistence boundary* — verify only place_id (+ derived first-party) is stored long-term, not raw Google content.
- [ ] **Offline tests:** Often missing the *no-real-network CI guard* — verify CI fails (not silently passes) if a test attempts an outbound call, and runs with zero keys.
- [ ] **Pact:** Often missing *blocking verification on both repos* — verify a Mar-shape change breaks CI loudly.
- [ ] **OpenRouter:** Often missing *resolved-provider logging + deploy-time probe* — verify `llm_generations` records the actual model/provider served and `data_collection: deny` is honored.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| DLQ landfill (1) | MEDIUM | Pause intake fan-out; re-tune band boundaries against the real histogram; add batch + auto-promote; re-run Rio over backlog |
| Calibration drift / gaming (2) | MEDIUM–HIGH | Re-score golden set per `score_version`; re-open suspect Mar records via the report-error/reprocess path; add per-reviewer audit |
| Bad dedup merges (3) | HIGH | Hard — un-merging canonical records is painful; recover via versioning + provenance to split; prevention >> cure |
| Hallucinated destinos in Mar (4) | MEDIUM–HIGH | Use `visibility=hidden`/`flagged` in norteia-api to pull them fast; re-open origem=40 records to DLQ; add corroboration gate |
| WhatsApp number ban (5) | HIGH | Number may be unrecoverable; switch to backup number/portfolio; fix templates + ramp; appeal to Meta (slow, uncertain) |
| LGPD gap discovered (6) | HIGH | Stop all sends; backfill consent/legal-basis or purge non-compliant contacts; add suppression + retention before resuming |
| Celery duplication/poison (7) | LOW–MEDIUM | Add idempotency keys + dedupe downstream; quarantine poison; reconcile duplicate Nascente/Mar rows |
| Cost blowup (8) | LOW | Hit the circuit breaker; cap per-lane; cache; resume after dry-run on one state |
| Places ToS violation (9) | MEDIUM–HIGH | Purge cached prohibited content; switch to place_id + re-fetch model; respond to Google before key revocation |
| Offline discipline / Pact drift (11) | LOW–MEDIUM | Add network-block + Pact-blocking gates; quarantine leaky tests; re-sync contract across repos |
| Silent provider swap (12/13) | LOW | Re-pin slug + provider; assert `data_collection: deny`; backfill `llm_generations` provenance |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| 1 DLQ landfill | T1 (score sim + calibrable bands) + T4 (batch UI) + T2 | Score histogram on representative intake; DLQ drain-rate SLO met |
| 2 Calibration drift / gaming | T1 (score versioning, golden set) + T4 (verification capture) | Golden-set re-score stable across versions; reviewer approval-rate < ~100% |
| 3 Dedup failures | T1 (blocking + measured recall + cached embeddings) + T2 (hierarchy-aware) | Trancoso≠Porto Seguro test; homonym-UF non-candidacy test; recorded recall |
| 4 LLM schema/hallucination | T1 (validate-or-quarantine client) + T2 (grounded prompt + origem=40 firewall) | No unvalidated path (test with bad fixtures); origem=40 can't reach Mar unaided |
| 5 WhatsApp bans/templates | T3 (window-aware FSM, ramp, auto-pause) + Compliance + T4 | Send refuses outside-window non-template; auto-pause on Red; ramp cap enforced |
| 6 LGPD consent/opt-out | Compliance (data model) + T3 (send-path gate) | Send-path refuses opted-out/never-consented; consent record precedes first send |
| 7 Celery operational | T1 (idempotency, poison-quarantine, beat singleton, locks) + T3 (FSM locks) | Retried task = no-op; poison routed to quarantine; no double UF sweep |
| 8 Cost blowup | T1 (enforcing guard + caching) + T2/T3 (per-lane caps, dry run) | Guard pauses fan-out at cap; single-state cold-start cost within budget |
| 9 Places ToS | T1 (client persistence boundary) + T3 (agents store place_id+derived) | No raw Google content persisted as canonical; place_id refresh path exists |
| 10 Gray scraping / OTA gating | T3 (best-effort non-blocking) + Compliance (per-source risk doc, no DM) | Record scoreable without Apify/OTA; no IG/FB DM code path |
| 11 Offline / Pact drift | T1 (network-block CI + fixtures) + T5 (Pact blocking) | Real network call fails CI; CI keyless; Mar-shape change breaks Pact loudly |
| 12 Slug/variant instability | T1 (pinned slug + resolved-provider logging + probe) + T2/T3 | `llm_generations` logs resolved model/provider; deploy probe passes |
| 13 LLM privacy (paid≠private) | Compliance + T1 (deny + allow-list) + T3 (PII minimization) | `data_collection: deny` on every request; minimal PII to backend LLM |

## Sources

- WhatsApp Business Platform — messaging limits (250/24h new portfolio, portfolio-shared), quality rating (Green/Yellow/Red from block/report rate over 24h), template review/pause/reject, Red→~14-day suspension, opt-in/opt-out requirements. [Meta for Developers — Messaging Limits](https://developers.facebook.com/docs/whatsapp/messaging-limits/), [WhatsApp Business Messaging Policy](https://whatsappbusiness.com/policy/), [Infobip — Template compliance](https://www.infobip.com/docs/whatsapp/compliance/template-compliance), [getkanal — Quality Rating explained](https://getkanal.com/blog/whatsapp-business-quality-rating-explained) (HIGH — official + corroborating vendor docs)
- Google Places API (New) — caching prohibited except place_id; place_id storable indefinitely, refresh free if >12 months; first-party validated data is the canonical store. [Google — Places API Policies](https://developers.google.com/maps/documentation/places/web-service/policies), [Google — Place IDs](https://developers.google.com/maps/documentation/places/web-service/place-id), [Google Maps Platform Service Terms](https://cloud.google.com/maps-platform/terms/maps-service-terms) (HIGH — official)
- OpenRouter — `:nitro` active (sorts by throughput, ≡ `provider.sort: throughput`); `:online` deprecated; new DeepSeek V4 Flash/Pro slugs + V3.2 confirm slug churn; provider routing (Balanced/Nitro/Exacto) abstracts served provider; `data_collection` setting. [OpenRouter — Nitro variant](https://openrouter.ai/docs/guides/routing/model-variants/nitro), [OpenRouter — Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection), [OpenRouter — DeepSeek V3.2](https://openrouter.ai/deepseek/deepseek-v3.2) (HIGH — official)
- pgvector — HNSW is approximate (may miss matches vs. exact); `ef_search` is the query-time recall/latency knob; measure recall, block before searching. [pgvector README](https://github.com/pgvector/pgvector), [Neon — Optimize pgvector search](https://neon.com/docs/ai/ai-vector-search-optimization) (HIGH — official + vendor)
- Scoring-calibration, DLQ-landfill, Celery 24/7 operational, and cost/cold-start pitfalls: domain reasoning over the PLANO §7.6 weights + general data-pipeline/distributed-job engineering practice (MEDIUM — not yet project-measured).

---
*Pitfalls research for: 24/7 LLM-assisted territorial data-collection + reliability-scoring pipeline (Norteia Brave)*
*Researched: 2026-06-11*
