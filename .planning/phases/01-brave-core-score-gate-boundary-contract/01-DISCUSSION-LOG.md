# Phase 1: Brave Core, Score Gate, Boundary & Contract - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-11
**Phase:** 1-Brave Core, Score Gate, Boundary & Contract
**Mode:** `--auto` (autonomous — recommended/research-backed option selected per area, no interactive prompts)
**Areas discussed:** Data-model & layer storage, Orchestration & durability, Dedup strategy, LLM client & structured output, Score config & calibration, Pact contract shape, Project layout & client boundary

---

## Data-model & layer storage

| Option | Description | Selected |
|--------|-------------|----------|
| Table-per-layer + routing sub_state in Rio, supersession versioning | Medallion 1:1, immutable raw decoupled from mutable lifecycle | ✓ |
| Single mega-table + state column | Fewer tables; couples raw store to lifecycle (anti-pattern) | |

**Choice:** Table-per-layer + sub_state + supersession. **Notes:** Per ARCHITECTURE.md; enables audit, idempotent push, safe error-report reopen.

---

## Orchestration & durability

| Option | Description | Selected |
|--------|-------------|----------|
| Celery+Redis+redbeat behind an interface | Sufficient for day-scale latency; contained future Temporal swap | ✓ |
| Temporal now | Durable workflows, heavier infra; unjustified this milestone | |

**Choice:** Celery+Redis+redbeat, interface-wrapped. **Notes:** Temporal trigger lives in Phase 3, not here.

---

## Dedup strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Territorial-key blocking (UF+município) → pgvector HNSW fuzzy, measured recall | Prevents homonym/parent-child false merges | ✓ |
| Pure embedding similarity | Risks merging Trancoso into Porto Seguro; approximate recall unmeasured | |

**Choice:** Two-stage, territorial-key-blocked, HNSW. **Notes:** Recall must be measured, not assumed (PITFALLS.md).

---

## LLM client & structured output

| Option | Description | Selected |
|--------|-------------|----------|
| instructor Mode.Tools + pinned slug/fallback + validate-or-quarantine | Native function calling; resilient to OpenRouter slug churn | ✓ |
| Naive JSON-mode, single slug | DeepSeek weak JSON adherence; slug churn breaks builds | |

**Choice:** instructor Mode.Tools, pinned+fallback, quarantine on malformed. **Notes:** log resolved provider; data_collection deny.

---

## Score config & calibration

| Option | Description | Selected |
|--------|-------------|----------|
| Config-driven weights/thresholds + score_version + histogram harness | Calibrable, versioned, DLQ-landfill measured up front | ✓ |
| Hard-coded weights | Can't tune without redeploy; drift between entities | |

**Choice:** pydantic-settings config + score_version + simulation harness. **Notes:** 50/85 boundaries tunable; calibrate on one state first (STATE.md blocker).

---

## Pact contract shape

| Option | Description | Selected |
|--------|-------------|----------|
| Consumer-driven Pact, freeze Mar push JSON keyed by source_ref + per-criterion provenance | Cheap-early/expensive-late; lanes + Laravel repo depend on stability | ✓ |
| Defer contract until lanes exist | Late freeze risks rework across two repos | |

**Choice:** Freeze early via Pact. **Notes:** idempotent no-op upsert on re-push; place_id cache only, canonical = first-party.

---

## Project layout & client boundary

| Option | Description | Selected |
|--------|-------------|----------|
| core/ (entity-agnostic) · lanes/ · clients/ faked boundary; lanes import core only | Single testability seam; no lane-to-lane coupling | ✓ |
| Flat package, externals called inline | Breaks the 100%-offline keyless CI constraint | |

**Choice:** Three boundaries, build clients/ seam first. **Notes:** psycopg3; Places client shaped for Places API (New).

---

## Claude's Discretion

- Exact DDL/columns, migration tool (Alembic assumed), FastAPI router layout, Celery queue topology, test-fixture structure — left to research/planning.

## Deferred Ideas

- Active freshness-decay cron (§7.8) — v2 FRESH-01
- Auto-tuning §7.6 weights — v2 TUNE-01
- OTA price cross-check — v2 OTA-01
- Temporal durable workflows — re-evaluate at Phase 3 outreach FSM
