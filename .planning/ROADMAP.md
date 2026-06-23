### Phase 10: Engine Stage-Depth Selector (cost-gated collection)

**Goal**: An operator selects the pipeline depth — `Apenas nascente` | `Nascente → Rio` | `Nascente → Rio → Mar` — on the /processo engine control before starting the sweep, and the selection is required to enable the "Ligar motor" button. The engine honors the depth as a real cost boundary: `Apenas nascente` runs only the free Mtur seed (store_raw + §7.6 score; no `process_nascente_record`/Rio, no Desmembramento LLM, no atrativos Places — zero external cost), `Nascente → Rio` runs producers + validation up to Rio routing (mar-eligible/dlq/descarte) without promoting to Mar or dispatching WhatsApp, and `Nascente → Rio → Mar` runs the full pipeline including the idempotent norteia-api Mar push. A per-entity "nascente" StageBadge variant renders for records that exist only in Nascente. Net effect: an operator can populate the territorial base (destinos↔municípios) at zero external cost and ramp spend (Google Places 1000/mo free tier, LLM budget) deliberately — "rodar aos poucos".
**Requirements**: ENG-01, ENG-02, ENG-03, ENG-04, ENG-05, ENG-06, ENG-07
**Depends on:** Phase 9
**Plans:** 4 plans (all Wave 1, parallel)

Plans:
- [x] 10-01-PLAN.md — Engine depth state (Redis `brave:engine:depth`) + server-side required-depth validation on `/start` + depth on `/status` (ENG-01/02)
- [ ] 10-02-PLAN.md — Orchestrator + destinos-lane depth gating: nascente=Mtur-only/no-Rio/no-LLM/no-atrativos, nascente_rio=Rio-no-gate, nascente_rio_mar=full (ENG-03/04/05)
- [ ] 10-03-PLAN.md — Dashboard depth selector + disabled-until-chosen "Ligar motor" + depth in start body + MSW/Vitest (ENG-01/02 client)
- [ ] 10-04-PLAN.md — StageBadge "nascente" variant for Nascente-only records + Vitest (ENG-06)

**Locked decisions (see 10-CONTEXT.md):** stage map as cost checkpoints (Nascente = free ingest+score; Nascente→Rio = Places+LLM; Rio→Mar = WhatsApp+human); atrativos have no free source today, so `Apenas nascente` runs destinos (Mtur) only; keep table-per-layer model (no `incoming_attractions(status)` migration); WhatsApp stays single-channel. Out of scope (future phases): gov source for atrativos + moving Places to the Nascente→Rio edge (Phase B); structured hours/price (Phase C); free LLM model for Desmembramento; dedicated contacts table.
