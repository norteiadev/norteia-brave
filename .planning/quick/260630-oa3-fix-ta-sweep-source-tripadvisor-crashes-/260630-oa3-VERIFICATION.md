---
phase: quick-260630-oa3
verified: 2026-06-30
status: passed
score: fix verified live + offline; 2 separate downstream gaps surfaced
---

# Quick 260630-oa3 — TA sweep atrativos-only — Verification

## The bug (live-reproduced, pre-fix)
`POST /engine/start {source:tripadvisor, depth:nascente_rio}` → `sweep_tripadvisor` Step 1
(`TripAdvisorDestinosIngest.produce` → `fetch_destinations`) raised
`ValueError: No destinations queryId configured` (client.py:357) for every UF → retry 3× →
task died → atrativos never ran → 0 records across 27 UFs.

## Fix (merged 70feca2, commits ed22eb9/4a89112)
`sweep_tripadvisor` per-UF path: removed the TA-destinos Step 1 + import; relocated
`import asyncio as _asyncio`; dropped the `source=="tripadvisor"` filter on the `destino_rio_map`
query (now built from ALL destination RioRecords in the UF, primarily Mtur/origem=100, keyed by
IBGE code). atrativos docstring updated. `TripAdvisorDestinosIngest`/`fetch_destinations`/
`_DESTINATIONS_QID` left intact. **892 offline tests pass** incl. new
`TestSweepTripAdvisorPerUfDestinoBuild` (Mtur row → destino_rio_map keyed by IBGE).

## Live validation (real sweep, 2026-06-30)
Clean DB + broker purge + restarted worker (fixed code) + injected session.
`POST /engine/start {source:tripadvisor, depth:nascente, ufs:["PR"]}`:
```
sweep_tripadvisor received
POST .../data/graphql/ids 200 OK + ta_session_writeback   (atrativos fetch_attractions ran)
GET nominatim .../Igreja+de+Nossa+Senhora...Paraná         (atrativos producer geocoding)
Task brave.sweep_tripadvisor succeeded in 3.45s            (NO crash, NO destinos-QID ValueError)
```
- **0 occurrences of "No destinations queryId"** — the crash is gone.
- `sweep_tripadvisor` **succeeds** and **runs the atrativos producer** against real PR attractions.
- PASSED.

## Two SEPARATE downstream gaps surfaced by the live test (NOT oa3 — follow-ups)
1. **ftx geo-linkage is dormant in production.** `sweep_tripadvisor` constructs
   `TripAdvisorAtrativosIngest(...)` WITHOUT `ta_config` (pipeline.py ~:1104-1111). The ftx
   `fetch_attraction_geo` fallback is guarded by `if ibge_match is None and self._ta_config is not None`,
   so with `ta_config=None` it NEVER runs in the real sweep — atrativos fall back to Nominatim →
   `ta.atrativos.ibge_unmatched`. The geo-linkage validated in ftx is not active in the pipeline.
   Fix: pass `ta_config=ta_config` to the atrativos producer in sweep_tripadvisor.
2. **Mtur/default destinos seed does not persist.** A `source=default, lane=destinos` sweep_uf(PR)
   logged repeated `push_destination_permanent_failure error='RioRecord <id> not found'` and persisted
   0 nascente/rio rows — a transaction-visibility race (push_destination on worker B reads the RioRecord
   before sweep_uf's writing transaction on worker A commits; concurrency=2). Blocks Mtur destinos from
   reaching Rio, so atrativos can't link even with gap #1 fixed.

Both are independent of oa3 and recommended as the next two quick fixes.
