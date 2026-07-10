"""FastAPI application — norteia-brave API surface (D-21).

Includes all Phase 1 routers:
  - health   — GET /api/v1/health
  - metrics  — GET /api/v1/metrics
  - dlq      — GET/PATCH /api/v1/dlq
  - audit    — GET /api/v1/audit
  - webhook  — POST /webhook/error-report

Phase 3 additions:
  - atrativos_gate — WhatsApp gate endpoints (D-06, ATR-05, COMP-01/02)

Phase ks0 additions:
  - logs — GET /api/v1/logs (per-source log ring buffer tail, Bearer-gated)
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app):
    """Configure structlog on startup — wires the Redis log-buffer processor.

    When BRAVE_USE_FAKEREDIS=1 (offline tests) or when Redis is unreachable,
    falls back to console-only structlog (no buffer). The fallback is
    fail-silent so the API process always starts even if Redis is down.
    """
    try:
        if not os.environ.get("BRAVE_USE_FAKEREDIS"):
            from brave.api.deps import get_redis  # noqa: PLC0415
            _r = get_redis()
            from brave.observability.structlog_setup import configure_structlog  # noqa: PLC0415
            configure_structlog(redis=_r)
        else:
            from brave.observability.structlog_setup import configure_structlog  # noqa: PLC0415
            configure_structlog(redis=None)
    except Exception:
        try:
            from brave.observability.structlog_setup import configure_structlog  # noqa: PLC0415
            configure_structlog(redis=None)   # offline / test — console renderer only
        except Exception:
            pass
    yield

# Bind the configured Celery app as the process GLOBAL-DEFAULT app at startup. Without
# this, the API process never instantiates the Redis-configured Celery() as the *default*
# app, so every `@shared_task` (engine_sweep_run, discover_atrativo, …) resolves to Celery's stock
# DEFAULT app — whose broker is amqp://localhost:5672 (RabbitMQ). Real dispatch (`.delay()`
# under RUN_REAL_EXTERNALS) then fails with "[Errno 61] Connection refused" → engine/start
# 503 "broker unavailable", even though the configured broker is Redis and is up.
#
# `set_as_current=True` (Celery's default) only pushes the app onto a THREAD-LOCAL stack —
# which the import thread has, but FastAPI's sync request handlers run in a threadpool
# worker thread where that stack is empty, so `current_app` falls back to the (unset)
# global default → the amqp app. `set_default()` sets the GLOBAL default app, so shared
# tasks resolve to Redis from any thread. The call is connection-free (Kombu connects
# lazily), so it adds no broker I/O at startup.
from brave.tasks.celery_app import app as _celery_app

_celery_app.set_default()
from brave.api.routers.atrativos_gate import router as atrativos_gate_router
from brave.api.routers.audit import router as audit_router
from brave.api.routers.dashboard import router as dashboard_router
from brave.api.routers.dlq import router as dlq_router
from brave.api.routers.health import router as health_router
from brave.api.routers.metrics import router as metrics_router
from brave.api.routers.sweep import router as sweep_router
from brave.api.routers.webhook import router as webhook_router

app = FastAPI(
    title="norteia-brave",
    description="Brave pipeline: Nascente → Rio → Mar with reliability score gate",
    version="1.0.0",
    lifespan=lifespan,
)

# Include all Phase 1 routers
app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(dlq_router)
app.include_router(audit_router)
app.include_router(webhook_router)

# Phase 3: Atrativos WhatsApp gate endpoints (D-06, ATR-05, COMP-01/02)
app.include_router(atrativos_gate_router)

# Phase 4: Dashboard read-aggregation surface (D-01, DASH-01..05)
app.include_router(dashboard_router)

# Phase 5: On-demand ops trigger — POST /api/v1/sweep (ORCH-03, D-05; Bearer-guarded)
app.include_router(sweep_router)

# Phase 8: CMS CRUD + Workers observability
from brave.api.routers.cms import router as cms_router
from brave.api.routers.workers import router as workers_router

app.include_router(cms_router)
app.include_router(workers_router)

# Collection engine — operator start/stop of the full sweep
from brave.api.routers.engine import router as engine_router

app.include_router(engine_router)

# Atrativos operator API — audited stage transitions (UI-PAINEL-2)
from brave.api.routers.atrativos import router as atrativos_router

app.include_router(atrativos_router)

# Phase 12: TripAdvisor session injection seam (TA-10, TA-11)
from brave.api.routers import tripadvisor_session

app.include_router(tripadvisor_session.router)

# Phase 17.1: Duplicados — dedup candidate↔Mar pairs + resolve (UI-PAINEL-2)
from brave.api.routers.dedup import router as dedup_router

app.include_router(dedup_router)

# Phase 17.1: Varreduras — durable engine-run trail + reprocess (UI-PAINEL-2)
from brave.api.routers.runs import router as runs_router

app.include_router(runs_router)

# Phase ks0: per-source log ring buffer tail (Bearer-gated)
from brave.api.routers.logs import router as logs_router  # noqa: E402

app.include_router(logs_router)

# Phase D: operator-tunable runtime config (config_settings overlay) — GET (Bearer)
# effective snapshot + PATCH (steward) upsert with reliability validation + audit + cache-bust.
from brave.api.routers.config import router as config_router  # noqa: E402

app.include_router(config_router)
