"""API package — FastAPI application, dependency injection, and routers.

Routers (Phase 1 stubs):
  - /webhook/error-report  — reopen Mar record into DLQ (CNTR-02)
  - /api/v1/metrics        — per-layer volume + queue health (OBS-03)
  - /api/v1/audit          — audit log read (OBS-04)
  - /api/v1/dlq            — list/mutate DLQ records (CORE-07, CORE-08)
  - /api/v1/health         — readiness check

Phase 1: module stubs (FastAPI app wired in Plan 1-02/1-03).
"""
