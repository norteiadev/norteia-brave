# Phase 5: Auto-Discovery Orchestration - Discussion Log

> **Audit trail only.** Not consumed by downstream agents — decisions live in CONTEXT.md.

**Date:** 2026-06-17
**Phase:** 5-Auto-Discovery Orchestration
**Mode:** `--auto` (all gray areas auto-resolved with the recommended option; no interactive prompts)
**Origin:** gap found during Phase 4 dogfooding — the "24/7 automatic fan-out" was only partially wired (verified in code: `brave.sweep_uf` is a phantom beat entry; the Atrativos FSM has 0 enqueue sites for `find_contacts`/`gather_signals` and stalls at `discovered`).
**Areas:** Destinos sweep composition, Atrativos FSM auto-advance mechanism, discovery→record handoff, ops trigger, idempotency/replay-safety, offline + gate-unchanged

---

## Destinos sweep composition (ORCH-01)
| Option | Selected |
|--------|----------|
| Implement `brave.sweep_uf` = MturSeed (idempotent) + Desmembramento (recurring LLM) (recommended) | ✓ |
| Rename the beat entry to an existing task | |
| Re-ingest only the static Mtur CSV (no recurring discovery) | |
**Auto-selected:** D-01/D-02. NotebookLM stays manual (deferred).

## Atrativos FSM auto-advance (ORCH-02)
| Option | Selected |
|--------|----------|
| Per-record self-enqueue, driven by `sub_state` queries (recommended) | ✓ |
| Rely on producer return values | (DiscoveryAgent returns None → impossible) |
| Single Celery `chain()` per record | |
**Auto-selected:** D-03. Self-healing across restarts via sub_state re-query.

## Idempotency / replay-safety (ORCH-02)
| Option | Selected |
|--------|----------|
| Reuse existing `advance_sub_state` guard (with_for_update + audit) (recommended) | ✓ |
| New locking/dedup layer | |
**Auto-selected:** D-04. The bug was missing enqueue sites, not missing guards.

## Ops trigger (ORCH-03)
| Option | Selected |
|--------|----------|
| CLI `sweep <uf>` + sync fallback (recommended); optional endpoint | ✓ |
| Endpoint only | |
**Auto-selected:** D-05.

## Offline + gate unchanged (ORCH-04)
| Option | Selected |
|--------|----------|
| 100% offline (existing fakes) + automation STOPS at the gate, no auto-send (recommended) | ✓ |
**Auto-selected:** D-06/D-07. Frozen Phase 3 gate/outreach.

## Claude's Discretion
Task names/queues, enqueue-in-discover vs thin advance task, CLI arg parsing, optional `/api/v1/sweep`, fixture layout, commit granularity.

## Deferred Ideas
NotebookLM scheduled producer · `/api/v1/sweep` endpoint · Temporal · freshness-decay cron · sweep-history dashboard panel.
