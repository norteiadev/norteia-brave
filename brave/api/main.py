"""FastAPI application — norteia-brave API surface (D-21).

Includes all Phase 1 routers:
  - health   — GET /api/v1/health
  - metrics  — GET /api/v1/metrics
  - dlq      — GET/PATCH /api/v1/dlq
  - audit    — GET /api/v1/audit
  - webhook  — POST /webhook/error-report
"""

from fastapi import FastAPI

from brave.api.routers.audit import router as audit_router
from brave.api.routers.dlq import router as dlq_router
from brave.api.routers.health import router as health_router
from brave.api.routers.metrics import router as metrics_router
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
