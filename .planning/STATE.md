# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-11)

**Core value:** Only validated, reliability-scored canonical records ("Mar", ≥85%) reach the platform — the Nascente→Rio→Mar pipeline with §7.6 scoring and a DLQ gate is the single thing that must work.
**Current focus:** Phase 1 — Brave Core, Score Gate, Boundary & Contract

## Current Position

Phase: 1 of 4 (Brave Core, Score Gate, Boundary & Contract)
Plan: 0 of TBD in current phase
Status: Ready to plan
Last activity: 2026-06-11 — Roadmap created (4 phases, coarse granularity, all v1 requirements mapped)

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: -
- Trend: -

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: Pact ingestion contract (1b) folded into Phase 1 (Core) — frozen early because cheap to build / expensive to change, and both lanes + the external Laravel repo depend on its stability.
- [Roadmap]: Dashboard is its own final phase (4) rather than parallel tracks — coarse granularity; each panel depends on its backing FastAPI surface (DLQ/monitor from P1, gate/conversations from P3) existing.
- [Roadmap]: Destinos (P2) precedes Atrativos (P3) — an atrativo's DiscoveryAgent resolves a parent destino that must already be in Mar.
- [Roadmap]: Compliance (LGPD + BSP) mapped into Phase 3 as hard send-path gates that land before the first real WhatsApp message, not as a late checkbox.

### Pending Todos

[From .planning/todos/pending/ — ideas captured during sessions]

None yet.

### Blockers/Concerns

[Issues that affect future work]

- [Phase 1 research flag] Score-distribution calibration (50/85 boundaries + §7.6 weights) is MEDIUM-confidence — ship the histogram-simulation harness and treat boundaries as tunable; calibrate on the first state before national fan-out.
- [Phase 3 research flag] WhatsApp BSP pricing/policy/limits shift often — re-verify Twilio-vs-Meta-Cloud, template categorization, and rate caps at build time; the Celery-durable-executor + LangGraph multi-day FSM warrants a focused design pass.

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-06-11
Stopped at: Roadmap and STATE created; REQUIREMENTS traceability updated. Ready to plan Phase 1.
Resume file: None
