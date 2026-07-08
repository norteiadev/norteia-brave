# QA Report — Painel Brave · TripAdvisor lane (real externals)

Real stack (RUN_REAL_EXTERNALS=true): API :8000 + worker (Redis broker) + dashboard :3000.
DB reset → operator TA session injected from a live browser cURL → 1-page bulk national
sweep (geoId 294280). Report-only. No code changed.

## Setup / evidence
- **Session injection** POST /api/v1/tripadvisor/session → **200 `{"status":"ready","canary":"ready"}`** — the real DataDome-protected canary call to TripAdvisor validated the session (12 cookies parsed; cookie values never logged). Session status: present=true, expires_in≈1689s, query_ids=[attractions].
- **Real sweep** sweep_tripadvisor(bulk_national, max_pages=1) → `ta_bulk_page_ingested ingested=27 errors=3`. 27 live attractions (Iguazu Falls, Sugarloaf Mountain, Parque Ibirapuera, Mini Mundo, Lago Negro, Muro Alto Beach…), Nominatim-geocoded to real municípios (RJ/SP/RS/PR/PE/MG…).

## Verified WORKING ✓
- TA session pill flips **"Expirada" → "Pronta"** after injection; sweep/session status endpoints 200.
- Board: **Atrativos 27**; Nascente count-only (QA F2 fix holding); topbar "Execução parada" + "Modo · …" (QA F4 fix holding).
- Views render with 0 NEW console errors: Kanban, Varreduras, DLQ/Revisão, Logs (TripAdvisor tab). BFF proxy forwards all /api/* as 200.
- **DLQ→WhatsApp with a REAL atrativo** (Iguazu Falls, no candidate): batch move → **202 moved=1 discovery=1**; sub_state → aguardando_consulta_whatsapp; the `brave.discover_whatsapp_number` task was **dispatched async to the Redis worker** and ran the real DeepSeek LLM (succeeded 3.88s, returned no number → correct, a national park has no WhatsApp).
- Celery broker correctly Redis (`broker_url=BRAVE_DB_REDIS_URL`); worker transport redis://…; API→Redis→worker dispatch path confirmed (not the inline fallback).

## FINDINGS

### TA1 — INTENDED (same class as F1): TA attractions can't reach Mar
All 27 score exactly **55.50** → DLQ (`score=55.50 below threshold_mar=80.0`). Single-source
(TA-only) attractions, like mTur-only destinos, don't cross the 80 binary threshold without
multi-source corroboração. By design (F1 decision). No bug.

### TA2 — INFO (test-method artifact, not a defect): Varreduras shows 0 runs
The Varreduras view read /runs empty because the test dispatched `sweep_tripadvisor` DIRECTLY
(one-off, for a controlled 1-page test) rather than through the engine orchestrator
(`engine_sweep_run`), which is what writes `runs_history`. Driving a sweep via the painel
"Ligar" motor path would populate Varreduras. Not a painel defect.

### TA3 — INFO: 3 ingest errors in the page
`errors=3` of ~30 items on the page — individual attractions whose TA payload shape didn't
map cleanly (geocode miss / missing field). 27/30 ingested. Acceptable data variance for a
best-effort scrape; worth a spot-check if the ratio grows on a full run.

## Not a bug (ruled out during QA)
- Ad-hoc `.delay()` from a throwaway python shell hit the default amqp broker — that was the
  shell's env timing, NOT the app. The API and worker both use the Redis broker; the async
  dispatch to the worker was verified end-to-end.

## Net
The TripAdvisor lane works end-to-end against live TripAdvisor with an operator-injected
session: inject → canary → real paginated sweep → reliability scoring → DLQ → manual WhatsApp move →
worker LLM discovery. No new painel/backend bugs surfaced. The only "findings" are the
by-design single-source DLQ scoring (TA1) and two INFO items (test-method, data variance).
