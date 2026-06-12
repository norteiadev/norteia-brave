# Phase 3: Atrativos Lane (WhatsApp + Compliance) - Context

**Gathered:** 2026-06-12
**Status:** Ready for planning

> Captured in `--auto` mode: gray areas auto-resolved with the research-backed recommended option for each. Every decision below is a default downstream agents may refine during research/planning — none is a hard user lock except where it restates a PROJECT.md Key Decision or a Phase 1/2 locked decision carried forward. This is the **hardest, riskiest phase** (durable FSM, LLM conversation, BSP/LGPD, most external deps); a focused research design pass is expected (see Phase 3 research flag in STATE.md).

<domain>
## Phase Boundary

Deliver the **Atrativos collection lane** end-to-end on top of the proven Phase 1 core and the Phase 2 Mar destinos: an atrativo advances through a **persisted, resumable sub-state machine** — `discovered` (parent destino resolved from Mar) → `contacts_found` → `signals_gathered` → (§7.6 score) → `[borderline <85%]` `aguardando_consulta_whatsapp` (human gate) → `whatsapp_in_progress` (automated owner-validation outreach) → re-score → **Mar/DLQ** — with **LGPD and WhatsApp BSP enforced as hard, code-enforced, offline-tested send-path gates that block before the first real message**.

**In scope:** DiscoveryAgent (Places sweep + gov → DeepSeek map → schema → Nascente; parent-destino resolution from Mar; `place_id` cache), ContactFinderAgent (Places Details + site/IG-FB/email), SignalAgent (`business_status`/`weekday_text`/`reviews[].publishTime`; Apify IG/X best-effort), the `sub_state` FSM persisted across worker restarts, the human WhatsApp gate FastAPI endpoint + volume ramp, the WhatsAppAgent automated outreach (Sonnet PT-BR ask + DeepSeek extract via LangGraph; n8n thin transport), owner-validation → re-score → Mar/DLQ, the LGPD + BSP compliance gates, real client impls + fakes for Places/OTA/Apify/WhatsApp, and an offline test suite proving all six gates. (Requirements ATR-01..06, COMP-01..03.)

**Out of scope (other phases):** the Next.js dashboard UI that drives the WhatsApp gate / conversations / funnels (Phase 4 — this phase ships the FastAPI surface, not the views), real Places/Apify/WhatsApp/OpenRouter/Anthropic network calls in the default suite (opt-in flag only), any change to the frozen Phase 1 core or the Pact contract (score engine, routing, Mar service, `push_attraction` are reused, not modified — extend behind their existing seams), and the Destinos lane (Phase 2, complete — this phase only *reads* its Mar output for parent resolution).
</domain>

<decisions>
## Implementation Decisions

### Sub-state machine, orchestration & durability
- **D-01:** **Stay on Celery + Redis with `celery-redbeat`; do NOT adopt Temporal this milestone.** Phase 3 was flagged as the trigger to *re-evaluate* Temporal (D-06 carried from Phase 1); the re-evaluation outcome is **defer** — model the FSM as idempotent Celery tasks keyed off the existing `RioRecord.sub_state` column (already present, `String(64)`, null for Destinos). Each transition is an idempotent task that reads `sub_state`, does its work, advances `sub_state`, and is safe to replay after a worker restart. The day-scale human-gate wait is held as queue state (record sits in `aguardando_consulta_whatsapp`), not a blocked worker. Keep orchestration behind the existing interface so a future Temporal swap stays contained. **Re-open the Temporal decision only if** lost-progress-on-restart or multi-day-timer plumbing proves painful in practice.
- **D-02:** **`sub_state` is the single source of truth for FSM position**, advanced by supersession-safe writes (Phase 1 D-03 pattern); transitions write an audit row (`write_audit`, actor = agent name / steward). The canonical FSM values are exactly those in ATR-01: `discovered → contacts_found → signals_gathered → aguardando_consulta_whatsapp → whatsapp_in_progress` (terminal routing stays in the existing `routing` column: `mar`/`dlq`/`descarte`).

### Producers (Discovery / ContactFinder / Signal)
- **D-03:** **Parent destino resolution from Mar is a hard precondition.** DiscoveryAgent resolves the parent destino via the existing territorial-key match (UF + município, Phase 1 D-07) against **Mar** (canonical, ≥85% or DLQ-approved). **If no parent destino is in Mar, the atrativo is NOT ingested** — log + audit the skip with a reason (`parent_destino_absent`) and let it be retried on a later sweep once the destino lands. This keeps Mar's atrativo→destino reference always resolvable (mirrors the Phase 2 ordering invariant: destinos precede atrativos).
- **D-04:** **Persist only Google `place_id` as cache (COMP-03 / Phase 1 D-17).** Canonical atrativo data is the first-party validated record; `place_id` rides in the Nascente payload as a cache key (re-fetch / dedup), never as the canonical identity. DiscoveryAgent maps Places → `DeepSeek` (instructor + Mode.Tools, D-09 carried) → an `AtrativoResult` Pydantic schema → `store_raw`, behind `PlacesClientProtocol` + `LLMClientProtocol` (both faked in the default suite).
- **D-05:** **SignalAgent maps Places fields to §7.6 inputs deterministically:** `business_status ∈ {CLOSED_PERMANENTLY, CLOSED_TEMPORARILY}` is a **hard pre-score `descarte`** (no point scoring a dead place); `reviews[].publishTime ≤ 30 days ⇒ funcionando` raises the **atualidade** criterion; `weekday_text` populates hours (completude). **Apify IG/X is best-effort and non-blocking** — a failure/timeout from `ApifyClientProtocol.scrape_ig` degrades the corroboração signal but never fails the record (Meta-ToS gray area: read-only signal, no automated DM). OTA price-check stays an optional corroboration signal for ticketed attractions only.

### Human WhatsApp gate + volume ramp
- **D-06:** **The gate is a FastAPI endpoint mirroring the Phase 2 DLQ steward pattern** (`brave/api/routers/`): a queue endpoint lists `sub_state = aguardando_consulta_whatsapp` borderline (<85%) atrativos; an approve action flips the record to `whatsapp_in_progress` and enqueues the outreach task; a reject action routes to `dlq`/`descarte`. Same "dispatch Celery task, fall back to synchronous in tests/dev without a broker" shape as the DLQ validate endpoint. **No automated outreach is dispatched without a human approve.**
- **D-07:** **The volume ramp is a Redis counter reusing the Phase 1 cost-guard counter pattern** (atomic INCR + ceiling check, daily/UTC key, crash-safe TTL — apply the CR-04 reserve-before-call hardening lesson). The ramp caps approved outreach per window (per-UF or global, calibrable in `pydantic-settings`); breaching the cap blocks new approvals/sends, not in-flight conversations. Ramp limits live in config, not code constants.

### WhatsApp conversation (outreach) — LLM split & transport
- **D-08:** **All conversation logic lives in LangGraph code; n8n is thin transport only** (per CLAUDE.md hard constraint — n8n is un-unit-testable, so it holds zero logic). The WhatsAppAgent is a LangGraph graph: **Claude Sonnet 4.5 via the native Anthropic SDK** generates the PT-BR turns (identifies Norteia + states opt-out, per LGPD), **DeepSeek (instructor + Mode.Tools) extracts** the structured answers (`existe?` / `funcionando?` / `horários` / `valor`) behind the mandatory 2nd-layer validator (Phase 1 D-11). The graph state (turn history, extraction, opt-out) persists so a multi-day conversation survives restarts (ties to D-01/D-02).
- **D-09:** **WhatsApp transport is Twilio at launch, behind the existing `WhatsAppClientProtocol.send_template`** (per CLAUDE.md / STACK.md: Twilio ships faster; Meta Cloud direct is the cost-optimized end-state, migrated behind the same interface later). Faked in the default suite (`tests/fakes/`), real only by opt-in flag.

### Owner-validation → re-score → Mar/DLQ
- **D-10:** **Owner confirmation is a validation signal that feeds the existing re-score path — no new scoring branch.** A successful owner-validation outreach (`existe? = sim`, `funcionando? = sim`) raises a validation criterion on the normalized record and calls the existing `reprocess_record` (Phase 1) → if routing crosses to `mar`, promote via `promote_to_mar` and push via the **`push_attraction`** Celery task (mirror Phase 2's `push_destination_task`; idempotent by `source_ref`; frozen Pact shape). Mirrors Phase 2 D-07 (steward validate → re-score → promote), substituting *owner* validation for *steward* validation. A negative/no-answer outcome leaves the record in DLQ.

### Compliance gates (LGPD + BSP) — hard send-path enforcement
- **D-11:** **LGPD + BSP are a single hard send-path gate function that every outbound message passes through, code-enforced and offline-tested, blocking before the first real send (COMP-01/02).** The gate, evaluated immediately before `WhatsAppClientProtocol.send_template`, asserts: **legal basis recorded**, **Norteia identification present in the message**, **opt-out honored** (a consent/opt-out log row exists and is not opted-out), **approved BSP template used**, **24h customer-service window respected** (template vs free-form), **human gate + ramp satisfied** (D-06/D-07), and **data minimization**. A failed assertion raises and **blocks the send** (no message leaves). Auto-pause: a degraded WhatsApp **quality rating** signal pauses the lane. A `consent`/opt-out log table (or audit-backed log) records legal basis + opt-out per contact. Every gate condition has an offline unit test that proves it blocks.

### Claude's Discretion
- The `AtrativoResult` / conversation-state Pydantic schemas, the exact FSM task topology & Celery queue/task names, the consent-log table DDL vs reusing the audit log, the precise ramp window (per-UF vs global) and its default cap, FastAPI request/response models for the gate/queue endpoints, the LangGraph node layout and prompt text, and test-fixture structure are left to research/planning. Decisions above set direction, not signatures. **The Phase 3 research flag (re-verify Twilio-vs-Meta-Cloud pricing/policy/template categorization/rate caps at build time; design the durable-FSM + multi-day LangGraph conversation) MUST be honored in the research pass.**
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Primary plan & framework
- `docs/PLANO-BRAVE.md` — full plan; **§B.4 Lane de Atrativos** (Discovery/ContactFinder/Signal/Gate/WhatsAppAgent), §B.6 LLM split (Sonnet conversation / DeepSeek extraction) + DeepSeek/instructor cautions, §B.7 observability (quality rating), **§D compliance** (LGPD base legal/opt-out/minimização; BSP templates/janela 24h/gate+ramp/opt-out), §C testability (n8n thin, network behind clients, no real externals by default). Authoritative for this milestone.
- `docs/brave-visao-geral.pdf` — Brave overview (visual companion).
- Note: the §-numbers (§7.6 score, §7.7–7.8 invalidation, §15.7 monitor/audit) cite `docs/Norteia_MVP_Documentacao_Tecnica_v1.md` which lives in the **norteia-api** repo, not here — treat the values quoted in PLANO-BRAVE.md as canonical for this repo.

### Phase 1 & 2 build (reuse, do not modify)
- `.planning/phases/01-brave-core-score-gate-boundary-contract/01-CONTEXT.md` — locked D-01..D-21 (carried forward here: supersession D-03, dedup/territorial-key D-07, instructor/Mode.Tools D-09, validate-or-quarantine D-11, score config D-12/D-13, Mar push + Pact D-15/D-16, place_id cache D-17, package boundaries D-18, cost-guard counter D-20).
- `.planning/phases/02-destinos-lane/02-CONTEXT.md` — steward validate → re-score → promote → push pattern (D-07/D-08/D-09); the FastAPI steward-endpoint shape this phase's WhatsApp gate mirrors.
- `brave/clients/base.py` — `PlacesClientProtocol`, `OTAClientProtocol`, `ApifyClientProtocol`, `WhatsAppClientProtocol`, `LLMClientProtocol`, `NorteiaApiClientProtocol.push_attraction` (the seams this phase fills/uses; real impls + fakes ship here).
- `brave/core/models.py` — `RioRecord.sub_state` (already present; the FSM column), `routing`/`dlq_reason`, supersession fields.
- `brave/core/nascente/service.py` — `store_raw` (idempotent + supersession) — DiscoveryAgent writes here.
- `brave/core/rio/routing.py` — `process_nascente_record`, `route_by_score`, `reprocess_record` — atrativo path + owner-validation re-score reuse these unchanged.
- `brave/core/mar/service.py` — `promote_to_mar` (idempotent, provenance) — promotion target.
- `brave/lanes/base.py` — `LaneProtocol.produce(uf)`; `brave/lanes/destinos/` is the structural template for `brave/lanes/atrativos/`.
- `brave/config/settings.py` — `anthropic_api_key` (Sonnet, already wired, CR-02 no-alias), `ScoreConfig`, LLM/cost-guard config; extend with WhatsApp/BSP/ramp settings.
- `tests/fakes/` — `fake_llm.py`, `fake_norteia_api.py` (extend; add Places/Apify/WhatsApp/OTA fakes).

### Research (this project)
- `.planning/research/STACK.md` — LangGraph FSM, Sonnet native SDK + instructor/DeepSeek, Twilio-vs-Meta-Cloud BSP notes, google-maps-places (New), Apify, **Temporal-defer rationale**.
- `.planning/research/PITFALLS.md` — Places ToS / place_id persistence, OpenRouter slug churn, cost-guard atomicity (informs ramp D-07), BSP policy volatility.
- `.planning/research/ARCHITECTURE.md` — medallion mapping, sub_state FSM, package boundaries, orchestration-behind-interface (Temporal swap containment).

### Project planning
- `.planning/ROADMAP.md` §"Phase 3" — goal + 5 success criteria.
- `.planning/REQUIREMENTS.md` — ATR-01..06, COMP-01..03, TEST IDs.
- `.planning/PROJECT.md` — Core Value, Key Decisions (LLM split: Sonnet conversation / DeepSeek extraction; compliance-as-hard-gate; Destinos-precedes-Atrativos).
- `.planning/STATE.md` — **Phase 3 research flag** (re-verify BSP pricing/policy/limits at build time; durable-FSM + LangGraph design pass) and the CR-04 cost-guard-atomicity lesson (apply to the ramp counter).
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **All five external client Protocols this phase needs already exist** in `brave/clients/base.py` (Places, OTA, Apify, WhatsApp, LLM) plus `NorteiaApiClientProtocol.push_attraction` — this phase writes their **real implementations + fakes**, not new interfaces.
- **`RioRecord.sub_state` column already exists** (`String(64)`, null for Destinos) — the FSM persistence substrate is in place; no migration needed for the column itself.
- **Full Phase 1 core reusable as-is**: `store_raw`, `process_nascente_record` + `route_by_score` + `reprocess_record`, `promote_to_mar`, `compute_score` + `ScoreConfig`, `write_audit`, the cost-guard Redis counter (the ramp's template), `llm_tracker`.
- **Phase 2 DLQ steward endpoints** (`brave/api/routers/dlq.py`) are the structural template for the WhatsApp gate endpoint (queue list + approve/reject + audit + Celery-or-sync fallback).
- **`brave/lanes/destinos/`** is the package-layout template for `brave/lanes/atrativos/`.
- **Sonnet is already wired**: `settings.anthropic_api_key` (BRAVE_LLM_ANTHROPIC_API_KEY, no alias per CR-02) — the conversation LLM key path exists, stubbed since Phase 1.

### Established Patterns
- **Score inputs flow through `RioRecord.normalized` `*_value` fields** — agents set them in the Nascente payload; no score-engine change for owner-validation (D-10) — it raises a criterion then calls existing `reprocess_record`.
- **Supersession versioning** on every layer — FSM transitions and re-score append rows, keeping the active-`source_ref` partial-unique index valid.
- **Steward endpoints dispatch a Celery task and fall back to synchronous** when no broker — reuse for the gate approve + outreach dispatch + push.
- **D-18 boundary:** lanes import core, never the reverse; atrativos code lives under `brave/lanes/atrativos/` implementing `LaneProtocol.produce(uf)`. Lane-to-lane coupling forbidden — the atrativo↔destino link crosses only through **Mar data** (D-03 reads Mar; no import of `brave/lanes/destinos/`).
- **validate-or-quarantine 2nd-layer** (Phase 1 D-11) wraps every LLM output — DeepSeek extraction in the conversation graph must pass it.
- **Cost-guard reserve-before-call atomicity (CR-04 lesson)** — apply the same atomic-INCR + crash-safe-TTL + UTC-key discipline to the ramp counter (D-07).

### Integration Points
- **DiscoveryAgent → Mar** (read, parent resolution by territorial key) → **Nascente** `store_raw`.
- **Producers → `PlacesClientProtocol` / `ApifyClientProtocol` / `OTAClientProtocol`** (all faked by default).
- **WhatsApp gate (FastAPI) → Celery outreach task → LangGraph graph → Anthropic SDK (Sonnet) + `LLMClientProtocol` (DeepSeek extract) + `WhatsAppClientProtocol.send_template` (Twilio)**, every send passing the D-11 compliance gate; n8n is thin transport outside the test boundary.
- **Owner-validation → `reprocess_record` → `promote_to_mar` → `push_attraction`** (frozen Pact shape; idempotent by `source_ref`).
- **FastAPI gate/queue/conversation endpoints** added here are consumed by the Phase 4 dashboard WhatsApp-gate/conversations views (built later) — design the response shape for that consumer but build no UI.
</code_context>

<specifics>
## Specific Ideas

- **"No message before the gates" is the headline invariant:** LGPD + BSP are a hard, code-enforced, offline-tested send-path gate (D-11) that *blocks* the first real send if any condition fails — not a documentation checkbox and not a late add. This is a PROJECT.md Key Decision (compliance mapped as hard send-path gates).
- **Parent-destino ordering invariant continues from Phase 2:** an atrativo with no parent destino in Mar is not ingested (D-03) — Mar's atrativo→destino reference must always resolve.
- **n8n holds zero logic** (CLAUDE.md hard constraint) — all conversation/opt-out/extraction logic is LangGraph code so the suite stays 100% offline.
- **Temporal stays deferred** — Phase 3 was the agreed re-evaluation point; the outcome is "Celery durable FSM now, revisit only on proven restart/timer pain" (D-01).
- **Apify / OTA / IG-X are best-effort, non-blocking** — they degrade signals, never fail a record (Meta-ToS read-only posture).

</specifics>

<deferred>
## Deferred Ideas

- **Dashboard WhatsApp-gate / conversations / funnels UI** — Phase 4 (this phase ships the FastAPI gate/queue/conversation endpoints only).
- **Meta Cloud API direct BSP migration** — cost-optimized end-state; ships behind the same `WhatsAppClientProtocol` once volume justifies (D-09). Launch on Twilio.
- **Temporal durable-workflow engine** — deferred again (D-01); re-open only on proven Celery FSM pain (lost restart progress / hard multi-day timers).
- **Active freshness-decay / re-score cron (§7.8, FRESH-01)** — v2; the re-score machinery ships, the scheduled decay does not.
- **OTA price cross-check integration (OTA-01)** — v2; the `OTAClientProtocol` may be exercised as a best-effort signal but no full integration.
- **Auto-tuning of §7.6 weights from steward/owner decisions (TUNE-01)** — v2.

None of these are in Phase 3 scope — recorded so they aren't lost.

</deferred>

---

*Phase: 3-Atrativos Lane (WhatsApp + Compliance)*
*Context gathered: 2026-06-12*
