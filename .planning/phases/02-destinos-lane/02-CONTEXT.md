# Phase 2: Destinos Lane - Context

**Gathered:** 2026-06-12
**Status:** Ready for planning

> Captured in `--auto` mode: gray areas auto-resolved with the research-backed recommended option for each. Every decision below is a default downstream agents may refine during research/planning — none is a hard user lock except where it restates a PROJECT.md Key Decision or a Phase 1 locked decision (D-xx carried forward).

<domain>
## Phase Boundary

Prove the **full Brave path on real destino data** through the already-built entity-agnostic core: three producers write to Nascente — **MturSeedIngest** (`source=mtur`, origem=100, linked to municipality), **NotebookLMIngest** (`source=notebooklm`, origem=80, for destinos absent from Mtur), and **DesmembramentoAgent** (DeepSeek, origem=40, "LLM-generated, pending validation") — records flow through Rio + §7.6 and land in **DLQ by default** (lacking human validation), a steward validates them **batch-by-state** (BA/RJ/SP/SC/CE/PE first) setting **validação humana=100**, which re-scores them into **Mar** and **pushes to `destinations`**. The origem=40 firewall guarantees no LLM-only destino reaches Mar unaided.

**In scope:** MturSeedIngest producer, NotebookLMIngest producer, DesmembramentoAgent (LLM + mandatory 2nd-layer validator + quarantine), the destino path through existing Rio/score/routing, steward DLQ validate (single + batch-by-state) → re-score → Mar promotion → push to `destinations`, and unit tests covering score + Desmembramento on Mar/DLQ/descarte boundaries. (Requirements DEST-01..05, TEST-02.)

**Out of scope (other phases):** anything atrativos (Discovery/Contact/Signal/WhatsApp — Phase 3), the dashboard UI that drives the DLQ queue (Phase 4 — this phase ships the FastAPI endpoints, not the Next.js views), real Mtur/NotebookLM/OpenRouter network calls in the default suite (opt-in flag only), and any change to the frozen Phase 1 core (score engine, routing, Mar service, Pact contract are reused, not modified — extend behind their existing seams).
</domain>

<decisions>
## Implementation Decisions

### Producers & data sources
- **D-01:** **MturClient supplies municipalities from a bundled, versioned static seed dataset** (CSV/Parquet under a repo `data/` dir, content-hashed for supersession), not a live REST API — the name `MturSeedIngest` and the "Mapa do Turismo Brasileiro" reality (periodic published dataset, categoria A–E per município) both point to a seed file. Behind the existing `MturClientProtocol`; a future "live fetch" stays a no-op/stub. Map Mtur categoria → Oferta Principal / Complementar / Apoio. Fully offline-testable.
- **D-02:** **NotebookLMIngest ingests all available reports at origem=80; overlap with Mtur is resolved by Rio dedup, not by an explicit "absent from Mtur" pre-filter.** A NotebookLM destino that matches an existing Mtur one collapses via the territorial-key-blocked dedup (D-07 carried forward) and *boosts corroboração* rather than creating a duplicate. Simpler and leans on already-built machinery.
- **D-03:** **DesmembramentoAgent fans out one LLM call per Oferta Principal município** (not one giant call), using `instructor` + `Mode.Tools` (D-09 carried forward) against a `DesmembramentoResult` Pydantic schema (list of `{nome, tipo ∈ distrito/praia/vila/…, posicionamento}`). Each valid destino → Nascente origem=40, flagged "LLM-generated, pending validation". Behind `LLMClientProtocol` → faked in tests (no real OpenRouter call in the default suite).

### Score inputs & cold-start calibration
- **D-04:** **Each producer populates the §7.6 criterion values in its Nascente payload** (the `*_value` fields the Rio normalizer already reads): `origem_value` = 100 / 80 / 40 by source; `completude_value` from field coverage of the source record; `corroboracao_value` = 0 at single-source ingest (raised when dedup merges a corroborating source per D-02); `atualidade_value` from the dataset/report publish date; `validacao_humana_value` = 0 until a steward acts.
- **D-05:** **Exact criterion values are a calibration task, not a hardcode.** Run the Phase 1 score-distribution **simulation harness** (`brave/core/score/simulation.py`, D-14 carried forward) on a representative sample of each producer's output to confirm records land in the **DLQ band (51–84.9), not descarte (≤50)** — the "DLQ landfill vs descarte black-hole" risk. Treat the 50/85 boundaries and per-source completude/atualidade mappings as tunable; calibrate on the first state before national fan-out.
- **D-06:** **The origem=40 firewall is a scoring consequence, verified by unit test, not a special-case branch.** With validação humana=0, an origem=40 record cannot reach 85 on the §7.6 weights without human validation — assert this on boundary cases (TEST-02) rather than adding firewall code.

### Steward validation → Mar promotion
- **D-07:** **Add a steward "validate" path to the existing DLQ router** (`brave/api/routers/dlq.py`, which currently has only reprocess + descarte): a new `PATCH /api/v1/dlq/{rio_id}/validate` sets `normalized.validacao_humana_value = 100`, calls the existing `reprocess_record` (re-score), and **if routing crosses to `mar`** promotes via the existing `promote_to_mar` (D-15 carried forward) and pushes. Writes an audit row (existing `write_audit`), actor=steward.
- **D-08:** **Batch-by-state is a thin endpoint over the same per-record validate** — e.g. `POST /api/v1/dlq/validate-batch` filtered by `uf` (BA/RJ/SP/SC/CE/PE first), iterating the single-record path. No new scoring logic; reuse `route_by_score` / `reprocess_record` / `promote_to_mar`.
- **D-09:** **Mar→`destinations` push fires on promotion via an idempotent Celery task** (`push_destination_task`) calling the existing `NorteiaApiClientProtocol.push_destination`, payload matching the **frozen Pact shape** (D-16 carried forward; re-push is a no-op upsert by `source_ref`). Mirror the existing reprocess endpoint's "dispatch task, fall back to synchronous in tests/dev without a broker" pattern.

### Municipality linkage & boundary
- **D-10:** **Carry the IBGE municipality code through the payload/canonical; do NOT build a municipality table in this repo.** The existing `RioRecord.municipio_id` field carries the IBGE code; norteia-api owns the canonical municipality table and resolves IBGE→`municipality_id` on push. Keeps the two-repo boundary clean (only Mar crosses; canonical entities stay first-party on the API side).

### Claude's Discretion
- Exact `data/` seed file format/location, the `DesmembramentoResult` schema field set, the quarantine destination for malformed LLM output (reuse the Phase 1 poison-quarantine pattern vs. a `routing='quarantine'` value — research to confirm), Celery queue/task names, FastAPI request/response models, and test-fixture layout are left to research/planning. Decisions above set direction, not signatures.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Primary plan & framework
- `docs/PLANO-BRAVE.md` — full plan; **§7.4 Desmembramento**, the Destinos producers, §7.6 score formula, §B.6 DeepSeek/instructor cautions, §C testability. Authoritative for this milestone.
- `docs/brave-visao-geral.pdf` — Brave overview (visual companion).
- Note: the §-numbers cite `docs/Norteia_MVP_Documentacao_Tecnica_v1.md` which lives in the **norteia-api** repo, not here — treat the §7.6 weights/thresholds and §7.4 desmembramento description quoted in PLANO-BRAVE.md as canonical for this repo.

### Phase 1 build (reuse, do not modify)
- `.planning/phases/01-brave-core-score-gate-boundary-contract/01-CONTEXT.md` — locked decisions D-01..D-21 (carried forward: dedup D-07, instructor/Mode.Tools D-09, score config D-12/D-13, Mar push + Pact D-15/D-16, package boundaries D-18).
- `brave/clients/base.py` — `MturClientProtocol`, `NotebookLMClientProtocol`, `LLMClientProtocol`, `NorteiaApiClientProtocol` (the seams this phase fills/uses).
- `brave/core/nascente/service.py` — `store_raw` (idempotent + supersession) — producers write here.
- `brave/core/rio/routing.py` — `process_nascente_record`, `route_by_score`, `reprocess_record` — destino path reuses these unchanged.
- `brave/core/mar/service.py` — `promote_to_mar` (idempotent, provenance) — promotion target.
- `brave/core/score/simulation.py` — calibration harness for D-05.
- `brave/api/routers/dlq.py` — existing reprocess/descarte; extend with validate (D-07/D-08).
- `brave/config/settings.py` — `ScoreConfig` (weights/thresholds), `LLMConfig` (DeepSeek slugs, cost guard).
- `tests/fakes/` — `fake_llm.py`, `fake_norteia_api.py` (extend; add Mtur/NotebookLM fakes).

### Research (this project)
- `.planning/research/PITFALLS.md` — DLQ-landfill, dedup false-merge, OpenRouter slug churn (all relevant to D-03/D-05).
- `.planning/research/STACK.md` — instructor/Mode.Tools, DeepSeek slug pinning.
- `.planning/research/ARCHITECTURE.md` — medallion mapping, package boundaries.

### Project planning
- `.planning/ROADMAP.md` §"Phase 2" — goal + 5 success criteria.
- `.planning/REQUIREMENTS.md` — DEST-01..05, TEST-02.
- `.planning/PROJECT.md` — Core Value, Key Decisions (LLM split, Destinos-precedes-Atrativos).
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Full Phase 1 core is reusable as-is**: `store_raw` (Nascente), `process_nascente_record` + `route_by_score` + `reprocess_record` (Rio/score/routing), `promote_to_mar` (Mar), `compute_score` + `ScoreConfig`, `simulation.py` harness, `write_audit`, cost guard + `llm_tracker`.
- **All four needed client Protocols already exist** in `brave/clients/base.py` (Mtur, NotebookLM, LLM, NorteiaApi) — this phase writes their **real implementations + fakes**, not new interfaces.
- **`tests/fakes/`** already has `fake_llm.py` and `fake_norteia_api.py` to extend.
- **DLQ router** already has the steward-action shape (reprocess/descarte + audit + Celery-or-sync fallback) to copy for `validate`.

### Established Patterns
- **Score inputs flow through `RioRecord.normalized` `*_value` fields** — producers set them in the Nascente payload; `process_nascente_record` copies them into `normalized`. No score-engine change needed.
- **Supersession versioning** (`superseded_by_id`) on every layer — re-score after validation appends a new Mar row, keeping the partial-unique active-`source_ref` index valid (already handled in `promote_to_mar`).
- **Steward endpoints dispatch a Celery task and fall back to synchronous** when no broker (tests/dev) — reuse for validate + push.
- **D-18 boundary:** lanes import core, never the reverse; new producer code lives under `brave/lanes/` implementing `LaneProtocol.produce(uf)` (`brave/lanes/base.py`).

### Integration Points
- **Producers → Nascente** via `store_raw` (source-tagged, content-hashed).
- **Steward validate → Rio re-score → Mar → norteia-api** `push_destination` (frozen Pact shape; idempotent by `source_ref`).
- **DesmembramentoAgent → `LLMClientProtocol.extract`** with a Pydantic schema (Mode.Tools) + 2nd-layer validate-or-quarantine.
- **FastAPI endpoints** added here are consumed by the Phase 4 dashboard DLQ queue (built later) — design the response shape for that consumer but do not build UI.
</code_context>

<specifics>
## Specific Ideas

- **origem=40 firewall** is the headline invariant: an LLM-generated destino must be *impossible* to auto-promote to Mar without a human — bake it as a tested scoring consequence (D-06), not a guard clause.
- **Batch-by-state ordering is fixed:** BA/RJ/SP/SC/CE/PE first (steward workflow + calibration both key off this order).
- **The DLQ must stay an actionable queue, not a landfill** — D-05's simulation-harness calibration is the explicit defense; confirm producers land destinos in 51–84.9, not ≤50.
</specifics>

<deferred>
## Deferred Ideas

- **Dashboard DLQ batch-review UI** — Phase 4 (this phase ships the FastAPI validate/batch endpoints only).
- **Atrativos producers (Discovery/Contact/Signal/WhatsApp)** — Phase 3; they depend on destinos being in Mar (this phase's output).
- **Live Mtur API fetch** — out of scope; seed dataset suffices (D-01). Revisit only if a real Mtur endpoint with categorized municipalities materializes.
- **Auto-tuning of §7.6 weights from steward decisions** (TUNE-01) — v2; this phase only *uses* the calibrable weights.
- **OTA price cross-check / freshness-decay cron** — v2 (OTA-01 / FRESH-01), atrativos-side.

None of these are in Phase 2 scope — recorded so they aren't lost.
</deferred>

---

*Phase: 2-Destinos Lane*
*Context gathered: 2026-06-12*
