"""FastAPI application — norteia-brave API surface (D-21).

Includes all Phase 1 routers:
  - health   — GET /api/v1/health
  - metrics  — GET /api/v1/metrics
  - dlq      — GET/PATCH /api/v1/dlq
  - audit    — GET /api/v1/audit
  - webhook  — POST /webhook/error-report

Phase 3 additions:
  - atrativos_gate — WhatsApp gate endpoints (D-06, ATR-05, COMP-01/02)
"""

from fastapi import FastAPI

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
    description="Brave pipeline: Nascente → Rio → Mar with §7.6 score gate",
    version="1.0.0",
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
