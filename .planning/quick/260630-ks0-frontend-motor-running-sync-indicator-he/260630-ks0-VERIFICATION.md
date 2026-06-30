---
phase: quick-260630-ks0
verified: 2026-06-30
status: passed
score: 3/3 features + live e2e confirmed
---

# Quick 260630-ks0 — sync indicator + logs sidebar — Verification

## Offline
- Backend: log_buffer (Redis ring buffer per source, LPUSH+LTRIM, `_BLOCKED_FIELDS` secret-strip),
  structlog processor (FastAPI lifespan + Celery `worker_process_init`, gated off under
  BRAVE_USE_FAKEREDIS), `GET /api/v1/logs?source=&since=&limit=` (Bearer). **11 new tests; 702 total pass.**
- Frontend: sync indicator in PainelTopbar (reuses existing engine/status query, no 2nd poll),
  PainelLogs slide-over (clone of PainelDrawer, 2s poll while open, cursor incremental, level-colored),
  header `logs-icon-btn`, logs-api, MSW handler. **8 new tests; 295 total pass.**
- Checker warnings applied: W1 (source cached, no redis.get per log line), W2 (configure_structlog idempotency guard).

## Live end-to-end (the user's requirement) — real UF=PR TripAdvisor sweep

Brought up the full local stack: docker compose (postgres+pgvector + redis), `alembic upgrade head`,
uvicorn FastAPI :8000, Celery worker (`-Q celery,brave.sweep`), injected a real TA session, Next dev :3000.
Started a real sweep: `POST /api/v1/engine/start {depth:nascente, source:tripadvisor, ufs:["PR"]}` → 202.

Confirmed:
- **Logs flow:** `redis llen brave:logs:tripadvisor` grew; `GET /api/v1/logs?source=tripadvisor` returned
  real sync lines — `engine_started`, `llm_extract_ok` (DeepSeek extraction, token/cost fields),
  `engine_uf_dispatched` — each with `id`/`ts`/`level`/`event` and NO secrets.
- **Indicator (frontend, /browse):** topbar showed "Sincronizando TripAdvisor · UF 0/1" + "Motor · Ligado"
  while `engine/status.enabled=true`.
- **Logs sidebar (frontend, /browse):** clicking `logs-icon-btn` opened the right slide-over titled
  "Logs · TripAdvisor"; the monospace console rendered the live numbered lines (engine_started →
  llm_extract_ok ×N → engine_uf_dispatched), polling live for the active source. Screenshot captured.

**Routing note (ops, not a code bug):** API-dispatched `engine_sweep_run.delay()` has no explicit
queue → default `celery` queue. A worker must consume `celery` (not only `brave.sweep`) for
API-triggered sweeps to run; the beat-scheduled sweeps use `brave.sweep`. For local/all-in-one,
run the worker with `-Q celery,brave.sweep`.

Verdict: PASSED — both features work, logs are displayed in the frontend on a real sweep.
