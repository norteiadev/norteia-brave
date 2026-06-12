# Phase 2: Destinos Lane - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.
> Captured in `--auto` mode: every choice is the recommended (first) option, auto-selected.

**Date:** 2026-06-12
**Phase:** 2-Destinos Lane
**Areas discussed:** Mtur source shape, Municipality linkage, Cold-start criterion values, Desmembramento extraction, Steward→Mar promotion path, NotebookLM overlap, Mar push wiring

---

## Mtur source shape

| Option | Description | Selected |
|--------|-------------|----------|
| Bundled versioned static seed dataset | CSV/Parquet under `data/`, content-hashed, behind MturClientProtocol; matches "Mapa do Turismo" published-dataset reality | ✓ |
| Live Mtur REST fetch | Poll a real Mtur endpoint per UF | |

**User's choice (auto):** Bundled versioned static seed dataset.
**Notes:** Name `MturSeedIngest` + the real Mtur dataset being a periodic published file (categoria A–E) both favor a seed; offline-testable. Live fetch deferred.

---

## Municipality linkage

| Option | Description | Selected |
|--------|-------------|----------|
| Pass IBGE code through; norteia-api resolves | Use existing `RioRecord.municipio_id`; no local municipality table | ✓ |
| Build a municipality table in this repo | Mirror IBGE catalog locally | |

**User's choice (auto):** Pass IBGE code through.
**Notes:** Keeps the two-repo boundary clean — canonical municipality entity stays first-party on norteia-api.

---

## Cold-start criterion values

| Option | Description | Selected |
|--------|-------------|----------|
| Producer sets values; calibrate via simulation harness | origem 100/80/40 + source-derived completude/atualidade; corroboração=0 single-source; validate DLQ-band landing with Phase 1 harness | ✓ |
| Hardcode fixed criterion values per source | Static numbers in code | |

**User's choice (auto):** Producer sets values; calibrate via harness.
**Notes:** Defends against DLQ-landfill / descarte-black-hole; boundaries treated as tunable, calibrate on first state.

---

## Desmembramento extraction

| Option | Description | Selected |
|--------|-------------|----------|
| Per-município fan-out, Mode.Tools, validate-or-quarantine | One LLM call per Oferta Principal município; `DesmembramentoResult` schema; malformed → quarantine | ✓ |
| Single bulk extraction call | One call covering many municípios | |

**User's choice (auto):** Per-município fan-out with mandatory 2nd-layer validator.
**Notes:** Behind LLMClientProtocol → faked in default suite; origem=40 flag on every output.

---

## Steward→Mar promotion path

| Option | Description | Selected |
|--------|-------------|----------|
| Extend DLQ router with validate (single + batch) | New `PATCH /dlq/{id}/validate` sets validação humana=100 → reprocess → promote → push; batch endpoint filtered by UF | ✓ |
| Separate validation service/module | Stand up a new steward subsystem | |

**User's choice (auto):** Extend the existing DLQ router.
**Notes:** Reuses reprocess/promote_to_mar/write_audit + the Celery-or-sync fallback pattern already in dlq.py.

---

## NotebookLM overlap

| Option | Description | Selected |
|--------|-------------|----------|
| Ingest all at origem=80; let Rio dedup handle overlap | Territorial-key dedup merges Mtur matches and boosts corroboração | ✓ |
| Explicit "absent from Mtur" pre-filter | Query Mtur set before ingesting each report | |

**User's choice (auto):** Ingest all; lean on existing dedup.
**Notes:** Simpler; reuses Phase 1 D-07 dedup machinery.

---

## Mar push wiring

| Option | Description | Selected |
|--------|-------------|----------|
| Idempotent push_destination Celery task on promotion | Frozen Pact shape, no-op upsert by source_ref, sync fallback in tests | ✓ |
| Synchronous push inline in the request | Block the steward request on the HTTP push | |

**User's choice (auto):** Idempotent Celery task on promotion.
**Notes:** Mirrors the reprocess endpoint dispatch/fallback pattern; keeps the contract frozen.

---

## Claude's Discretion

- Exact `data/` seed file format/location.
- `DesmembramentoResult` schema field set.
- Quarantine destination for malformed LLM output (poison-quarantine pattern vs `routing='quarantine'`).
- Celery queue/task names, FastAPI request/response models, test-fixture layout.

## Deferred Ideas

- Dashboard DLQ batch-review UI — Phase 4.
- Atrativos producers — Phase 3.
- Live Mtur API fetch — out of scope unless a real categorized endpoint appears.
- Auto-tuning of §7.6 weights (TUNE-01) — v2.
- OTA price cross-check / freshness-decay cron — v2 (atrativos-side).
