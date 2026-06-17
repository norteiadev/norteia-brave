# Phase 5: Auto-Discovery Orchestration - Context

**Gathered:** 2026-06-17
**Status:** Ready for planning

> Captured in `--auto` mode: every gray area auto-resolved with the recommended (research-backed) default. Each decision is a default downstream research/planning may refine — none is a hard user lock except where it restates a PROJECT.md Key Decision or a Phase 1/2/3 locked decision carried forward. This phase is **gap closure**: the milestone's "24/7 automatic fan-out" promise was only partially wired (verified during Phase 4 dogfooding). Beat skeleton exists; the Destinos sweep task is a phantom and the Atrativos FSM never auto-advances past `discovered`.

<domain>
## Phase Boundary

Make the celery-redbeat 27-UF fan-out **actually drive records end-to-end**, up to (and only up to) the human WhatsApp gate.

**The two confirmed gaps (verified in code this session):**
1. **`brave.sweep_uf` does not exist.** `beat_schedule.py` registers `sweep-{uf}-daily @ 2 AM` pointing at task `brave.sweep_uf`, but no such task is implemented (only a Phase-1 stub comment + a docstring mention in `lanes/base.py`). The daily Destinos sweep is a dangling beat entry that would raise *unregistered task* if it fired.
2. **The Atrativos sub-state FSM never auto-advances.** Beat fires `discover_atrativo_task(uf)` (real, registered) which runs `DiscoveryAgent.produce(uf)` → records land at `sub_state=discovered`. But **nothing enqueues `find_contacts_task`** (0 `.delay`/`.apply_async` sites in the repo), and nothing enqueues `gather_signals_task`. Both tasks exist and are unit-tested but are orphaned — the FSM stalls at `discovered`.

**In scope (ORCH-01..04):**
- Implement the registered **`brave.sweep_uf(uf)`** task (Destinos sweep) so the beat entry resolves and runs a real recurring producer per UF: `DesmembramentoAgent.produce(uf)` (LLM lists sub-destinos inside Oferta-Principal municípios — the genuine recurring discovery) plus idempotent `MturSeedIngest.produce(uf)` seed re-ingest. (ORCH-01)
- **Auto-advance the Atrativos FSM**: `discover_atrativo_task` → enqueue `find_contacts_task` per discovered record → enqueue `gather_signals_task` → §7.6 score → borderline lands in `aguardando_consulta_whatsapp`. Idempotent, replay-safe, keyed on `sub_state`. (ORCH-02)
- **On-demand ops trigger** (CLI command, optionally an internal Bearer-guarded endpoint) to kick a UF sweep for destinos and/or atrativos without waiting for the 2/3 AM beat. (ORCH-03)
- Keep it **100%-offline-testable** (existing fakes + opt-in real flag) and the **human WhatsApp gate + outreach unchanged** — automation stops at the gate; no automatic send. (ORCH-04)

**Out of scope:** any change to the §7.6 score engine / routing / Mar / Pact contract (reused unchanged); the WhatsApp conversation graph / outreach / compliance gate (Phase 3, frozen — automation deliberately stops before it); the dashboard (Phase 4 surfaces what the sweep produces, no UI change needed beyond what exists); NotebookLM as a scheduled producer (stays a manual report ingest — only Mtur seed + Desmembramento run in the recurring sweep); new external integrations; Temporal (deferred — Celery FSM stays).
</domain>

<decisions>
## Implementation Decisions

### Destinos sweep task (ORCH-01)
- **D-01:** Implement **`@shared_task(name="brave.sweep_uf")` `sweep_uf(uf)`** in `brave/tasks/pipeline.py` so the existing `beat_schedule.py` entry resolves (do NOT rename the beat entry — fix the missing task). It opens a DB session (same `BRAVE_DB_URL` pattern as the other tasks), runs `MturSeedIngest(MturClient(), session, ScoreConfig()).produce(uf)` (idempotent seed re-ingest; `store_raw` dedups by source_ref/content_hash) **and** `DesmembramentoAgent(...).produce(uf)` (the real recurring LLM discovery), commits, and quarantines on poison (mirror `discover_atrativo_task`'s quarantine wrapper). Idempotent + replay-safe by construction (store_raw dedup). NotebookLM is NOT run here (manual ingest only).
- **D-02:** The Destinos sweep is **producer-only** — it writes Nascente + runs Rio (via the producers' existing `process_nascente_record` calls) and lands records in DLQ/Mar/descarte by §7.6. It does NOT auto-validate (human steward validation in the dashboard DLQ stays the gate, per the Phase 2 invariant). No new scoring branch.

### Atrativos FSM auto-advance (ORCH-02)
- **D-03:** **Per-record self-enqueue chaining, driven by `sub_state` queries — not by producer return values.** `DiscoveryAgent.produce(uf)` returns `None`, so the chaining cannot rely on returned ids. Instead: at the end of `discover_atrativo_task(uf)`, query `RioRecord` where `entity_type='attraction' AND uf=uf AND sub_state='discovered'` and enqueue `find_contacts_task.delay(rio_id)` for each. `find_contacts_task` advances `discovered → contacts_found` then enqueues `gather_signals_task.delay(rio_id)`. `gather_signals_task` advances `contacts_found → signals_gathered`, runs the §7.6 score/route; a borderline (<85%) attraction lands in `sub_state='aguardando_consulta_whatsapp'` and **STOPS** (human gate). Each task is small, restart-safe, and re-derivable from `sub_state` (a missed enqueue is recovered on the next sweep, which re-queries by sub_state).
- **D-04:** **Idempotency/replay-safety is enforced by the existing `advance_sub_state` guard** (`lanes/atrativos/state_machine.py`: `with_for_update` row lock + `if sub_state != expected_state: return False` + audit row, D-01/D-02/CR-04). Every advancing task MUST gate its work behind `advance_sub_state(expected, next)` returning `True`; a retry/replay where the record already moved is a no-op (returns `False`, task exits cleanly). No double-advance, no double-enqueue-with-effect (the next task's own guard absorbs a duplicate dispatch). Use `acks_late` semantics already configured on the app.

### Ops trigger (ORCH-03)
- **D-05:** Add a **CLI subcommand** `python -m brave.cli sweep <UF> [--lane destinos|atrativos|both]` (extends `brave/cli.py`, today only `run-fixture`). It dispatches `sweep_uf.delay(uf)` and/or `discover_atrativo_task.delay(uf)`; **falls back to synchronous execution when no Celery broker is reachable** (mirror the DLQ/gate endpoints' "dispatch task, except → run inline" pattern) so an operator can run a real sweep without a worker. An internal Bearer-guarded `POST /api/v1/sweep` endpoint is optional/nice-to-have (planner's discretion) — the CLI is the required surface.

### Offline testability + gate untouched (ORCH-04)
- **D-06:** **All new orchestration is 100%-offline-testable.** Reuse the existing fakes (`FakeMturClient`, `FakePlacesClient`, `FakeApifyClient`, fake LLM) and the opt-in real flag; CI stays keyless. Tests assert: `sweep_uf` ingests destinos (fake Mtur+Desmembramento) idempotently; the atrativos chain advances `discovered→contacts_found→signals_gathered→[gate]` and STOPS at `aguardando_consulta_whatsapp`; replay/duplicate dispatch is a no-op (the `advance_sub_state` guard); and **no outreach/WhatsApp send is triggered by the automatic chain** (only the human gate approve enqueues `outreach_task`, unchanged — `atrativos_gate.py:378`).
- **D-07:** **The WhatsApp gate + outreach + compliance gate are frozen** — automation terminates at `aguardando_consulta_whatsapp`. Do not auto-approve, do not auto-send. This preserves the Phase 3 PROJECT.md Key Decision (human gate before the first real message).

### Claude's Discretion
The exact Celery task names/queues for the chain steps, whether the per-record enqueue lives inside `discover_atrativo_task` or a thin separate `advance_discovered_atrativos(uf)` task, the precise CLI arg parsing, whether to also add the optional `/api/v1/sweep` endpoint, the test-fixture layout, and whether `sweep_uf` should batch-commit per município or per-UF are left to research/planning. Decisions above set direction, not signatures.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### The gap surface (what this phase fixes)
- `brave/tasks/beat_schedule.py` — the 27-UF fan-out; `sweep-{uf}-daily → brave.sweep_uf` (PHANTOM task, ORCH-01) + `sweep-atrativos-{uf}-daily → brave.discover_atrativo` (real). Do not rename entries — implement the missing task.
- `brave/tasks/pipeline.py` — `discover_atrativo_task` (line ~635; runs `agent.produce(uf)`, does NOT chain — ORCH-02 fix point), `find_contacts_task` (~732, orphaned), `gather_signals_task` (~819, orphaned), `outreach_task` (~1033, dispatched only by the gate), the quarantine wrapper pattern, the `BRAVE_DB_URL` session pattern. Add `sweep_uf` here.
- `brave/lanes/atrativos/state_machine.py` — `advance_sub_state(session, rio, expected_state, next_state, actor)` — the with_for_update + audit + guard helper every advancing task MUST use (D-04).
- `brave/lanes/atrativos/discovery_agent.py` — `DiscoveryAgent.produce(uf) -> None` (writes records at `sub_state='discovered'`; returns nothing → chain queries by sub_state, D-03).

### Producers the sweep composes
- `brave/lanes/destinos/mtur.py` — `MturSeedIngest(client, session, config).produce(uf)` (idempotent seed). `brave/clients/mtur.py` `MturClient` (reads bundled `data/mtur/municipios_mtur_*.csv`, no network).
- `brave/lanes/destinos/desmembramento.py` — `DesmembramentoAgent.produce(uf)` (DeepSeek lists sub-destinos; instructor Mode.Tools + validate-or-quarantine, origem=40 firewall). The real recurring Destinos discovery.
- `brave/lanes/base.py` — `LaneProtocol.produce(uf)` (the contract; "Called by the Celery sweep_uf task" — the docstring that anticipated this task).
- `brave/lanes/atrativos/contact_finder_agent.py`, `signal_agent.py` — what `find_contacts_task`/`gather_signals_task` drive.

### Ops surface + dispatch pattern to mirror
- `brave/cli.py` — only `run-fixture` today; add `sweep` (D-05).
- `brave/api/routers/dlq.py` / `brave/api/routers/atrativos_gate.py` — the "dispatch Celery `.delay`, `except` → run inline" sync-fallback pattern; `atrativos_gate.py:378` `outreach_task.delay` (the ONE human-gated dispatch — leave unchanged).
- `brave/tasks/celery_app.py` — `acks_late`, `task_reject_on_worker_lost`, redbeat config (the replay semantics D-04 relies on).
- `tests/fakes/` — FakeMtur/FakePlaces/FakeApify/fake LLM for the offline suite.

### Phase context (reuse, do not modify)
- `.planning/phases/03-atrativos-lane-whatsapp-compliance/03-CONTEXT.md` — D-01/D-02 sub_state FSM + audit, D-06 gate, the "no message before the gate" invariant (D-07 here continues it).
- `.planning/phases/02-destinos-lane/02-CONTEXT.md` — Mtur seed / Desmembramento / steward-validate invariants.
- `.planning/PROJECT.md` — "24/7 orchestration, fan-out by UF" (the promise this phase delivers); human gate Key Decision; Celery-not-Temporal.
- `.planning/ROADMAP.md` §"Phase 5" — goal + ORCH-01..04. `.planning/REQUIREMENTS.md` — ORCH-01..04.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`advance_sub_state` guard already exists** — the idempotency/concurrency primitive for the whole chain (D-04). No new locking code needed.
- **All producers expose `produce(uf)`** (LaneProtocol) — `MturSeedIngest`, `DesmembramentoAgent`, `DiscoveryAgent`. `sweep_uf` just composes them.
- **`find_contacts_task` / `gather_signals_task` already implemented + unit-tested** — this phase only WIRES them (enqueue sites), it doesn't build them.
- **Quarantine + BRAVE_DB_URL session + Celery-or-sync-fallback patterns** all exist in `pipeline.py` / the routers — copy them.
- **Fakes for every external** already in `tests/fakes/` — the offline suite extends, doesn't invent.

### Established Patterns
- **`sub_state` is the single source of truth** (Phase 3 D-02) — the chain queries/advances by it; never relies on producer return values (they return `None`).
- **`acks_late` + idempotent tasks** (Phase 1) — replay-safe is the default; `advance_sub_state` returning `False` on a moved record makes a duplicate a no-op.
- **Dispatch Celery, fall back to sync when no broker** — the DLQ/gate endpoints' pattern; reuse for the CLI sweep trigger.
- **Lanes import core, never reverse (D-18)** — `sweep_uf` lives in `brave/tasks/` and imports the lane producers; no lane-to-lane coupling.

### Integration Points
- **beat → `sweep_uf(uf)` → MturSeed+Desmembramento produce → Nascente → Rio → DLQ/Mar** (Destinos, ORCH-01).
- **beat → `discover_atrativo_task(uf)` → produce → query `sub_state=discovered` → `find_contacts_task(rio_id)` → `gather_signals_task(rio_id)` → score → `aguardando_consulta_whatsapp` STOP** (Atrativos, ORCH-02).
- **CLI `sweep <uf>` → `.delay` or inline** (ORCH-03).
- **Human gate approve → `outreach_task` (UNCHANGED)** — the automation boundary (D-07).
</code_context>

<specifics>
## Specific Ideas

- **Headline: automation drives to the gate, never through it.** The whole point is closing the discovery→score chain so records reach the human WhatsApp gate automatically — and stop. No auto-approve, no auto-send (D-07).
- **Fix the phantom, don't rename the schedule.** `brave.sweep_uf` is the contract the beat already expects — implement it (D-01), don't paper over the beat entry.
- **Chain by sub_state queries, not return values** — DiscoveryAgent returns `None`; the FSM is the source of truth and makes the chain self-healing across restarts (D-03).
- **Reuse `advance_sub_state`** — the replay-safety is already built; the bug was missing enqueue sites, not missing guards.

</specifics>

<deferred>
## Deferred Ideas

- **NotebookLM as a scheduled producer** — stays manual report ingest; only Mtur seed + Desmembramento run in the recurring sweep.
- **`/api/v1/sweep` internal endpoint** — optional; CLI is the required ops surface (planner may add it).
- **Temporal durable workflows** — deferred again; Celery FSM + `advance_sub_state` covers this phase.
- **Active freshness-decay / re-score cron (FRESH-01)** — v2; the sweep re-ingests but does not decay aging Mar records.
- **Sweep observability dashboard panel (sweep run history / per-UF last-run)** — nice-to-have; the existing monitor + audit log already capture transitions.

</deferred>

---

*Phase: 5-Auto-Discovery Orchestration*
*Context gathered: 2026-06-17*
